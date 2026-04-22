"""Download Qwen3-ASR weights + tokenizer from ModelScope.

ModelScope is the sole distribution source. Repo
``zengshuishui/Qwen3-ASR-onnx`` hosts both variants side-by-side:

    model_0.6B/{conv_frontend,encoder.int8,decoder.int8}.onnx
    model_1.7B/{conv_frontend,encoder.int8,decoder.int8}.onnx
    tokenizer/{vocab.json,merges.txt,tokenizer_config.json, ...}

``allow_patterns`` restricts the download to the requested variant so users
don't pull 3+ GB when they only want 0.6B.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

REPO_ID = "zengshuishui/Qwen3-ASR-onnx"
Variant = Literal["0.6B", "1.7B"]
VALID_VARIANTS: tuple[Variant, ...] = ("0.6B", "1.7B")


def download_qwen3_asr(variant: str) -> Path:
    """Fetch the ONNX bundle + tokenizer for the given variant.

    Parameters
    ----------
    variant:
        ``"0.6B"`` (default choice, ~990 MiB) or ``"1.7B"`` (~2.4 GiB).

    Returns
    -------
    Path
        Root directory under ModelScope's cache; callers pass
        ``root / f"model_{variant}"`` to the ONNX runner and
        ``root / "tokenizer"`` to the tokenizer.
    """
    if variant not in VALID_VARIANTS:
        raise ValueError(
            f"unknown variant {variant!r}; expected one of {VALID_VARIANTS}"
        )

    # Lazy import so `--help`, tests that mock the downloader, and the
    # module-import path don't pay the modelscope cost.
    from modelscope import snapshot_download

    root = snapshot_download(
        REPO_ID,
        allow_patterns=[
            f"model_{variant}/conv_frontend.onnx",
            f"model_{variant}/encoder.int8.onnx",
            f"model_{variant}/decoder.int8.onnx",
            "tokenizer/*",
        ],
    )
    return Path(root)
