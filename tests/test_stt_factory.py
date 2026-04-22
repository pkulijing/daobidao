"""Tests for ``whisper_input.stt.create_stt``."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from whisper_input.stt import BaseSTT, create_stt


def test_create_qwen3_returns_qwen3_stt():
    # We don't actually load the model — just verify the factory wiring.
    with patch(
        "whisper_input.stt.qwen3.Qwen3ASRSTT"
    ) as mock_cls:
        create_stt("qwen3", {"variant": "1.7B"})
    mock_cls.assert_called_once_with(variant="1.7B")


def test_create_qwen3_default_variant():
    with patch(
        "whisper_input.stt.qwen3.Qwen3ASRSTT"
    ) as mock_cls:
        create_stt("qwen3", {})
    mock_cls.assert_called_once_with(variant="0.6B")


def test_create_stt_unknown_engine_raises():
    # Old "sensevoice" engine name must now be rejected — migration in
    # ConfigManager should have rewritten it, but if a user edits config
    # by hand we fall through to the catch-all.
    with pytest.raises(ValueError):
        create_stt("sensevoice", {})
    with pytest.raises(ValueError):
        create_stt("unknown", {})


def test_base_stt_is_abstract():
    with pytest.raises(TypeError):
        BaseSTT()  # type: ignore[abstract]
