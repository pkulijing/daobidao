"""Modelscope 取消语义验证 spike。

跑这个脚本可以验证:在 ProgressCallback.update 里抛 BaseException 时
modelscope.snapshot_download 是否真的能被中断。

默认被 pytest skip,人工跑(需要网络):
    uv run pytest tests/spike_modelscope_cancel.py -v --no-cov \
        --override-ini="addopts="

预期:抛 _DownloadCancelled 后 snapshot_download 直接挂出来,部分文件
落 cache(不影响下次重下时 modelscope 续上)。
"""

from __future__ import annotations

import shutil

import pytest

pytestmark = pytest.mark.skip(reason="manual spike — runs real download")


def test_cancel_via_callback_baseexception(tmp_path):
    from modelscope import snapshot_download
    from modelscope.hub.callback import ProgressCallback

    raised = {"count": 0}

    class _Cancel(BaseException):
        pass

    class CB(ProgressCallback):
        def __init__(self, filename, file_size):
            super().__init__(filename, file_size)
            print(f"[spike] start {filename}: {file_size} bytes")

        def update(self, n):
            raised["count"] += 1
            if raised["count"] > 3:
                print("[spike] raising _Cancel")
                raise _Cancel()

        def end(self):
            print(f"[spike] end {self.filename}")

    target = tmp_path / "modelscope-cache"
    try:
        with pytest.raises(_Cancel):
            snapshot_download(
                "zengshuishui/Qwen3-ASR-onnx",
                allow_patterns=["model_1.7B/conv_frontend.onnx"],
                cache_dir=str(target),
                progress_callbacks=[CB],
            )
        print(
            f"[spike] OK: BaseException 成功打断 snapshot_download "
            f"after {raised['count']} updates"
        )
    finally:
        shutil.rmtree(target, ignore_errors=True)
