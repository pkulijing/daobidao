"""结构化日志 —— structlog 接 stdlib logging，同时落盘 + 打 stderr。

日志目录按平台分：
- macOS: ~/Library/Logs/Daobidao/   (Apple 推荐;Console.app 会扫)
- Linux: $XDG_STATE_HOME/daobidao/  (兜底 ~/.local/state/daobidao/)
- Dev  : {repo_root}/logs/           (通过 .git + pyproject.toml 探测)

目录里有两个文件:
- daobidao.log          app 的结构化日志(logfmt),RotatingFileHandler,
                        1 MB × 3 份
- daobidao-launchd.log  macOS 专属,由 launchd 在 plist 里 StandardErrorPath
                        直接写入,捕获 pre-logger 阶段的崩溃。不经 Python
                        轮转,避免和 RotatingFileHandler 抢 fd
"""

from __future__ import annotations

import contextlib
import logging
import logging.handlers
import os
import sys
from pathlib import Path
from typing import ClassVar

import structlog

from daobidao.backends import IS_MACOS

APP_LOG_FILENAME = "daobidao.log"
LAUNCHD_LOG_FILENAME = "daobidao-launchd.log"

_MAX_BYTES = 1_000_000
_BACKUP_COUNT = 3

_configured = False

# 37 轮:控制台通道放行的 INFO 事件名。
#
# 34 轮把 terminal log 整体静默(只挂 file handler)之后,终端上一条应用自己
# 的输出都没有了 —— 用户看到的唯一一行是 modelscope 打的 "Downloading 9
# files ...",之后永远沉默。首次启动真下载要几分钟,期间那行不动,看起来
# 就是卡死。那次静默的动机(不想持续刷结构化 INFO log)成立,所以不回滚,
# 改成只放行下面这些**每次启动各出现一次**的里程碑,不随使用刷屏。
#
# WARNING 以上不走这张表,一律放行(见 _ConsoleFilter)。
_CONSOLE_INFO_EVENTS = frozenset(
    {
        "startup_banner",
        "model_preload_start",
        "qwen3_download_slow",
        "preload_fallback_to_0_6b",
        "ready",
        "hotkey_listening",
        "shutting_down",
        # daobidao --init 的一次性初始化
        "init_start",
        "init_download_model",
        "init_model_ready",
        "init_done",
    }
)

# structlog 塞进 record 的元字段,拼 fallback 文案时要排掉。
_STRUCTLOG_META_KEYS = frozenset(
    {"event", "message", "level", "timestamp", "logger", "exc_info"}
)


def _dev_log_dir() -> Path | None:
    """Dev 模式:返回 {repo_root}/logs/,非 dev 返回 None。

    复用 config_manager._find_project_root 的 .git + pyproject.toml 探测,
    行为和 config 路径解析保持一致。
    """
    from daobidao.config_manager import _find_project_root

    root = _find_project_root()
    return (root / "logs") if root is not None else None


def get_log_dir() -> Path:
    """解析日志目录(不创建)。"""
    dev = _dev_log_dir()
    if dev is not None:
        return dev
    if IS_MACOS:
        return Path(os.path.expanduser("~/Library/Logs/Daobidao"))
    xdg_state = os.environ.get("XDG_STATE_HOME")
    base = (
        Path(xdg_state)
        if xdg_state
        else Path(os.path.expanduser("~/.local/state"))
    )
    return base / "daobidao"


def get_log_file() -> Path:
    """app 结构化日志文件路径。"""
    return get_log_dir() / APP_LOG_FILENAME


def get_launchd_log_file() -> Path:
    """macOS plist StandardErrorPath 指向的文件路径。"""
    return get_log_dir() / LAUNCHD_LOG_FILENAME


class _ConsoleFilter(logging.Filter):
    """控制台通道的放行规则:WARNING 以上全放,INFO 只放白名单事件。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno >= logging.WARNING:
            return True
        msg = record.msg
        return (
            isinstance(msg, dict) and msg.get("event") in _CONSOLE_INFO_EVENTS
        )


class _ConsoleFormatter(logging.Formatter):
    """纯文本渲染:只出人话,不带时间戳 / logger 名 / key=value。

    ``message`` 字段是 i18n 之后的用户可读文案,优先用它;没有(不少
    warning 只带结构化字段)就退回"事件名 + 关键字段",别打成空行。
    """

    _PREFIX: ClassVar[dict[int, str]] = {
        logging.WARNING: "⚠ ",
        logging.ERROR: "✗ ",
        logging.CRITICAL: "✗ ",
    }

    def format(self, record: logging.LogRecord) -> str:
        msg = record.msg
        if isinstance(msg, dict):
            text = msg.get("message") or self._fallback(msg)
        else:
            # 第三方库经 root logger 冒上来的普通 record
            text = record.getMessage()
        prefix = self._PREFIX.get(record.levelno, "")
        return f"{prefix}{text}"

    @staticmethod
    def _fallback(event_dict: dict) -> str:
        event = event_dict.get("event", "")
        extras = " ".join(
            f"{k}={v}"
            for k, v in event_dict.items()
            if k not in _STRUCTLOG_META_KEYS
        )
        return f"{event} {extras}".strip()


def _shared_processors() -> list:
    return [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]


def configure_logging(
    level: str = "INFO",
    *,
    stderr: bool = False,
    console: bool = True,
) -> None:
    """一次性配好 structlog + stdlib logging。再调用会重新配置(幂等)。

    三档输出,file handler 恒挂:

    - ``console=True`` (默认):额外挂**控制台通道** —— 纯文本、只放行启动
      里程碑与 WARNING 以上,终端既有必要反馈又不会被 INFO log 刷屏。
    - ``console=False`` (``--quiet``):只挂 file handler,终端全静默。
    - ``stderr=True`` (``--verbose``):挂完整 ``ConsoleRenderer``、全量输出,
      此时不再叠加控制台通道(否则同一条打两遍)。也用于 launchd 把 stderr
      重定向到日志文件的场景。
    """
    global _configured  # noqa: PLW0603 - 幂等配置的一次性标志

    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    shared = _shared_processors()

    structlog.configure(
        processors=[
            *shared,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    # 文件:logfmt (KeyValueRenderer),grep 友好、人也能读
    file_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            structlog.processors.KeyValueRenderer(
                key_order=["timestamp", "level", "logger", "event"],
            ),
        ],
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / APP_LOG_FILENAME,
        maxBytes=_MAX_BYTES,
        backupCount=_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(file_formatter)

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
        with contextlib.suppress(Exception):
            h.close()
    root.addHandler(file_handler)

    if stderr:
        # 终端是 TTY 就带颜色,否则纯文本(避免 ANSI 码污染重定向的日志)
        stderr_formatter = structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.dev.ConsoleRenderer(
                    colors=sys.stderr.isatty(),
                ),
            ],
        )
        stderr_handler = logging.StreamHandler(sys.stderr)
        stderr_handler.setFormatter(stderr_formatter)
        root.addHandler(stderr_handler)
    elif console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(logging.INFO)
        console_handler.addFilter(_ConsoleFilter())
        console_handler.setFormatter(_ConsoleFormatter())
        root.addHandler(console_handler)

    root.setLevel(_normalize_level(level))

    _configured = True


def _normalize_level(level: str | int) -> int:
    if isinstance(level, int):
        return level
    return logging.getLevelName(level.upper()) if level else logging.INFO


def get_logger(name: str | None = None):
    """薄封装:调用方统一写 `logger = get_logger(__name__)`。"""
    return structlog.get_logger(name)


def is_configured() -> bool:
    return _configured
