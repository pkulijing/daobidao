"""Tests for ``whisper_input.stt.qwen3._prompt.build_prompt``."""

from __future__ import annotations

import pytest

from whisper_input.stt.qwen3._prompt import (
    AUDIO_END,
    AUDIO_PAD,
    AUDIO_START,
    IM_END,
    IM_START,
    build_prompt,
)


def test_build_prompt_rejects_zero_audio_tokens():
    with pytest.raises(ValueError, match="audio_token_count"):
        build_prompt(0)


def test_build_prompt_rejects_negative():
    with pytest.raises(ValueError, match="audio_token_count"):
        build_prompt(-3)


def test_build_prompt_contains_required_markers():
    prompt = build_prompt(5)
    for marker in (
        f"{IM_START}system\n",
        f"{IM_END}\n",
        f"{IM_START}user\n",
        AUDIO_START,
        AUDIO_END,
        f"{IM_START}assistant\n",
    ):
        assert marker in prompt, f"missing marker: {marker!r}"


def test_build_prompt_pad_count_is_exact():
    n = 17
    prompt = build_prompt(n)
    assert prompt.count(AUDIO_PAD) == n


def test_build_prompt_ordering_is_system_user_assistant():
    prompt = build_prompt(1)
    sys_pos = prompt.index(f"{IM_START}system")
    user_pos = prompt.index(f"{IM_START}user")
    assistant_pos = prompt.index(f"{IM_START}assistant")
    assert sys_pos < user_pos < assistant_pos


def test_build_prompt_audio_section_between_markers():
    prompt = build_prompt(3)
    start_pos = prompt.index(AUDIO_START)
    end_pos = prompt.index(AUDIO_END)
    assert start_pos < end_pos
    between = prompt[start_pos + len(AUDIO_START) : end_pos]
    assert between == AUDIO_PAD * 3


def test_build_prompt_empty_system_by_default():
    prompt = build_prompt(2)
    # system open→close is immediate for empty system prompt
    assert f"{IM_START}system\n{IM_END}" in prompt


def test_build_prompt_with_system_prompt():
    prompt = build_prompt(2, system_prompt="关键词: kubernetes, TypeScript")
    assert "关键词: kubernetes, TypeScript" in prompt
    # Must still end with assistant opening
    assert prompt.endswith(f"{IM_START}assistant\n")


def test_build_prompt_assistant_opening_has_no_content():
    # No <asr_text> or trailing text injected — that's the model's job
    prompt = build_prompt(1)
    assert prompt.endswith(f"{IM_START}assistant\n")
    # and does NOT contain pre-filled asr_text marker
    assert "<asr_text>" not in prompt


def test_build_prompt_with_large_audio_token_count():
    # Sanity for a 30s audio (~1500 audio tokens typical after encoder)
    prompt = build_prompt(1500)
    assert prompt.count(AUDIO_PAD) == 1500
