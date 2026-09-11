"""模型变体下载管理器 — 36 轮"模型管理与可视化下载"。

封装磁盘状态检查 + 后台下载线程 + 进度状态 + 取消信号。
跟 ``Qwen3ASRSTT.load()`` 平级地各自调用 ``modelscope.snapshot_download``
(不引入抽象层),DownloadManager 只负责把文件下到磁盘,session 构造留给
真正切到该 variant 时由 load() 处理。
"""

from __future__ import annotations

import contextlib
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

# 顶层 import 让测试能 patch 到 _download_manager 模块上的符号
from modelscope import snapshot_download
from modelscope.hub.callback import ProgressCallback

from daobidao.logger import get_logger

logger = get_logger(__name__)

VARIANTS = ("0.6B", "1.7B")

REPO_ID = "zengshuishui/Qwen3-ASR-onnx"

# 每个 variant 必需的核心文件。检查时所有文件都被 modelscope 索引到磁盘 →
# 视为已下载;任一缺失(包括用户外部 rm) → 未下载。tokenizer/* 是共享资源
# (两个 variant 通用),不入此清单 — 一旦有任一 variant 在,tokenizer 也已经
# 跟着下了;tokenizer 单独被 rm 是极少见的边界,留给 load() 时自然失败兜底。
REQUIRED_FILES: dict[str, list[str]] = {
    "0.6B": [
        "model_0.6B/conv_frontend.onnx",
        "model_0.6B/encoder.int8.onnx",
        "model_0.6B/decoder.int8.onnx",
    ],
    "1.7B": [
        "model_1.7B/conv_frontend.onnx",
        "model_1.7B/encoder.int8.onnx",
        "model_1.7B/decoder.int8.onnx",
    ],
}


def _empty_state() -> dict[str, Any]:
    return {
        "downloaded": False,
        "downloading": False,
        "received_bytes": 0,
        "total_bytes": 0,
        "speed_bps": 0.0,
        "eta_seconds": 0,
        "error": None,
        "cancelled": False,
    }


class DownloadManager:
    """单实例,管理 0.6B / 1.7B 两个 variant 的下载状态与触发。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, dict[str, Any]] = {
            v: _empty_state() for v in VARIANTS
        }
        # "全局单 active 下载":同一时刻只允许下一个 variant
        self._active_variant: str | None = None
        # 速度计算用的滑动窗口(只跟当前活跃下载有关,变更 variant 时清空)
        # 元素 (monotonic_seconds, cumulative_received_bytes)
        self._byte_log: deque[tuple[float, int]] = deque(maxlen=128)
        # 取消信号:set 后下一次 callback.update 会抛 _DownloadCancelled
        self._cancel_event = threading.Event()
        # modelscope 给出的真实 snapshot 目录。见 set_cache_root。
        self._cache_root: Path | None = None
        # 真正串行化"谁在调 snapshot_download"的锁:后台下载线程与
        # Qwen3ASRSTT.load() 都要先拿到它。见 loading()。
        # 锁序恒为 _download_lock → _lock,不得反向。
        self._download_lock = threading.Lock()

    def variant_states(self) -> dict[str, dict[str, Any]]:
        """返回每个 variant 当前状态的浅拷贝(直接给 _send_json 用)。

        ``downloaded`` 字段每次都重新看磁盘,反映实时状态(用户外部 rm 后
        下次 GET 自动报 False)。``eta_seconds`` 实时计算。
        """
        with self._lock:
            snap = {v: dict(s) for v, s in self._state.items()}
        # cache 检查在锁外做(可能 I/O)
        for v in VARIANTS:
            snap[v]["downloaded"] = self.is_variant_downloaded(v)
            s = snap[v]
            remaining = max(0, s["total_bytes"] - s["received_bytes"])
            speed = s["speed_bps"]
            s["eta_seconds"] = int(remaining / speed) if speed > 0 else 0
        return snap

    @property
    def cache_root(self) -> Path | None:
        """modelscope 给出的真实 snapshot 目录;还不知道时为 None。"""
        with self._lock:
            return self._cache_root

    def set_cache_root(self, root: Path | str | None) -> None:
        """记下 modelscope 自己算出的 snapshot 目录。

        来源只有两个,都是 modelscope 的返回值、不是我们推的路径:
        ``Qwen3ASRSTT.load()`` 成功后的 ``cache_root``,以及本类
        ``_worker`` 里那次 ``snapshot_download`` 的返回值。

        **为什么不自己算**:1.40 起 ``snapshot_download`` 委托给
        ``modelscope_hub``,落点由它内部 ``find_reusable_legacy_repo_dir()``
        在四种历史布局(``models/<owner>--<name>/snapshots/<rev>/`` /
        ``hub/models/<owner>/<name>/`` / ...)之间探测决定。我们复刻不了这套
        探测:37 轮实机上,旧做法与 ``local_files_only=True`` 两条路都指到了
        空的旧布局目录,而 942 MB 真文件在新布局里,于是把已下好的 0.6B 判成
        "未下载"。

        ``None`` 是 no-op —— load 没跑成时别把已知的好值冲掉。
        """
        if root is None:
            return
        with self._lock:
            self._cache_root = Path(root)

    def is_variant_downloaded(self, variant: str) -> bool:
        """variant 的核心文件是否都在磁盘上。

        两个 variant 是同一 repo 下的兄弟目录(``model_0.6B/`` /
        ``model_1.7B/``),所以知道 root 之后这就只是 ``Path.exists()``。
        root 还不知道(没 load 过、也没下载过)→ 返 False,不猜。
        """
        if variant not in REQUIRED_FILES:
            return False
        root = self.cache_root
        if root is None:
            return False
        return all((root / rel).exists() for rel in REQUIRED_FILES[variant])

    @contextlib.contextmanager
    def loading(self, variant: str | None):
        """`Qwen3ASRSTT.load()` 期间独占下载权。

        load() 在缓存没命中时自己就会调 ``snapshot_download``(首启可能几分钟),
        所以它和本类的后台下载必须**双向**互斥 —— 两个下载器无协调地写同一个
        cache 目录,而两个 variant 的 ``allow_patterns`` 还共享 ``tokenizer/*``,
        重叠文件可能被读到写了一半的状态。

        - **load 在前**:占住"全局单活跃"槽位,设置页此时点「下载」被
          ``start()`` 挡成 ``busy``。这条尤其针对首启 —— 那时 ``cache_root``
          还是 None、设置页显示"未下载",正诱导用户去点。
        - **下载在前**:在 ``_download_lock`` 上等它下完再进 body,而不是并发
          再下一份。等待是对的:文件本来就正在被拉下来。

        槽位已被别人占着时**不抢也不清**,那份下载的 ``_worker`` 自己会还。
        """
        with self._download_lock:
            acquired = False
            with self._lock:
                if self._active_variant is None:
                    self._active_variant = variant
                    acquired = True
            try:
                yield
            finally:
                if acquired:
                    with self._lock:
                        if self._active_variant == variant:
                            self._active_variant = None

    # ------------------------------------------------------------------
    # start() — 触发后台下载
    # ------------------------------------------------------------------

    def start(self, variant: str) -> tuple[bool, str | None]:
        """触发后台下载。

        返回 (accepted, reason):
        - accepted=True:已起后台线程,reason=None
        - accepted=False:reason 为 i18n key
          (``invalid_variant`` / ``already_downloaded`` / ``busy``)
        """
        if variant not in REQUIRED_FILES:
            return False, "invalid_variant"
        if self.is_variant_downloaded(variant):
            return False, "already_downloaded"

        with self._lock:
            if self._active_variant is not None:
                return False, "busy"
            self._active_variant = variant
            # 重置该 variant 的进度状态 + 速度窗口 + cancel 信号
            self._state[variant] = _empty_state()
            self._state[variant]["downloading"] = True
            self._byte_log.clear()
            self._cancel_event.clear()

        threading.Thread(
            target=self._worker,
            args=(variant,),
            name=f"model-download-{variant}",
            daemon=True,
        ).start()
        logger.info("model_download_start", variant=variant)
        return True, None

    def cancel(self, variant: str) -> bool:
        """取消正在跑的下载。

        - 仅当当前 active variant 等于参数时才生效(防止误取消)
        - 设置 _cancel_event,worker 内 callback.update 下次抛 _DownloadCancelled
        - 返回是否真正发起了取消
        """
        with self._lock:
            if self._active_variant != variant:
                return False
            self._cancel_event.set()
            logger.info("model_download_cancel_requested", variant=variant)
            return True

    def _worker(self, variant: str) -> None:
        """后台线程:跑 snapshot_download 拉文件,捕获错误写 state。"""
        try:
            allow_patterns = [
                f"model_{variant}/conv_frontend.onnx",
                f"model_{variant}/encoder.int8.onnx",
                f"model_{variant}/decoder.int8.onnx",
                "tokenizer/*",
            ]
            cb_class = _make_callback_class(self, variant)
            # 与 loading() 共用一把锁:同一时刻只允许一个 snapshot_download。
            with self._download_lock:
                root = snapshot_download(
                    REPO_ID,
                    allow_patterns=allow_patterns,
                    progress_callbacks=[cb_class],
                )
            # 下完之后 modelscope 告诉我们文件到底落在哪 —— 记下来
            self.set_cache_root(root)
            logger.info("model_download_done", variant=variant)
        except _DownloadCancelled:
            with self._lock:
                self._state[variant]["cancelled"] = True
            logger.info("model_download_cancelled", variant=variant)
        except Exception as exc:
            with self._lock:
                self._state[variant]["error"] = repr(exc)
            logger.exception("model_download_failed", variant=variant)
        finally:
            with self._lock:
                self._state[variant]["downloading"] = False
                self._active_variant = None

    # ------------------------------------------------------------------
    # progress 累加 — callback 工厂里调用
    # ------------------------------------------------------------------

    def _on_file_start(
        self, variant: str, filename: str, file_size: int
    ) -> None:
        """新文件开始下:累加到 total_bytes。"""
        with self._lock:
            self._state[variant]["total_bytes"] += file_size

    def _on_bytes(self, variant: str, increment: int) -> None:
        """收到一块 chunk:累加 received_bytes,更新速度窗口。

        modelscope ``ProgressCallback.update(size)`` 的 size 是**增量**
        (file_download.py 第 435 行 ``callback.update(len(chunk))``)。
        """
        with self._lock:
            s = self._state[variant]
            s["received_bytes"] += increment
            now = time.monotonic()
            self._byte_log.append((now, s["received_bytes"]))
            # 砍掉 1s 之前的样本
            while self._byte_log and now - self._byte_log[0][0] > 1.0:
                self._byte_log.popleft()
            # 速度 = (window 末端 bytes - 头部 bytes) / Δt
            if len(self._byte_log) >= 2:
                dt = self._byte_log[-1][0] - self._byte_log[0][0]
                db = self._byte_log[-1][1] - self._byte_log[0][1]
                s["speed_bps"] = db / dt if dt > 0 else 0.0
            else:
                s["speed_bps"] = 0.0

    def _on_file_end(self, variant: str) -> None:
        """单文件下完。这里暂不做特殊处理,留 hook 以后扩展。"""


class _DownloadCancelled(BaseException):
    """取消信号专用异常。

    继承 ``BaseException`` 而非 ``Exception``,防止 modelscope retry 装饰器内
    的 ``except Exception`` 误吞 — 我们要它直冲 worker 的顶层捕获。
    """


def _make_callback_class(mgr: DownloadManager, variant: str) -> type:
    """工厂函数:返回一个绑定到指定 mgr+variant 的 ProgressCallback 子类。

    modelscope 对每个文件 ``instantiate(filename, file_size)`` 一次,所以
    我们传的是 class(不是 instance)。闭包让多个文件实例都能写到同一个
    DownloadManager 的 state 上。
    """

    class _Tracker(ProgressCallback):
        def __init__(self, filename: str, file_size: int):
            super().__init__(filename, file_size)
            mgr._on_file_start(variant, filename, file_size)

        def update(self, size: int) -> None:
            mgr._on_bytes(variant, size)
            if mgr._cancel_event.is_set():
                raise _DownloadCancelled()

        def end(self) -> None:
            mgr._on_file_end(variant)

    return _Tracker
