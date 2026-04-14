"""SenseVoice 模型本地状态：跨进程/跨平台的模型缓存清单。

设计动机：让 Whisper Input 在"一次联网下载成功"之后成为彻底的本地闭环。
之后无论开机自启动、网络迟到、还是机器永久离线,setup_window stage B 和
stt_sensevoice 都不再发起任何网络请求 —— 包括 modelscope 的"看看有没有新
版"那种隐式 revision check。

清单文件 JSON:

    {
        "model_id":   "iic/SenseVoiceSmall",
        "model_path": "/abs/path/to/iic/SenseVoiceSmall",
        "files":      ["model.pt", "configuration.json", ...]
    }

写入采用 .tmp + os.replace 原子替换,进程被杀不会留半截文件。

只能依赖 stdlib —— setup_window 阶段 user venv 还没建好,bundled python
里没有 yaml/funasr 之类。所以 USER_DATA_DIR 这里独立实现一份,而不是
import config_manager。
"""

import json
import os
import sys
from pathlib import Path

# SenseVoiceSmall 的关键文件 —— 任意一项缺失就当模型不可用,强制重下,
# 用来兜底"上次下到一半进程被杀"或磁盘损坏的场景。
REQUIRED_FILES: tuple[str, ...] = (
    "model.pt",
    "configuration.json",
    "config.yaml",
    "tokens.json",
    "am.mvn",
    "chn_jpn_yue_eng_ko_spectok.bpe.model",
)

DEFAULT_MODEL_ID = "iic/SenseVoiceSmall"


def _user_data_dir() -> Path:
    """manifest 所在目录,和 setup_window 的 USER_DATA_DIR 对齐。"""
    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/Whisper Input"
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")
    return Path(xdg) / "whisper-input"


def state_path() -> Path:
    return _user_data_dir() / ".model_state.json"


def _modelscope_cache_candidates(model_id: str) -> list[Path]:
    """枚举 modelscope 可能 cache 模型的目录。

    modelscope 不同版本的实际缓存布局不一致:
        老版本: <hub>/<owner>/<name>
        新版本: <hub>/models/<owner>/<name>
    都查一遍,命中哪个用哪个。
    """
    base_env = os.environ.get("MODELSCOPE_CACHE")
    if base_env:
        bases = [Path(base_env).expanduser() / "hub"]
    else:
        bases = [Path.home() / ".cache/modelscope/hub"]
    out: list[Path] = []
    for base in bases:
        out.append(base / model_id)
        out.append(base / "models" / model_id)
    return out


def _is_complete(model_dir: Path) -> bool:
    if not model_dir.is_dir():
        return False
    for fname in REQUIRED_FILES:
        f = model_dir / fname
        try:
            if not f.is_file() or f.stat().st_size == 0:
                return False
        except OSError:
            return False
    return True


def load_state() -> dict | None:
    """读 manifest,损坏/不存在都返回 None。"""
    p = state_path()
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def save_state(model_id: str, model_path: str) -> None:
    """原子写入 manifest。"""
    p = state_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_id": model_id,
        "model_path": str(model_path),
        "files": list(REQUIRED_FILES),
    }
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, p)


def find_local_model(model_id: str = DEFAULT_MODEL_ID) -> str | None:
    """查找 model_id 对应的可用本地路径,找不到返回 None。

    查找顺序:
      1. manifest 里记录的路径(首选 —— 这是上次成功落盘的产物)
      2. modelscope 默认缓存路径(兼容老版本/用户手动 mv 过去的场景)

    只要候选目录的 REQUIRED_FILES 全在且非空就算命中。
    """
    state = load_state()
    if state and state.get("model_id") == model_id:
        candidate = Path(state.get("model_path", ""))
        if _is_complete(candidate):
            return str(candidate)

    for candidate in _modelscope_cache_candidates(model_id):
        if _is_complete(candidate):
            return str(candidate)

    return None
