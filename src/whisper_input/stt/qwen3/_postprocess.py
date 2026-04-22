"""Clean the decoded string produced by Qwen3-ASR's decoder.

The model is trained to emit:

    <asr_text>{transcript}<|im_end|>

but the tokenizer's ``decode(skip_special_tokens=True)`` keeps the literal
``<asr_text>`` (non-special added token) and drops ``<|im_end|>``. Occasional
runs still trail ``<|endoftext|>`` or leave a stray ``<asr_text>`` at the
start. This module extracts the transcript payload and trims whitespace.
"""

from __future__ import annotations

import re

_ASR_START = "<asr_text>"
_SPECIAL_TAG_RE = re.compile(r"<\|[a-zA-Z0-9_]+\|>")


def parse_asr_output(raw_text: str) -> str:
    """Extract the transcript from a decoded ASR output string.

    Accepts variations:
    - ``<asr_text>hello world`` → ``"hello world"``
    - ``<asr_text>hello<|im_end|>`` → ``"hello"``
    - ``hello<|endoftext|>`` (no marker) → ``"hello"``
    - ``"  hello  "`` → ``"hello"``

    Never raises; returns ``""`` for empty / whitespace-only input.
    """
    if not raw_text:
        return ""

    text = raw_text
    # Keep only the substring after the last <asr_text> marker (if present).
    marker_idx = text.rfind(_ASR_START)
    if marker_idx >= 0:
        text = text[marker_idx + len(_ASR_START) :]

    # Strip any leftover <|...|> chat / special tokens that the tokenizer
    # didn't filter (shouldn't happen under skip_special_tokens=True, but we
    # defend against future tokenizer changes).
    text = _SPECIAL_TAG_RE.sub("", text)

    # Also scrub any stray <asr_text> occurrences left in the middle.
    text = text.replace(_ASR_START, "")

    return text.strip()
