# 目录重构总结

## 开发背景

项目从 `0-初始灵感` 一路长到 0.4.0,代码结构是"长出来的":11 个 `.py` 文件堆在仓库根目录,和 `backends/` / `stt/` 两个子包、`.sh` 脚本、`macos/` / `debian/` 分发目录、`assets/` / `config.example.yaml` 全部混在同一层。不只是"不好看":

- **`whisper_input` 根本不是真 package**。`pyproject.toml` 没有 `[build-system]`,`uv sync` 只装依赖不装项目,`from backends import ...` 能 work 纯粹靠 CWD 恰好在 `sys.path` 上。一旦脱离"从仓库根 `uv run` 启动"这个前提,所有 import 都会炸。
- **`__file__` 路径拼接有 6 处**,分散在 `config_manager.py` / `version.py` / `backends/autostart_*.py`,靠 "`__file__` 往上跳 N 级" 定位 `assets/` / `config.example.yaml` / `pyproject.toml` / `main.py`。`.app` bundle 里的目录深度和 dev 不同、DEB flat 装到 `/opt/` 又是另一种形状,这堆写法每次重构都得重新对一遍,docs/7 的 TCC 权限事故里就摔过一次。
- **运行期 / 打包期 / 开发期资源没分层**。PyInstaller 模板、DEB control 文件、dev 期 shell 脚本全堆在仓库根同一层,读代码的人一眼看不出谁是谁。

本轮目标是一次性收敛成 **src layout + 单 distribution**,让:
1. 所有 Python 代码在 `src/whisper_input/` 下,`uv sync` 装成 editable wheel
2. 对外入口统一为 `whisper-input` console script(也支持 `python -m whisper_input`)
3. 运行期资源走 `importlib.resources`,打包产物走 `importlib.metadata`,彻底干掉 `__file__` 路径魔法
4. 开发期脚本进 `scripts/`,分发期模板进 `packaging/{macos,debian}/`,目录分层和语义一致

明确不做的事:不拆 `main.py` → `app.py+cli.py`、不加 `tests/`、不改业务逻辑、不升级依赖、不做 monorepo、不发 PyPI。

## 实现方案

### 关键设计

1. **src layout + hatchling build backend**。`pyproject.toml` 新增 `[build-system]`(hatchling)和 `[tool.hatch.build.targets.wheel] packages = ["src/whisper_input"]`,让 `uv sync` 把项目自己作为 editable wheel 安装进 venv。重构前 `uv.lock` 里 `whisper-input` 的 `source` 字段是 `virtual`(application 模式,项目不被安装),现在是 `editable`(项目作为真 package 安装)。

2. **console script 入口**。新增 `[project.scripts] whisper-input = "whisper_input.__main__:main"`,`__main__.py` 直接用原来 `main.py` 里已有的 `def main()`,两种调用方式都支持:
   - `uv run whisper-input`(venv 里生成的 binary)
   - `uv run python -m whisper_input`(标准 Python 模块入口)

3. **`__file__` → `importlib.resources` / `importlib.metadata`**。一共 6 处:
   - `version.py`:`__version__` 改用 `importlib.metadata.version("whisper-input")`,`__commit__` 从 package data `_commit.txt` 读取(`importlib.resources.files("whisper_input") / "_commit.txt"`),失败 fallback `git rev-parse HEAD`。
   - `config_manager.py`:新增 `_find_project_root()` helper,用 `.git/` + `pyproject.toml` 双 marker 从 package 位置往上探测 dev 仓库根。探测到即 dev 模式(用仓库根的 `config.yaml`),探测不到即 installed/bundled 模式(用平台 `CONFIG_DIR`)。example 配置永远从 `whisper_input.assets` 通过 `importlib.resources` 读,不再依赖 `INSTALL_DIR` 常量(已删除)。
   - `backends/autostart_linux.py`:`.desktop` 模板查找优先级变成"`/usr/share/applications/whisper-input.desktop`(DEB 装的) → `whisper_input.assets` package data(fallback)",移除 `__file__` 向上拼接。
   - `backends/autostart_macos.py`:`_bundle_trampoline()` 的 marker 检测不变(仍然靠 `__file__` 匹配 `/Contents/Resources/app/`,src layout 下匹配照样成立),dev 模式 fallback 从 `[sys.executable, main.py 绝对路径]` 改成 `[sys.prefix/bin/whisper-input]` 或 `[sys.executable, -m, whisper_input]`。

4. **dev / installed / bundled 三种模式的语义切分**
   - **Dev**:仓库根 `config.yaml` + `git rev-parse HEAD` + `.venv/bin/whisper-input`。通过 `.git + pyproject.toml` 双 marker 识别。
   - **Installed (DEB)**:`/opt/whisper-input/` flat 放 `src/whisper_input/` + `pyproject.toml`,`whisper-input.sh` trampoline → `setup_window.py` → `uv sync`(在 user venv) → `python -m whisper_input`。用户配置走 `CONFIG_DIR`。
   - **Bundled (macOS .app)**:`Contents/Resources/app/src/whisper_input/` + `pyproject.toml`,trampoline 同样经过三阶段 bootstrap,最后 `python -m whisper_input`。

5. **`setup_window.py` 适配**。这两个 bootstrap 文件运行在 bundled `python-build-standalone` 里,只有 stdlib。关键改动:
   - Stage B 的 `sys.path.insert(0, str(APP_SRC))` 改成 `sys.path.insert(0, str(APP_SRC / "src"))`,`from stt.downloader import ...` 改成 `from whisper_input.stt.downloader import ...`。这时 bundled python 虽然没装 `whisper_input`,但 `whisper_input.stt.downloader` 及其依赖链(`whisper_input.__init__` → `whisper_input.stt.__init__` → `whisper_input.stt.base` → `whisper_input.stt.model_paths`)全是纯 stdlib,import 得动。
   - Stage C 的 `[USER_VENV_PYTHON, APP_SRC / "main.py"]` 改成 `[USER_VENV_PYTHON, "-m", "whisper_input"]`。user venv 里 `uv sync` 已经把 whisper-input 作为 editable wheel 装好,`-m` 走 editable install 找到 `APP_SRC/src/whisper_input/` 下的代码。

6. **`build.sh` 整体瘦身**。原来 `SOURCE_PY` / `SOURCE_BACKENDS` / `SOURCE_STT` / `SOURCE_OTHER` 四个数组逐文件枚举,每次加文件都要记得同步。现在提取 `copy_src_tree()` 函数,直接 `cp -R src "$dest/src"` 整棵搬,外加 `pyproject.toml` / `uv.lock` / `.python-version` 三件套,最后 `find ... __pycache__ -exec rm -rf {} +` 清缓存。macOS 和 Linux 分支共用这个函数。Commit hash 从 `$DEST/commit.txt` 改成 `$DEST/src/whisper_input/_commit.txt`。脚本开头加 `cd "$REPO_ROOT"`,支持从任意 CWD 调用。

### 开发内容概括

按开发宪法的"分阶段 commit"原则,重构拆成 7 个独立 commit(详细理由是便于单独回滚,尤其是动核心路径解析的 Phase 4):

| Phase | Commit | 内容 | 文件数 |
|---|---|---|---|
| 1 | `d130966` | `git mv` 骨架搬运(无代码改动) | 46 |
| 2 | `383d979` | 所有 import 改为 `whisper_input.X` 绝对路径 | 9 |
| 3 | `62c01b9` | `pyproject.toml` 启用 src layout + console script | 4 |
| 4 | `0eb76be` | `__file__` 路径拼接改用 `importlib.resources` | 4 |
| 5 | `2c5c7c7` | `build/setup` 脚本适配 src layout 和 console script 入口 | 8 |
| 6 | `26c8018` | `.gitignore` commit.txt 路径更新 | 1 |
| 7 | `a5477d9` | `README` / `CLAUDE.md` / `__main__.py` 同步 src layout 结构 | 3 |

每个 Phase 结束 `uv run ruff check .` 都通过。

### 额外产物

- **删除了 `model_state.py`**。这是 12 轮重构时保留的向后兼容 shim,当时声称 `setup_window.py` 会通过它 import,但实际上 `setup_window.py` 早就直接引 `stt.model_paths` 了。grep 确认除 `model_state.py` 自己和若干 docs/build.sh 文本引用外,没有任何 `.py` 文件真的 import 它。顺手删了。
- **`_find_project_root()` helper**。`config_manager.py` 里新增的这个函数用 `.git` + `pyproject.toml` 双 marker 探测 dev 项目根,比原来的 `__file__` 启发式更稳。特意用 `.git/` 作为强信号——installed/bundled 产物永远不会有 `.git/`,误判概率极低。
- **`scripts/generate_icon.py` 定位改用 `repo_root` 相对**。从 `os.path.dirname(__file__)` 改成 `Path(__file__).resolve().parent.parent`,让输出路径正确落到 `src/whisper_input/assets/whisper-input.png`。

## 局限性

- **Dev 模式(`uv run whisper-input`)完全 work**。自动化和手动都过:console script 启动、托盘、热键 → STT → 粘贴链路、Web 设置页、自启开关、版本/commit 显示,都正常。
- **macOS `.app` bundle 已知不能用**。.app 冷启动会在 Stage A 的 hatchling editable build 阶段炸,因为 bundle 里只拷了 `pyproject.toml` 没拷 `README.md`(pyproject 声明了 `readme = "README.md"`,validate 阶段 `OSError`)。本来打算 1 行修(把 README.md 也拷进 bundle),但更深层的问题是:**整个 "拷源 + 在首启时 uv sync 把项目当 application 装" 的流程,在 src layout 下根本是个权宜之计**——src layout 的正解是"build 出 wheel,bundle 里只装 wheel,首启从本地 wheel 安装"。1 行修是 patch 在错误架构上,所以**故意不修**,留给 14 轮换路线。
- **Linux DEB 同样**没在真机上验证,但 build.sh 路径逻辑和 macOS 一致,大概率也会撞 README.md issue。
- **顺手发现的真 bug 已修**:`_run_pty()` 在用户取消时会 return True,导致 `.deps_sha256` sentinel 被错误写入,下次启动 `deps_up_to_date()` 命中跳过 Stage A,留下半截 venv → Stage C `No module named whisper_input`。修法见 commit `1476b7e`(在两个 setup_window.py 里改成 cancelled 优先返回 False)。这个 bug 重构前就在,只是 application 模式下半截 venv 也能跑,被掩盖。
- **没加 `tests/`**。本就不在范围内,而且项目从头就没测试套,不是本轮造成的缺口。

## 后续 TODO

**14 轮方向决定:放弃 `.app` / `.deb` 手工 bundle,改走 PyPI tool 分发**(`uv tool install whisper-input` / `pipx install whisper-input`)。13 轮把项目变成真 package 是这个转向的必要前提,本身就是 14 轮的铺垫。下面的列表按这个新方向重排:

1. **14 轮:PyPI tool 化**
   - `uv build --wheel` → `uv publish` 走 PyPI(可能需要先注册 namespace)
   - 模型下载逻辑保持不变(用户首次 `whisper-input` 时跑 `whisper_input.stt.downloader`),走用户 home 而非 bundle resources
   - 删 `packaging/`、`scripts/build.sh`、`scripts/run_macos.sh`、`packaging/{macos,debian}/setup_window.py` 这堆 bundle/trampoline 代码
   - autostart 改成"用户跑一次 `whisper-input --install-autostart` 命令式开启",而不是 .app 安装时自动注册
   - `python-build-standalone` 也不需要打包了,用户用自己的 uv/pipx 拉 Python
2. **Linux dev 模式 autostart `.desktop` `Exec=`**(本轮局限性里那条)——14 轮做 `--install-autostart` 时一并解决
3. **未来考虑 `tests/`**——同样推到 14 轮以后,但优先级低于 PyPI 发布

### 本轮内已清理的 TODO

- ✅ `config.example.yaml` 的过时描述已改为 ModelScope 231MB 直连
- ✅ `packaging/debian/postrm` cleanup 死代码已换成 `find ... __pycache__ -exec rm -rf {} +`
- ✅ `_run_pty()` cancel 返回值 bug 已修(commit `1476b7e`)

## 相关文档

- [PROMPT.md](PROMPT.md) — 需求文档
- [PLAN.md](PLAN.md) — 实施计划(带完整的 phase 分解和验证方案)
- 前置重构:[`docs/12-去torch-iic官方ONNX/`](../12-去torch-iic官方ONNX/) 为本轮提供了干净的 STT 依赖树(无 torch、无 funasr、5 个 stdlib only 的 `stt/*`),没有那轮的瘦身,本轮把 `whisper_input.stt.downloader` 塞进 bundled python stdlib 进程里跑的思路是不成立的
