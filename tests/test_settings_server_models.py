"""测试设置页面 /api/models/* 端点 — 36 轮"模型管理与可视化下载"。

启动一个真实 SettingsServer 在 127.0.0.1 + 临时端口上,通过 fake
DownloadManager(MagicMock 风格)注入预设 state,验证端点正确转发。
"""

from __future__ import annotations

import http.client
import json
import socket
from typing import Any
from unittest.mock import MagicMock

import pytest

from daobidao import settings_server as ss
from daobidao.config_manager import ConfigManager


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _request(method: str, host: str, port: int, path: str, body=None):
    conn = http.client.HTTPConnection(host, port, timeout=5)
    headers = {}
    payload = None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, body=payload, headers=headers)
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, data


@pytest.fixture
def autostart_state(monkeypatch):
    state = {"enabled": False}
    monkeypatch.setattr(ss, "_is_autostart_enabled", lambda: state["enabled"])
    monkeypatch.setattr(
        ss, "_set_autostart", lambda enabled: state.update(enabled=enabled)
    )
    return state


def _make_state(
    *,
    downloaded: bool = True,
    downloading: bool = False,
    received_bytes: int = 0,
    total_bytes: int = 0,
    speed_bps: float = 0.0,
    eta_seconds: int = 0,
    error: str | None = None,
    cancelled: bool = False,
) -> dict[str, Any]:
    return {
        "downloaded": downloaded,
        "downloading": downloading,
        "received_bytes": received_bytes,
        "total_bytes": total_bytes,
        "speed_bps": speed_bps,
        "eta_seconds": eta_seconds,
        "error": error,
        "cancelled": cancelled,
    }


@pytest.fixture
def server_with_manager(tmp_path, autostart_state, monkeypatch):
    """启动 SettingsServer 并注入 fake DownloadManager。"""
    monkeypatch.setattr(ss.os, "kill", lambda *a, **kw: None)
    monkeypatch.setattr(ss.os, "execv", lambda *a, **kw: None)

    cfg_path = tmp_path / "config.yaml"
    config_mgr = ConfigManager(config_path=str(cfg_path))
    port = _free_port()

    fake_mgr = MagicMock()
    fake_mgr.variant_states.return_value = {
        "0.6B": _make_state(downloaded=True),
        "1.7B": _make_state(downloaded=False),
    }
    fake_mgr.start.return_value = (True, None)
    fake_mgr.cancel.return_value = True

    server = ss.SettingsServer(config_mgr, port=port, download_manager=fake_mgr)
    server.start()
    try:
        yield ("127.0.0.1", port, fake_mgr)
    finally:
        server.stop()


@pytest.fixture
def server_without_manager(tmp_path, autostart_state, monkeypatch):
    """不传 download_manager 的 server,验证 stub 行为。"""
    monkeypatch.setattr(ss.os, "kill", lambda *a, **kw: None)
    monkeypatch.setattr(ss.os, "execv", lambda *a, **kw: None)

    cfg_path = tmp_path / "config.yaml"
    config_mgr = ConfigManager(config_path=str(cfg_path))
    port = _free_port()
    server = ss.SettingsServer(config_mgr, port=port)
    server.start()
    try:
        yield ("127.0.0.1", port)
    finally:
        server.stop()


# ----------------------------------------------------------------------------
# GET /api/models/status
# ----------------------------------------------------------------------------


def test_get_models_status_forwards_manager_state(server_with_manager):
    host, port, fake_mgr = server_with_manager
    status, data = _request("GET", host, port, "/api/models/status")
    assert status == 200
    payload = json.loads(data)
    assert "variants" in payload
    assert payload["variants"]["0.6B"]["downloaded"] is True
    assert payload["variants"]["1.7B"]["downloaded"] is False
    fake_mgr.variant_states.assert_called()


def test_get_models_status_default_when_no_manager(server_without_manager):
    """无 download_manager 时返回 stub:两个 variant 都标已下载,
    保持 UI 不需要区分两种部署。"""
    host, port = server_without_manager
    status, data = _request("GET", host, port, "/api/models/status")
    assert status == 200
    payload = json.loads(data)
    assert payload["variants"]["0.6B"]["downloaded"] is True
    assert payload["variants"]["1.7B"]["downloaded"] is True
    assert payload["variants"]["0.6B"]["downloading"] is False
    assert payload["variants"]["1.7B"]["downloading"] is False


# ----------------------------------------------------------------------------
# POST /api/models/download
# ----------------------------------------------------------------------------


def test_post_models_download_invokes_start(server_with_manager):
    host, port, fake_mgr = server_with_manager
    status, data = _request(
        "POST", host, port, "/api/models/download", body={"variant": "1.7B"}
    )
    assert status == 200
    payload = json.loads(data)
    assert payload["ok"] is True
    fake_mgr.start.assert_called_once_with("1.7B")


def test_post_models_download_busy_returns_reason(server_with_manager):
    host, port, fake_mgr = server_with_manager
    fake_mgr.start.return_value = (False, "busy")
    status, data = _request(
        "POST", host, port, "/api/models/download", body={"variant": "1.7B"}
    )
    assert status == 200
    payload = json.loads(data)
    assert payload["ok"] is False
    assert payload["reason"] == "busy"


def test_post_models_download_invalid_variant(server_with_manager):
    host, port, fake_mgr = server_with_manager
    fake_mgr.start.return_value = (False, "invalid_variant")
    status, data = _request(
        "POST", host, port, "/api/models/download", body={"variant": "99B"}
    )
    assert status == 200
    payload = json.loads(data)
    assert payload["ok"] is False
    assert payload["reason"] == "invalid_variant"


def test_post_models_download_missing_variant_field(server_with_manager):
    host, port, _ = server_with_manager
    status, _data = _request(
        "POST", host, port, "/api/models/download", body={}
    )
    assert status == 400


def test_post_models_download_no_manager_silent(server_without_manager):
    """没 download_manager 时 POST 应该返 503 (有意义的拒绝)。"""
    host, port = server_without_manager
    status, _data = _request(
        "POST", host, port, "/api/models/download", body={"variant": "1.7B"}
    )
    assert status == 503


# ----------------------------------------------------------------------------
# POST /api/models/cancel
# ----------------------------------------------------------------------------


def test_post_models_cancel_invokes_cancel(server_with_manager):
    host, port, fake_mgr = server_with_manager
    fake_mgr.cancel.return_value = True
    status, data = _request(
        "POST", host, port, "/api/models/cancel", body={"variant": "1.7B"}
    )
    assert status == 200
    payload = json.loads(data)
    assert payload["ok"] is True
    fake_mgr.cancel.assert_called_once_with("1.7B")


def test_post_models_cancel_returns_false_when_idle(server_with_manager):
    host, port, fake_mgr = server_with_manager
    fake_mgr.cancel.return_value = False
    status, data = _request(
        "POST", host, port, "/api/models/cancel", body={"variant": "1.7B"}
    )
    assert status == 200
    payload = json.loads(data)
    assert payload["ok"] is False
