# 目录重构实施计划

> 本文是精简的工程视角计划。完整决策依据(为什么 src layout、为什么 hatchling、为什么不做 monorepo、`__file__` 魔法清单) 已经在 PROMPT.md 里交代过,这里只讲"怎么做"。

## 目标目录

```
whisper-input/
├── pyproject.toml               # 新增 [build-system] + [project.scripts] + [tool.hatch]
├── uv.lock
├── README.md
├── CLAUDE.md
├── .python-version
├── .gitignore                   # 新增 src/whisper_input/_commit.txt
├── config.yaml                  # dev 模式运行时配置,gitignored,留根目录
├── docs/
├── src/
│   └── whisper_input/
│       ├── __init__.py          # 空
│       ├── __main__.py          # ← 原 main.py,暴露 main()
│       ├── version.py
│       ├── recorder.py
│       ├── hotkey.py
│       ├── input_method.py
│       ├── overlay.py
│       ├── config_manager.py
│       ├── settings_server.py
│       ├── _commit.txt          # 打包时由 build.sh 写入;gitignored
│       ├── assets/              # package data
│       │   ├── whisper-input.png
│       │   ├── whisper-input.desktop
│       │   └── config.example.yaml
│       ├── backends/            # 原样
│       │   ├── __init__.py
│       │   ├── hotkey_linux.py / hotkey_macos.py
│       │   ├── input_linux.py / input_macos.py
│       │   ├── overlay_linux.py / overlay_macos.py
│       │   └── autostart_linux.py / autostart_macos.py
│       └── stt/                 # 原样
│           ├── __init__.py
│           ├── base.py / sense_voice.py / model_paths.py / downloader.py
│           └── _wav_frontend.py / _tokenizer.py / _postprocess.py
├── scripts/
│   ├── setup_macos.sh / setup_linux.sh
│   ├── build.sh / run_macos.sh
│   └── generate_icon.py
└── packaging/
    ├── macos/
    │   ├── Info.plist / whisper-input.sh
    │   ├── setup_window.py / python_dist.txt
    └── debian/
        ├── control / postinst / prerm / postrm
        ├── whisper-input.sh
        ├── setup_window.py / python_dist.txt
```

**删除**:
- `model_state.py`(shim,已无引用)
- 根目录 `assets/` 文件夹(内容已分散到 `src/whisper_input/assets/` 和 `scripts/`)

## 关键设计

### 1. Build backend: hatchling

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/whisper_input"]

[tool.hatch.build.targets.wheel.force-include]
"src/whisper_input/assets" = "whisper_input/assets"
```

### 2. Console script

```toml
[project.scripts]
whisper-input = "whisper_input.__main__:main"
```

`__main__.py` 末尾:

```python
def main():
    ...  # 原 if __name__ == "__main__" 下的代码

if __name__ == "__main__":
    main()
```

### 3. `__file__` → `importlib.resources` / `importlib.metadata`

| 位置 | 当前 | 新 |
|---|---|---|
| `version.py` 读 version | 解析 `pyproject.toml` | `importlib.metadata.version("whisper-input")` |
| `version.py` 读 commit | 读 `commit.txt`(根目录) | `importlib.resources.files("whisper_input") / "_commit.txt"`,失败 fallback `git rev-parse HEAD` |
| `config_manager.py` 找 example 配置 | `Path(__file__).parent / "config.example.yaml"` | `importlib.resources.files("whisper_input.assets") / "config.example.yaml"` |
| `config_manager.py` 找 dev 项目根 | `os.path.dirname(__file__)` | 新增 `_find_project_root()`:从 `__file__` 往上找,遇到同时包含 `pyproject.toml` 和 `src/whisper_input/` 的目录即为 dev 项目根 |
| `backends/autostart_linux.py` 找 `.desktop` 模板 | `__file__` 向上两级拼 `assets/whisper-input.desktop` | `importlib.resources.files("whisper_input.assets") / "whisper-input.desktop"` |
| `backends/autostart_macos.py` bundle trampoline 检测 | `__file__` 找 `/Contents/Resources/app/` marker | 同样的 marker 匹配,只是路径多一层 `src/whisper_input/backends/`;dev fallback 改成 `.venv/bin/whisper-input` |

### 4. Dev / installed / bundled 三种模式的语义

- **Dev**:仓库根目录 `config.yaml` + git rev-parse HEAD + `.venv/bin/whisper-input`
- **Installed(DEB)**:`/opt/whisper-input/` 下 flat 放置 `whisper_input/` + pyproject.toml,首次通过 `whisper-input.sh` 启动时跑 `setup_window.py` 做 uv sync + `python -m whisper_input`
- **Bundled(macOS .app)**:`Contents/Resources/app/whisper_input/` + pyproject.toml,`whisper-input.sh` trampoline → `setup_window.py` → `python -m whisper_input`

## 执行顺序(每 Phase 一个 commit)

### Phase 1 — 骨架搬运(纯 `git mv`)
- `mkdir -p src/whisper_input/{assets,backends,stt} scripts packaging`
- `git mv` 所有源文件到目标位置(清单见目标目录章节)
- `git rm model_state.py`
- 新建空 `src/whisper_input/__init__.py`
- `main.py` → `src/whisper_input/__main__.py`(仅重命名,内容 Phase 2 再改)
- Commit: `refactor: 迁移到 src layout 目录骨架(无代码改动)`

### Phase 2 — 改 import
对 `src/whisper_input/` 下所有 `.py` 批量改:`from X import Y` → `from whisper_input.X import Y`(X 可以是 `config_manager` / `backends.hotkey_macos` / `stt.model_paths` 等)。

`__main__.py` 末尾包 `main()` + `if __name__ == "__main__": main()`。

Commit: `refactor: 所有 import 改为 whisper_input.X 绝对路径`

### Phase 3 — `pyproject.toml`
- 加 `[build-system]` / `[project.scripts]` / `[tool.hatch.build.targets.wheel]`
- `rm -rf .venv && uv sync`
- `uv run ruff check .` 通过
- `uv run python -c "import whisper_input"` 通过
- Commit: `refactor: pyproject.toml 启用 src layout + console script`

### Phase 4 — 消灭 `__file__` 魔法
按前表逐条改写 `version.py` / `config_manager.py` / `backends/autostart_linux.py` / `backends/autostart_macos.py`。

Commit: `refactor: __file__ 路径拼接改用 importlib.resources`

### Phase 5 — 脚本适配
- `scripts/build.sh`:`SOURCE_*` 数组指向 `src/whisper_input/`,`$DEST/` 结构保留 `whisper_input/` 包名一层;commit hash 写 `$DEST/whisper_input/_commit.txt`;`pyproject.toml` 也拷到 `$DEST/`;图标脚本路径改 `scripts/generate_icon.py`;`packaging/macos/` 和 `packaging/debian/` 替代原 `macos/` / `debian/`
- `scripts/setup_macos.sh` / `scripts/setup_linux.sh` / `scripts/run_macos.sh`:`uv run python main.py` → `uv run whisper-input`
- `packaging/macos/setup_window.py` / `packaging/debian/setup_window.py`:启动从 `main.py` 改为 `python -m whisper_input`;路径里 `$APP_SRC/*.py` 变 `$APP_SRC/whisper_input/*.py` 或 `$APP_SRC/pyproject.toml`
- `packaging/*/whisper-input.sh`:同样的入口点切换

Commit: `refactor: build/setup 脚本适配 src layout 和 console script 入口`

### Phase 6 — `.gitignore`
新增 `src/whisper_input/_commit.txt`,移除老的 `commit.txt`。

Commit: `chore: gitignore 新增 src/whisper_input/_commit.txt`

### Phase 7 — 文档同步
- `README.md`:命令和目录结构
- `CLAUDE.md`:Architecture / Commands / Key Technical Decisions 全部刷新
- Commit: `docs: README / CLAUDE.md 同步 src layout 结构`

### Phase 8 — 端到端验证
Dev 模式冒烟:
```bash
rm -rf .venv && uv sync
uv run python -c "import whisper_input; from whisper_input.version import __version__, __commit__; print(__version__, __commit__)"
uv run whisper-input --help
uv run python -m whisper_input --help
uv run ruff check .
```

macOS 打包验证:
```bash
bash scripts/build.sh
open "build/macos/Whisper Input.app"
```

Linux 打包验证:
```bash
bash scripts/build.sh
sudo dpkg -i build/linux/whisper-input_*.deb
whisper-input
```

### Phase 9 — SUMMARY.md

按开发宪法写 `docs/13-目录重构/SUMMARY.md`。

## 关键修改文件清单

1. `pyproject.toml` — 加 build-system/scripts/hatch
2. `scripts/build.sh` — 源文件数组和路径映射
3. `src/whisper_input/config_manager.py` — dev 检测 + `importlib.resources`
4. `src/whisper_input/__main__.py` — import 改写 + `main()` 包装
5. `src/whisper_input/version.py` — `importlib.metadata` 替换 pyproject.toml 解析
6. `src/whisper_input/backends/autostart_macos.py` — bundle 检测路径 + dev fallback
7. `src/whisper_input/backends/autostart_linux.py` — `.desktop` 模板查找
8. `packaging/macos/setup_window.py` / `packaging/debian/setup_window.py` — 启动入口切换
9. `src/whisper_input/settings_server.py` 及其他 — 纯 import 改写
10. `README.md` / `CLAUDE.md` / `.gitignore`

## 风险

1. hatchling 和依赖冲突(低,排查 `[build-system]` 即可)
2. `setup_window.py` 三阶段 bootstrap 路径假设多,Phase 5 要逐字段核对
3. macOS LaunchAgent plist 在 bundle/dev 两种模式下的 `ProgramArguments` 切换逻辑,两种模式都要实测

回退:每 Phase 独立 commit,`git revert` 即可。
