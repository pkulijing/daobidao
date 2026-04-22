"""Tests for ``whisper_input.stt.qwen3._postprocess.parse_asr_output``."""

from __future__ import annotations

import pytest

from whisper_input.stt.qwen3._postprocess import parse_asr_output


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Normal cases
        ("<asr_text>hello world", "hello world"),
        ("<asr_text>今天要部署 kubernetes 集群", "今天要部署 kubernetes 集群"),
        # Trailing special tokens that slip through
        ("<asr_text>hello<|im_end|>", "hello"),
        ("<asr_text>hello<|endoftext|>", "hello"),
        ("<asr_text>hello<|im_end|><|endoftext|>", "hello"),
        # No marker at all (graceful degradation)
        ("plain text without marker", "plain text without marker"),
        ("no marker<|im_end|>", "no marker"),
        # Whitespace trimming
        ("<asr_text>  spaced  ", "spaced"),
        ("<asr_text>\n\nleading newlines", "leading newlines"),
        # Empty / edge cases
        ("", ""),
        ("   ", ""),
        ("<asr_text>", ""),
        ("<asr_text>   ", ""),
        # Repeated marker — keep only text after the LAST marker
        ("<asr_text>first<asr_text>second", "second"),
        # Mid-string special tokens should also be scrubbed
        ("<asr_text>part1<|im_start|>part2<|im_end|>", "part1part2"),
    ],
)
def test_parse_asr_output(raw: str, expected: str) -> None:
    assert parse_asr_output(raw) == expected
