# 目录重构需求

## 问题背景

项目从 `0-初始灵感` 一路迭代到 0.4.0,docs 走到第 12 轮,代码结构是"长出来的":

- **根目录混乱**:11 个 `.py` 文件(`main.py` / `config_manager.py` / `recorder.py` / `hotkey.py` / `input_method.py` / `overlay.py` / `settings_server.py` / `version.py` / `model_state.py` 等)直接堆在仓库根,和 `backends/` / `stt/` 两个子包、`setup_*.sh` / `build.sh` / `run_macos.sh` 这些脚本、`config.yaml` / `config.example.yaml` / `assets/` / `macos/` / `debian/` 等资源/分发目录混在一起
- **`whisper_input` 不是真 package**:`pyproject.toml` 里没有 `[build-system]`,`uv sync` 只装依赖不装项目本身,当前 `from backends import ...` 能 work 是因为 CWD 恰好在 `sys.path` 上
- **`__file__` 路径拼接脆弱**:`config_manager.py` / `version.py` / `backends/autostart_*.py` 里有多处 `Path(__file__).parent` 往上跳若干级找 `assets/` / `config.example.yaml` / `main.py` / `pyproject.toml`,在 `.app` bundle 里已经出过问题(参见 `docs/7-macOS分发优化` 的 TCC 教训)
- **开发/打包/运行期资源没有分层**:分发模板(`.plist` / `.desktop` / DEB control) 和开发脚本混在根目录同层,读代码的人一眼看不出哪些是运行期代码、哪些是打包时才用的

## 期望结果

一次性收敛到**src layout + 单 distribution**,重构完成后:

1. 所有 Python 代码在 `src/whisper_input/` 下,是可 `pip install -e .` / `uv sync` 装成 editable wheel 的真 package
2. 开发期脚本(`setup_*.sh` / `build.sh` / `run_macos.sh` / `generate_icon.py`)全部进 `scripts/`
3. 分发期模板(macOS `.app` 骨架、Debian control/postinst 等)全部进 `packaging/{macos,debian}/`
4. 运行期资源(托盘图标、`.desktop` 模板、`config.example.yaml`)作为 package data 放在 `src/whisper_input/assets/`,通过 `importlib.resources` 访问
5. 对外入口统一为 `whisper-input` console script(也支持 `python -m whisper_input`),不再 `python main.py`
6. `.app` / `.deb` 的打包和首启流程维持"flat 拷贝 + `uv sync` 首启"的现有架构,只是被拷贝的源树变成 `src/whisper_input/` 整棵树,`setup_window.py` 的启动命令从 `main.py` 改为 `python -m whisper_input`
7. `model_state.py` 这个 12 轮重构后已经没人 import 的 shim 顺手删掉

## 约束

- **不做的事**:不拆 `main.py` → `app.py`+`cli.py`;不引入 `tests/`;不改业务逻辑;不升级依赖;不改 Ruff 规则;不做 monorepo;不发 PyPI
- **保 dev 体验**:`config.yaml` 在 dev 模式下继续留在仓库根目录(gitignored),用户手改实时生效
- **保 git 历史**:所有文件搬运用 `git mv`,不用 `rm` + 新建
- **分阶段 commit**:每个 Phase 独立提交,便于出问题精准回滚;非核心路径 Phase(骨架搬运、import 改写)和动核心路径解析的 Phase(`__file__` → `importlib.resources`) 分开,后者是重灾区需要优先测试

## 范围

- **一定要做**:目录骨架重组、`pyproject.toml` 引入 `[build-system]` + `[project.scripts]`、`__file__` 魔法改 `importlib.resources`、`version.py` 切 `importlib.metadata`、`build.sh` 的源文件数组和 `$DEST` 结构更新、`setup_window.py` 的启动入口切换、`README.md` / `CLAUDE.md` 同步
- **不做**:见上面"不做的事"

## 验收

见 PLAN.md 的"验证方案"章节。核心是 dev 模式冒烟 + macOS `.app` 冷启动 + Linux `.deb` 安装三路都跑通。
