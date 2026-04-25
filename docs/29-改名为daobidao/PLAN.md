# 改名为 daobidao —— 实施计划

## 0. 命名总则

为减少二义性，先把整个项目里"名字"涉及的几个层级都钉死：

| 维度 | 旧值 | 新值 |
|------|------|------|
| PyPI 项目名(distribution) | `whisper-input` | `daobidao` |
| Python 包名(import 名) | `whisper_input` | `daobidao` |
| console script | `whisper-input` | `daobidao` |
| 用户可见的英文名(标题/横幅) | `Whisper Input` | `Daobidao` |
| 用户可见的中文名(中文 UI) | (无,只有英文) | `叨逼叨` |
| macOS Bundle 显示名 | `Whisper Input` | `Daobidao` |
| macOS Bundle ID | `com.whisper-input.app` | `com.daobidao.app` |
| macOS LaunchAgent label | `com.whisper-input` | `com.daobidao` |
| macOS .app 路径 | `~/Applications/Whisper Input.app` | `~/Applications/Daobidao.app` |
| macOS 配置目录 | `~/Library/Application Support/Whisper Input/` | `~/Library/Application Support/Daobidao/` |
| macOS 日志目录 | `~/Library/Logs/Whisper Input/` | `~/Library/Logs/Daobidao/` |
| Linux 配置目录 | `~/.config/whisper-input/` | `~/.config/daobidao/` |
| Linux 日志目录 | `~/.local/state/whisper-input/` | `~/.local/state/daobidao/` |
| autostart .desktop | `whisper-input.desktop` | `daobidao.desktop` |
| GitHub 仓库 URL(写进 metadata) | `pkulijing/whisper-input` | `pkulijing/daobidao` |

> **关于 GitHub 仓库实际改名**：本轮代码里所有 URL 一次性写成新仓库地址，但 `git remote` / GitHub 后台改名是用户人工操作（GitHub 改完后老地址会自动 redirect，code 里的链接当下就生效）。

> **关于 `Daobidao` vs `叨逼叨`**：Bundle 显示名、`.desktop` 的 `Name=`、横幅、settings.html 的 `<title>` 等"系统层"显示用拉丁字母 `Daobidao`(避免操作系统对 CJK 字符的兼容性陷阱)；中文 locale 文案里用「叨逼叨」(`Daobidao 叨逼叨`、`叨逼叨` 都可，按上下文)；英文/法语 locale 用 `Daobidao`。

## 1. 实施顺序

```
Step 1: 主包改名(src/whisper_input → src/daobidao + 全部 import + 常量)
Step 2: 资产文件改名(assets/whisper-input.* + launcher binary)
Step 3: 添加配置/bundle 自动迁移逻辑(老用户无感升级)
Step 4: 更新 macOS launcher (main.m + build.sh)
Step 5: 更新 i18n locale 文案
Step 6: 更新安装脚本 / setup / dev_reinstall
Step 7: 更新 GitHub Actions workflow
Step 8: 更新所有测试文件
Step 9: 更新文档(README × 2 + BACKLOG + CLAUDE.md + docs/DEVTREE.md)
Step 10: 建立 shim/whisper-input/ 子项目(老包转发壳)
Step 11: 本地验证(ruff + pytest + 起一次新 daobidao)
Step 12: 撰写 SUMMARY.md
```

跨步的依赖：Step 1-2 是底盘，必须先做完；Step 4 修改 main.m 编译产物名跟 Step 2 的 `daobidao-launcher` 文件名要联动；Step 8 测试要在 Step 1-3 之后，因为它会真 import `daobidao` 包。

## 2. Step 1 — 主包改名

### 2.1 pyproject.toml

```toml
[project]
name = "daobidao"
version = "1.0.0"      # 标志性大版本,标识改名
description = "叨逼叨 - 跨平台本地语音输入工具(中英日韩粤,Qwen3-ASR ONNX 本地推理)"
readme = "README.md"
license = { text = "MIT" }
authors = [{ email = "pkuyplijing@gmail.com" }]
requires-python = ">=3.12.13,<3.13"
keywords = [
    "speech-recognition", "voice-input", "qwen3-asr",
    "asr", "stt", "modelscope", "daobidao",
]
classifiers = [...]    # 内容不变

dependencies = [...]   # 内容不变

[project.scripts]
daobidao = "daobidao.__main__:main"

[project.urls]
Homepage = "https://github.com/pkulijing/daobidao"
Repository = "https://github.com/pkulijing/daobidao"
Issues = "https://github.com/pkulijing/daobidao/issues"

[tool.hatch.build.targets.wheel]
packages = ["src/daobidao"]

[tool.hatch.build.hooks.custom]
path = "scripts/hatch_build.py"

# tuna mirror、ruff、pytest、coverage 配置保持不变,但 source/cov 路径改:
[tool.pytest.ini_options]
addopts = "-ra --tb=short --cov=daobidao --cov-report=term"

[tool.coverage.run]
source = ["src/daobidao"]
```

版本号跳到 `1.0.0` 是刻意的：用户从 PyPI 看到 `whisper-input 1.0.0` → 切换到 `daobidao 1.0.0` 是清晰的"改名版本"语义。后续小版本继续 1.0.x。

### 2.2 包目录改名

```
src/whisper_input/  →  src/daobidao/
```

所有内部子目录(`backends/`, `stt/`, `assets/`)结构保持。

### 2.3 全局 import 替换

涉及的所有 `.py` 文件里：

```python
from whisper_input.X import Y    →    from daobidao.X import Y
import whisper_input.X            →    import daobidao.X
"whisper_input"  (字符串引用)     →    "daobidao"
```

涉及面：包内所有源文件 + tests/ + scripts/ 下的 spike 脚本 + scripts/hatch_build.py。

### 2.4 代码常量改写

涉及的关键文件和常量：

| 文件 | 改动 |
|------|------|
| `daobidao/version.py` | `version("whisper-input")` → `version("daobidao")` |
| `daobidao/backends/app_bundle_macos.py` | `APP_NAME = "Daobidao"`、`BUNDLE_ID = "com.daobidao.app"`、`CONFIG_DIR = "~/.config/daobidao"`、`BUNDLE_ENV_KEY = "_DAOBIDAO_BUNDLE"`、所有 `whisper-input` launcher 路径 → `daobidao` |
| `daobidao/backends/autostart_macos.py` | `AUTOSTART_LABEL = "com.daobidao"`,launcher exe 名 `whisper-input` → `daobidao` |
| `daobidao/backends/autostart_linux.py` | `AUTOSTART_FILE = ".../daobidao.desktop"`,desktop 模板文件名同步 |
| `daobidao/config_manager.py` | macOS `CONFIG_DIR` `Whisper Input` → `Daobidao`,Linux `whisper-input` → `daobidao` |
| `daobidao/logger.py` | macOS `~/Library/Logs/Daobidao`、Linux `whisper-input` → `daobidao`,文件名 `whisper-input.log` → `daobidao.log`,`whisper-input-launchd.log` → `daobidao-launchd.log` |
| `daobidao/updater.py` | `PYPI_JSON_URL = "https://pypi.org/pypi/daobidao/json"`、`PACKAGE_NAME = "daobidao"`、`MANUAL_UPGRADE_HINT` 改 `uv tool upgrade daobidao` |
| `daobidao/__main__.py` | argparse `version=f"daobidao {__version__}..."`,docstring/banner |
| `scripts/hatch_build.py` | `whisper_input` 路径 → `daobidao`,`_commit.txt` 写到 `daobidao/` |

## 3. Step 2 — assets 资源改名

### 3.1 包内资产

| 旧 | 新 |
|----|----|
| `src/daobidao/assets/whisper-input.png` | `src/daobidao/assets/daobidao.png`(内容不变,文件改名) |
| `src/daobidao/assets/whisper-input.desktop` | `src/daobidao/assets/daobidao.desktop` |
| `src/daobidao/assets/macos/whisper-input-launcher` | `src/daobidao/assets/macos/daobidao-launcher`(launcher 编译产物) |
| `src/daobidao/assets/macos/.gitignore` 里的 `whisper-input-launcher` 行 | `daobidao-launcher` |

### 3.2 desktop 文件内容

```ini
[Desktop Entry]
Version=1.0
Type=Application
Name=Daobidao
Name[zh_CN]=叨逼叨
GenericName=Voice Input
GenericName[zh_CN]=语音输入工具
Comment=Voice input tool - hold hotkey to speak, release to type
Comment[zh_CN]=本地语音输入工具 - 按住快捷键说话,松开自动输入
Exec=daobidao
Icon=daobidao
Terminal=false
Categories=Utility;Accessibility;
Keywords=voice;speech;input;stt;daobidao;语音;输入;叨逼叨;
StartupNotify=false
```

### 3.3 引用资产的代码

- `app_bundle_macos.py:_get_prebuilt_assets()`：launcher binary 名字 `whisper-input-launcher` → `daobidao-launcher`，`.app/Contents/MacOS/whisper-input` → `.app/Contents/MacOS/daobidao`
- `tray_*.py`(若存在 png 加载)：tray 图标按 `daobidao.png` 加载
- `hatch_build.py`：force_include 里的 `whisper-input-launcher` 路径

### 3.4 config.example.yaml

```yaml
# Daobidao 叨逼叨 - 语音输入配置
...
# 日志目录: macOS ~/Library/Logs/Daobidao/
#          Linux $XDG_STATE_HOME/daobidao/(兜底 ~/.local/state/daobidao/)
```

ConfigManager._generate_yaml() 输出顶部注释同样调整。

## 4. Step 3 — 配置 / bundle 自动迁移

让从 `whisper-input` 升级过来的老用户**无感**：第一次跑 `daobidao` 时，自动把老路径下的东西搬到新路径。**只搬一次，不破坏老路径以外的东西**。

### 4.1 配置目录迁移

新增 `daobidao/_legacy_migration.py`(纯 stdlib,无依赖)，被 `__main__.main()` 在 ConfigManager 初始化前显式调用一次：

```python
def migrate_legacy_user_data() -> None:
    """从 whisper-input 时代的路径搬到 daobidao 路径(若新路径已存在则跳过)。"""
    pairs = []
    if sys.platform == "darwin":
        pairs.append((
            "~/Library/Application Support/Whisper Input",
            "~/Library/Application Support/Daobidao",
        ))
        pairs.append((
            "~/Library/Logs/Whisper Input",
            "~/Library/Logs/Daobidao",
        ))
        pairs.append((
            "~/.config/whisper-input",   # bundle 用过的 venv-path 配置
            "~/.config/daobidao",
        ))
    else:  # linux
        pairs.append((
            "~/.config/whisper-input",
            "~/.config/daobidao",
        ))
        # XDG_STATE_HOME 不展开,优先 env,fallback ~/.local/state
        state = os.environ.get("XDG_STATE_HOME") or "~/.local/state"
        pairs.append((
            f"{state}/whisper-input",
            f"{state}/daobidao",
        ))
    for old, new in pairs:
        old_p = Path(os.path.expanduser(old))
        new_p = Path(os.path.expanduser(new))
        if old_p.is_dir() and not new_p.exists():
            new_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_p), str(new_p))
            # 写一行结构化日志(注意:此时 logger 可能还没配置,先 print 兜底)
```

在 `__main__.main()` 最早(`load_locales()` 之前)调一次。

**模型缓存不动**：`~/.cache/modelscope/hub/` 下的模型按 organization/repo 路径分目录，跟项目名没关系，不需要迁移。

### 4.2 macOS bundle 迁移

新增到 `app_bundle_macos.py`：

```python
LEGACY_APP_BUNDLE_NAME = "Whisper Input.app"
LEGACY_BUNDLE_ID = "com.whisper-input.app"
LEGACY_AUTOSTART_LABEL = "com.whisper-input"

def cleanup_legacy_bundle() -> None:
    """删掉 Whisper Input.app 残留(LaunchAgent + bundle 目录 + TCC),
    避免 LaunchServices 同时识别两个 bundle。新 daobidao bundle 安装前调一次。"""
    legacy_app = os.path.expanduser(
        f"~/Applications/{LEGACY_APP_BUNDLE_NAME}"
    )
    legacy_plist = os.path.expanduser(
        f"~/Library/LaunchAgents/{LEGACY_AUTOSTART_LABEL}.plist"
    )
    if os.path.exists(legacy_plist):
        subprocess.run(
            ["launchctl", "bootout",
             f"gui/{os.getuid()}/{LEGACY_AUTOSTART_LABEL}"],
            capture_output=True,
        )
        os.remove(legacy_plist)
    if os.path.isdir(legacy_app):
        shutil.rmtree(legacy_app)
    for service in ("Accessibility", "ListenEvent"):
        subprocess.run(
            ["tccutil", "reset", service, LEGACY_BUNDLE_ID],
            capture_output=True,
        )
```

在 `install_app_bundle()` 入口调一次。**已知不可避免的痛点**：用户从 `Whisper Input` 升级到 `Daobidao`，TCC 授权(辅助功能 / 麦克风)必须重新点。这是 macOS TCC 的设计 — bundle ID 变了就视为新 app。**README 顶部公告里要明确告知**。

### 4.3 README 顶部改名公告

```markdown
> **🚨 项目改名通知（2026-04-25）**
>
> 本项目原名 `whisper-input`,自 1.0.0 起更名为 **`daobidao`(叨逼叨)**。
>
> - 老 PyPI 包 `whisper-input` 自动转发到 `daobidao`,`pip install -U whisper-input` 仍可获取最新版
> - 老用户首次启动 `daobidao` 会自动迁移配置文件 / 日志 / 自启动条目,无需手动操作
> - **macOS 用户需重新授予辅助功能 + 麦克风权限**(macOS TCC 的限制,bundle ID 变更后无法继承授权)
> - 推荐迁移命令:`uv tool uninstall whisper-input && uv tool install daobidao`
```

## 5. Step 4 — macOS launcher (main.m + build.sh)

### 5.1 main.m

- 注释顶部 "Whisper Input" → "Daobidao"
- 错误对话框文案 "Whisper Input" → "Daobidao"
- venv-path 路径 `~/.config/whisper-input/venv-path` → `~/.config/daobidao/venv-path`
- 错误提示命令 `whisper-input --install-app` → `daobidao --init`(顺带修一下 — 老命令 `--install-app` 现在已经叫 `--init` 了,launcher 里这条 hint 早就过时)
- python 启动脚本里 `from whisper_input.__main__ import main` → `from daobidao.__main__ import main`
- `_WHISPER_INPUT_BUNDLE` env key → `_DAOBIDAO_BUNDLE`

### 5.2 build.sh

```bash
OUT="$OUT_DIR/daobidao-launcher"     # 旧: whisper-input-launcher
PNG="$REPO_ROOT/src/daobidao/assets/daobidao.png"   # 旧: whisper_input/assets/whisper-input.png
ICNS="$OUT_DIR/AppIcon.icns"          # 不变
OUT_DIR="$REPO_ROOT/src/daobidao/assets/macos"      # 旧: src/whisper_input/...
```

## 6. Step 5 — i18n locale

`assets/locales/{zh,en,fr}.json` 里搜索 `Whisper Input` / `whisper-input` / `whisper_input`,逐条替换。三份的总改动条数大约：

- `Whisper Input` 字面值 → `Daobidao` (zh.json 视语境用「叨逼叨」)
- `uv tool uninstall whisper-input` → `uv tool uninstall daobidao`
- `运行 whisper-input` → `运行 daobidao`

涉及的 i18n key 大约：`settings.title`, `settings.autostart_desc`, `settings.confirm_quit`, `update.done_confirm_restart`, `update.hint`, `tray.*`, `main.banner`, `cli.description`, `perm.waiting_for_grant`, `cli.uninstall_help`, `init.*`, `install.*`, `uninstall.*`(共约 15-20 处 × 3 语言 ≈ 50 处替换)。

## 7. Step 6 — 安装脚本 / setup / dev_reinstall

### 7.1 install.sh

- header 文本 `Whisper Input installer` / `Whisper Input 一键安装` → `Daobidao installer` / `叨逼叨 一键安装`
- 所有 user-facing message 里的 `whisper-input` → `daobidao`,`Whisper Input` → `Daobidao`
- `uv tool install whisper-input` → `uv tool install daobidao`
- `whisper-input --init` → `daobidao --init`
- `exec whisper-input` → `exec daobidao`

> 改名公告：在脚本最开头(`main()` 第一行)加一段双语提示，告诉用户"这是改名后的新包,如果你已装过 `whisper-input`,新包会自动迁移配置"。

### 7.2 scripts/setup.sh

- header 文字 / 第一行注释 / 完成提示里的 `whisper-input` → `daobidao`
- `uv run whisper-input` → `uv run daobidao`
- 顺手修一下注释里残留的"SenseVoice ONNX 模型(约 231 MB)" → "Qwen3-ASR(约 990 MB)"(round 26 漏改的，本轮顺手补上,scope creep 控制在 1 行)

### 7.3 scripts/dev_reinstall.sh

- 所有路径 `Whisper Input.app` → `Daobidao.app`
- `com.whisper-input.plist` / `com.whisper-input.app` → `com.daobidao.*`
- `Whisper Input/`(配置目录) → `Daobidao/`
- `uv tool uninstall whisper-input` → `uv tool uninstall daobidao`
- `whisper_input-*.whl` → `daobidao-*.whl`
- 注释里 `~/.cache/modelscope/.../iic/SenseVoiceSmall*` 这几行也清掉,模型已经是 Qwen3 了,留着是 round 26 没清的旧路径

## 8. Step 7 — GitHub Actions

### 8.1 .github/workflows/release.yml

- 注释里 `whisper-input` → `daobidao`
- `pyproject.toml` 校验逻辑里的 grep 命令逻辑无需改(只看 version 字段)
- artifact 名 `dist`(默认)不变

### 8.2 .github/workflows/build.yml

- 注释里 `whisper-input` 名字提及 → `daobidao`
- cache key `modelscope-qwen3-asr-v1` 不需要 bump(模型缓存路径不变)

## 9. Step 8 — 测试更新

`tests/conftest.py` 和所有 `test_*.py`：

- `from whisper_input...` → `from daobidao...`
- monkeypatch / mock target `whisper_input.X` → `daobidao.X`
- 字符串断言里 `whisper-input` / `Whisper Input` 同步改

特别注意 `test_settings_server.py`、`test_main_*`、`test_autostart_*` 这几个会断言 BUNDLE_ID / 路径字面量的，要按 Step 2.4 的常量同步修。

## 10. Step 9 — 文档更新

### 10.1 README.md / README.zh-CN.md

完整改写：

- 标题 `# Whisper Input` → `# Daobidao 叨逼叨`(README.md)、`# 叨逼叨 (Daobidao)`(README.zh-CN.md)
- 顶部插入 § 4.3 的改名公告
- 徽章 URL 全部 `pkulijing/whisper-input` → `pkulijing/daobidao`,PyPI 徽章 `pypi/v/whisper-input` → `pypi/v/daobidao`
- 所有 `uv tool install whisper-input` / `whisper-input --init` / `whisper-input` 命令同步改
- "技术架构"那段里的 `whisper_input.backends`、`whisper_input.stt.qwen3` 等模块名同步改
- `git clone https://github.com/pkulijing/whisper-input` → `daobidao`,`cd whisper-input` → `cd daobidao`(这个 dir 名字默认跟 GitHub repo 走,改完就是 daobidao)

### 10.2 BACKLOG.md

- 文件标题 `# Whisper Input — Backlog` → `# Daobidao — Backlog`
- 文中所有 `whisper-input` 字面值替换
- 已完成段落里历史叙述无需改(那是事实记录),只改现在仍然引用包名/路径的地方

### 10.3 CLAUDE.md (项目根目录)

完整改写：

- Project Overview 第一句 `Whisper Input is...` → `Daobidao(叨逼叨)is...`,补一句"(formerly: whisper-input,改名于第 29 轮)"
- 所有 src/whisper_input → src/daobidao
- 命令示例 `uv run whisper-input` → `uv run daobidao`、`python -m whisper_input` → `python -m daobidao`
- 所有 import 路径示例 `whisper_input.X` → `daobidao.X`
- "Distribution & Release" 段所有命令 `uv tool install whisper-input` → `uv tool install daobidao`
- 不在本轮改的：CLAUDE.md 已经存在的"round 14/26/27/28"等历史叙述里的项目名引用按事实保留(那是讲历史时的项目名,改了反而失真)

### 10.4 docs/DEVTREE.md

- mermaid 图根节点 `ROOT["whisper-input"]` → `ROOT["daobidao"]`
- 在最后一个 epic 后面加一节 "改名" epic(保持开发树完整性):
  ```
  ROOT --> e_rename["改名为 daobidao"]:::epic
  e_rename --> N29["📦 29 · 改名为 daobidao"]:::infra
  ```
- 文末若有项目名文字描述同步改

### 10.5 不在本轮改的

- `docs/0-*` ~ `docs/28-*` 下所有 PROMPT.md / PLAN.md / SUMMARY.md / 补充文档 — **保留原貌**,这些是历史快照
- `docs/12-*` 等若干轮里的 CONTEXT.md — 同样保留

## 11. Step 10 — shim/whisper-input/ 老包

为了让 `pip install -U whisper-input` / `uv tool install whisper-input` 的老用户能拿到迁移版本，建一个独立的子项目，发到 PyPI 占位老包名：

```
shim/whisper-input/
├── pyproject.toml         # name = "whisper-input", version = "0.9.0"
├── README.md              # 大字写"已改名为 daobidao,见 daobidao 包"
└── src/
    └── whisper_input/
        ├── __init__.py    # 用于让 `import whisper_input` 仍能跑
        └── __main__.py    # console script 入口,打提示 + 调 daobidao.__main__:main
```

### 11.1 shim 的 pyproject.toml

```toml
[project]
name = "whisper-input"
version = "0.9.0"           # 比改名前的 0.8.0 高,但不抢 daobidao 的 1.0.0
description = "Renamed to 'daobidao'. This package is a thin redirect."
readme = "README.md"
license = { text = "MIT" }
authors = [{ email = "pkuyplijing@gmail.com" }]
requires-python = ">=3.12.13,<3.13"

dependencies = [
    "daobidao>=1.0.0",
]

[project.scripts]
whisper-input = "whisper_input.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/whisper_input"]
```

### 11.2 shim 的 __main__.py

```python
"""Deprecated shim — 已改名为 daobidao,本包仅做一次性提示并转发到新包。"""

import sys


_BANNER = """\
\033[1;33m[whisper-input]\033[0m 本工具已改名为 \033[1;32mdaobidao\033[0m(叨逼叨)。
当前 whisper-input 仅做转发壳。建议手动迁移:

    uv tool uninstall whisper-input
    uv tool install   daobidao

本次仍以 daobidao 启动 ——
"""


def main():
    print(_BANNER, file=sys.stderr)
    from daobidao.__main__ import main as _real_main
    _real_main()


if __name__ == "__main__":
    main()
```

### 11.3 shim 的 __init__.py

```python
"""whisper-input 已改名为 daobidao,本模块仅做向后兼容。"""

# 让 `import whisper_input` 旧代码不立刻 ImportError,把 daobidao 作为别名暴露
from daobidao import *  # noqa: F401, F403
```

### 11.4 shim 的发布流程

shim 不参与主项目 CI(避免主 release.yml 既发 daobidao 又发 whisper-input 的复杂度)，建议本轮**仅在仓库内建好目录结构，不在本轮立刻发布**。等 daobidao 1.0.0 在 PyPI 上线、本地验证 `pip install daobidao` 能跑通后，再走手动 `cd shim/whisper-input && uv build && uv publish` 把 0.9.0 推上 PyPI。

发布步骤进 docs/29-改名为daobidao/SUMMARY.md 的"后续 TODO"段,提示自己后面手动跑一次。

### 11.5 主仓库 .gitignore / build 隔离

- `shim/whisper-input/.gitignore` 自己一份(`dist/` `*.egg-info` 等)
- 主项目根 `.gitignore` 不需要改(`shim/` 下的产物不属于主包构建)
- 主项目 hatch 不会把 `shim/` 卷进 wheel,因为 `[tool.hatch.build.targets.wheel] packages = ["src/daobidao"]` 已经显式 limit 了

## 12. Step 11 — 本地验证

```bash
uv sync                                     # 产生新 daobidao 的 editable wheel
uv run ruff check .                         # 应该 0 error
uv run pytest                               # 全套测试通过
uv run daobidao --version                   # 输出 daobidao 1.0.0
uv run daobidao --help                      # 看下 banner / argparse 描述
uv run daobidao                             # 真起一次,看日志路径 / 浮窗 / 设置页都正常
```

(macOS 单独还要测一次:`uv run daobidao --init` 看新 bundle 安装、TCC 路径。本轮主开发是在 Linux 上,macOS 验证如果不便可以挪到下轮或人工补 - 在 SUMMARY 里标记。)

迁移路径冒烟:在 dev 模式下手动建一个空的 `~/.config/whisper-input/config.yaml` + 一些占位文件,跑 `daobidao` 看会不会自动 mv 到 `~/.config/daobidao/`,验证没有问题再清理。

## 13. Step 12 — SUMMARY.md

完成后撰写 docs/29-改名为daobidao/SUMMARY.md，按 CLAUDE.md 规定的结构(背景 / 关键设计 / 实现 / 局限性 / 后续 TODO)。后续 TODO 里至少包括：

- shim/whisper-input/ 0.9.0 的 PyPI 发布
- macOS 真机上验证迁移路径(若本轮跳过)
- GitHub 仓库改名(用户人工)
- (可选)icon 视觉重做

## 14. 风险与注意点

1. **PyPI 包名 `daobidao` 是否被人占用** — 在动手前需要 `pip search daobidao` 或访问 `https://pypi.org/project/daobidao/`(404 即可用)。如果被占,临时方案是 `daobidao-input` 或类似变体,本 PLAN 默认 `daobidao` 可用,若不行先暂停问用户。
2. **shim 包的 `from daobidao import *`** — 实际上 daobidao 的 `__init__.py` 没有显式 `__all__`,这种 import 行为只导出 module 顶层公开名。多数老代码不会 `import whisper_input` 当库用(它本来就是 console-script-only),所以这一行更多是兜底。如果有用户真把 whisper_input 当库 import 了某个深路径(`from whisper_input.config_manager import X`),shim 的 `__init__.py` 不会让它跑通 — 这个不在本轮承诺范围,在 README 公告里说清楚。
3. **macOS TCC 重新授权** — 不可避免,见 § 4.2。文案里务必说清楚。
4. **`daobidao` 字面值在中英文环境的视觉冲击** — 中文用户一眼看懂"叨逼叨";英文用户看到 "Daobidao" 会拼读成 "dao-bee-dao",可能不知道含义,但作为产品名字符合"短 + 拼写无歧义"标准。系统级显示用拉丁字母规避 macOS / Linux desktop 对 CJK 文件名 / Bundle 名的偶发兼容性问题。
5. **uv.lock** 不手动改,Step 11 跑 `uv sync` 时会自然重生成。提交时一并 commit。
6. **`config.example.yaml` 的注释顶部"# Whisper Input"** 也要改成"# Daobidao 叨逼叨",别忘了。
7. **测试套里的 fixture 路径** — `tests/fixtures/zh.wav` 不需要动(数据文件名跟项目名无关)。
8. **scripts/spike_qwen3_*.py**(实验性脚本)的 import 路径要改,但它不是产品代码,改完不跑也行,运行时再说。

## 15. 不在本轮范围

- 项目 logo / icon 视觉重做(daobidao.png 复用旧 png 内容,文件改名而已)
- GitHub 仓库 rename(用户人工,GitHub 后台一键完成)
- shim 包的实际 PyPI 发布(放到本轮 SUMMARY 的"后续 TODO"里)
- macOS 真机端到端验证(若本轮无 macOS 环境,放到后续轮)

## 16. 验收标准

- [ ] `uv sync` 成功,无错误
- [ ] `uv run ruff check .` 0 error
- [ ] `uv run pytest` 全部通过(239 个用例 + 跟改名相关的可能新增 1-2 个迁移测试)
- [ ] `uv run daobidao --version` 输出 `daobidao 1.0.0 (commit ...)`
- [ ] `uv run daobidao` 能正常起来,日志写到新路径,浮窗 / 设置页 / 托盘正常
- [ ] dev 模式下能跑通老配置自动迁移(手工冒烟)
- [ ] 仓库内全文 grep `whisper[_-]?input` 仅剩历史 docs(`docs/0-*` ~ `docs/28-*`) 和 shim/ 子项目两处
- [ ] CLAUDE.md / README × 2 / BACKLOG / DEVTREE 已更新
- [ ] shim/whisper-input/ 子项目目录建好,含 pyproject + README + 转发壳代码
