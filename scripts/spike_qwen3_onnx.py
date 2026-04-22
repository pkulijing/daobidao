"""
Spike: enumerate Qwen3-ASR ONNX graph interfaces and validate a dry-run
forward pass, so the PLAN can be written against real schema rather
than inference from upstream Python code.

Usage:
    uv run python scripts/spike_qwen3_onnx.py /tmp/qwen3-asr-spike

This script is one-shot tooling, not part of the shipping package.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import onnxruntime as ort


def describe_session(label: str, sess: ort.InferenceSession) -> None:
    print(f"\n=== {label} ===")
    print("  inputs:")
    for i in sess.get_inputs():
        print(f"    - name={i.name!r:35s} shape={i.shape}  dtype={i.type}")
    print("  outputs:")
    for o in sess.get_outputs():
        print(f"    - name={o.name!r:35s} shape={o.shape}  dtype={o.type}")


def count_kv_cache_layers(sess: ort.InferenceSession) -> tuple[int, list]:
    keys = sorted(
        (i for i in sess.get_inputs() if i.name.startswith("cache_key_")),
        key=lambda x: int(x.name.split("_")[-1]),
    )
    return len(keys), keys


def try_decoder_dry_run(
    dec: ort.InferenceSession,
    n_layers: int,
    kv_shape_template: list,
    audio_feat_dim: int,
) -> None:
    """Feed dummy tensors through decoder once to confirm static shapes."""
    print("\n=== decoder dry-run ===")

    kv_sample = kv_shape_template
    max_total_len = kv_sample[1] if isinstance(kv_sample[1], int) else 2048
    kv_heads = kv_sample[2] if isinstance(kv_sample[2], int) else 8
    head_dim = kv_sample[3] if isinstance(kv_sample[3], int) else 64
    print(
        f"  derived: max_total_len={max_total_len}, "
        f"kv_heads={kv_heads}, head_dim={head_dim}, layers={n_layers}"
    )

    batch = 1
    prompt_len = 16
    audio_tokens = 20

    input_ids = np.zeros((batch, prompt_len), dtype=np.int64)
    audio_features = np.zeros(
        (batch, audio_tokens, audio_feat_dim), dtype=np.float32
    )
    attention_mask = np.ones((batch, prompt_len), dtype=np.int64)
    cache_position = np.arange(0, prompt_len, dtype=np.int64)

    feed = {
        "input_ids": input_ids,
        "audio_features": audio_features,
        "attention_mask": attention_mask,
        "cache_position": cache_position,
    }
    for i in range(n_layers):
        feed[f"cache_key_{i}"] = np.zeros(
            (batch, max_total_len, kv_heads, head_dim), dtype=np.float32
        )
        feed[f"cache_value_{i}"] = np.zeros(
            (batch, max_total_len, kv_heads, head_dim), dtype=np.float32
        )

    out_names = [o.name for o in dec.get_outputs()]
    outs = dec.run(out_names, feed)
    out_map = dict(zip(out_names, outs, strict=True))

    print(f"  logits shape={out_map['logits'].shape}")
    kd0 = out_map.get("key_delta_0")
    vd0 = out_map.get("value_delta_0")
    if kd0 is not None:
        print(f"  key_delta_0 shape={kd0.shape}  dtype={kd0.dtype}")
    if vd0 is not None:
        print(f"  value_delta_0 shape={vd0.shape}  dtype={vd0.dtype}")

    print("\n  second step (cache_position=prompt_len..prompt_len+1):")
    new_input = np.zeros((batch, 1), dtype=np.int64)
    new_mask = np.ones((batch, 1), dtype=np.int64)
    new_cpos = np.array([prompt_len], dtype=np.int64)
    feed2 = dict(feed)
    feed2["input_ids"] = new_input
    feed2["attention_mask"] = new_mask
    feed2["cache_position"] = new_cpos
    outs2 = dec.run(out_names, feed2)
    print(f"  2nd logits shape={outs2[out_names.index('logits')].shape}")


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/qwen3-asr-spike")
    model_dir = root / "model_0.6B"

    conv = ort.InferenceSession(
        str(model_dir / "conv_frontend.onnx"),
        providers=["CPUExecutionProvider"],
    )
    enc = ort.InferenceSession(
        str(model_dir / "encoder.int8.onnx"),
        providers=["CPUExecutionProvider"],
    )
    dec = ort.InferenceSession(
        str(model_dir / "decoder.int8.onnx"),
        providers=["CPUExecutionProvider"],
    )

    describe_session("conv_frontend", conv)
    describe_session("encoder", enc)
    describe_session("decoder", dec)

    n_layers, kv_keys = count_kv_cache_layers(dec)
    print(f"\ndecoder KV layers detected: {n_layers}")
    if kv_keys:
        print(f"sample cache_key_0 shape: {kv_keys[0].shape}")

    audio_feat_in = next(
        i for i in dec.get_inputs() if i.name == "audio_features"
    )
    audio_feat_dim = audio_feat_in.shape[-1]
    if not isinstance(audio_feat_dim, int):
        audio_feat_dim = 3584
    print(f"decoder audio_features last dim: {audio_feat_dim}")

    if kv_keys:
        try_decoder_dry_run(dec, n_layers, kv_keys[0].shape, audio_feat_dim)

    tok_cfg = json.loads((root / "tokenizer" / "config.json").read_text())
    print("\n=== tokenizer/config.json summary ===")
    for key in (
        "vocab_size",
        "hidden_size",
        "num_hidden_layers",
        "num_attention_heads",
        "num_key_value_heads",
        "head_dim",
        "max_position_embeddings",
    ):
        if key in tok_cfg:
            print(f"  {key}: {tok_cfg[key]}")


if __name__ == "__main__":
    main()
