"""版本号管理 - 统一提供 __version__ 和 __commit__ 变量。"""

import subprocess
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

_HERE = Path(__file__).parent

try:
    __version__ = version("whisper-input")
except PackageNotFoundError:
    # 开发模式或未安装时，从 pyproject.toml 解析
    import re

    _toml = _HERE / "pyproject.toml"
    _m = re.search(r'^version\s*=\s*"(.+?)"', _toml.read_text(), re.M)
    __version__ = _m.group(1) if _m else "dev"


def _read_commit() -> str:
    # 打包构建时由 build.sh 写入 commit.txt
    commit_file = _HERE / "commit.txt"
    if commit_file.exists():
        c = commit_file.read_text().strip()
        if c:
            return c
    # 开发模式从 git 读取
    try:
        r = subprocess.run(
            ["git", "-C", str(_HERE), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return ""


__commit__ = _read_commit()
