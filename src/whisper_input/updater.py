"""PyPI 更新检查与触发 —— 查询最新版本、探测安装方式、跑 upgrade 子进程。

所有网络 / 子进程调用都是同步的，外面由 UpdateChecker 包后台线程（保持与
整个项目 threading + 阻塞 IO 的一致；future work 见 BACKLOG 的 asyncio 迁移）。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

from whisper_input.logger import get_logger
from whisper_input.version import __version__

logger = get_logger(__name__)

PYPI_JSON_URL = "https://pypi.org/pypi/whisper-input/json"
PACKAGE_NAME = "whisper-input"

# install method 常量
UV_TOOL = "uv-tool"
PIPX = "pipx"
PIP = "pip"
DEV = "dev"


def detect_install_method() -> str:
    """返回 "uv-tool" | "pipx" | "pip" | "dev"。"""
    if __version__ == "dev":
        return DEV
    prefix = sys.prefix.replace("\\", "/")
    if "/uv/tools/whisper-input" in prefix:
        return UV_TOOL
    if "/pipx/venvs/whisper-input" in prefix:
        return PIPX
    return PIP


def fetch_latest_version(timeout: float = 3.0) -> str | None:
    """同步查 PyPI。失败返回 None，不抛异常。"""
    try:
        req = urllib.request.Request(
            PYPI_JSON_URL,
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                return None
            data = json.loads(resp.read().decode("utf-8"))
        v = data.get("info", {}).get("version")
        return v if isinstance(v, str) and v else None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        logger.debug("pypi_fetch_failed", error=str(e))
        return None


def is_newer(latest: str, current: str) -> bool:
    """packaging.Version 比较；任一不合法返回 False。"""
    try:
        from packaging.version import InvalidVersion, Version

        return Version(latest) > Version(current)
    except (InvalidVersion, ImportError, TypeError):
        return False


def get_upgrade_command(install_method: str) -> list[str] | None:
    """返回 subprocess 可执行的 argv。dev 模式返回 None。"""
    if install_method == DEV:
        return None
    if install_method == UV_TOOL:
        uv = shutil.which("uv")
        if uv is None:
            return None
        return [uv, "tool", "upgrade", PACKAGE_NAME]
    if install_method == PIPX:
        pipx = shutil.which("pipx")
        if pipx is None:
            return None
        return [pipx, "upgrade", PACKAGE_NAME]
    # pip 回退：用当前解释器 -m pip，不依赖 PATH
    return [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        PACKAGE_NAME,
    ]


def apply_upgrade(
    install_method: str, timeout: float = 180.0
) -> tuple[bool, str]:
    """执行 upgrade 命令。返回 (ok, combined_output)。"""
    cmd = get_upgrade_command(install_method)
    if cmd is None:
        return False, (
            f"无法确定升级命令（install_method={install_method}）。"
            "请在终端手动运行 `uv tool upgrade whisper-input` "
            "或 `pipx upgrade whisper-input`。"
        )
    logger.info("upgrade_start", cmd=cmd)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        logger.warning("upgrade_timeout", cmd=cmd, timeout=timeout)
        return False, f"升级超时（>{timeout:.0f}s），已中止。"
    except (OSError, FileNotFoundError) as e:
        logger.warning("upgrade_oserror", cmd=cmd, error=str(e))
        return False, f"无法启动升级命令: {e}"
    output = (proc.stdout or "") + (proc.stderr or "")
    ok = proc.returncode == 0
    logger.info(
        "upgrade_done",
        returncode=proc.returncode,
        ok=ok,
    )
    return ok, output.strip() or (
        "（升级命令无输出）" if ok else f"退出码 {proc.returncode}"
    )


class UpdateChecker:
    """缓存最近一次 PyPI 检查结果，后台线程刷新。"""

    def __init__(self, current_version: str | None = None):
        self._current = current_version or __version__
        self._install_method = detect_install_method()
        self._lock = threading.Lock()
        self._latest: str | None = None
        self._checked_at: float | None = None
        self._error: str | None = None
        self._checking: bool = False

    @property
    def snapshot(self) -> dict:
        with self._lock:
            has_update = (
                self._latest is not None
                and self._install_method != DEV
                and is_newer(self._latest, self._current)
            )
            return {
                "current": self._current,
                "latest": self._latest,
                "has_update": has_update,
                "install_method": self._install_method,
                "checking": self._checking,
                "checked_at": self._checked_at,
                "error": self._error,
            }

    def trigger_async(self) -> bool:
        """启动后台检查。dev 模式或已在检查中则跳过，返回是否真的启动了。"""
        if self._install_method == DEV:
            return False
        with self._lock:
            if self._checking:
                return False
            self._checking = True
            self._error = None
        t = threading.Thread(
            target=self._run_check,
            daemon=True,
            name="update-checker",
        )
        t.start()
        return True

    def _run_check(self) -> None:
        latest = fetch_latest_version()
        with self._lock:
            self._latest = latest
            self._checked_at = time.time()
            self._checking = False
            if latest is None:
                self._error = "无法获取最新版本（网络或 PyPI 异常）"
            else:
                self._error = None
        logger.info(
            "update_check_done",
            current=self._current,
            latest=latest,
        )
