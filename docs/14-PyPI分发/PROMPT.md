# 14 轮需求：走 PyPI 标准分发

## 背景与反思

13 轮重构结束后，我（用户）回头看前面几轮的开发重心，意识到一个方向性的问题：

**在项目还很不正规的阶段（目录结构混乱、推理引擎笨重、没有测试套、分发脚本 patchwork），我花了大量精力去追求"小白用户的一键安装体验"—— 手搓 `.app` bundle、手搓 `.deb`、塞 `python-build-standalone`、写 setup_window 引导首启下载、做 DMG 分发……这是过度设计。**

应该先做的事是：

1. 把代码质量搞好（src layout、真 package、干净的 import / 资源定位）—— 这个 13 轮已经做了
2. 把架构数据做好（依赖树干净、STT 后端可插拔）—— 10/12 轮已经做了
3. **分发用最标准的方式：PyPI**。让懂命令行的用户（开发者、tinker）能先用上工具，反馈回来驱动后续迭代
4. 等项目框架稳定了，再回头优化小白安装体验（DMG / DEB / 引导向导），那时候基础够硬，包装层才值得投入

所以本轮目标是：**把 whisper-input 发布到 PyPI，让用户可以通过 `uv tool install whisper-input` / `pipx install whisper-input` / `pip install whisper-input` 装上就能跑**。

## 目标

1. **能在 PyPI 上通过标准方式装** —— `uv tool install whisper-input` 或 `pipx install whisper-input` 跑通，进程里能 `whisper-input` 启动，热键 → 录音 → STT → 粘贴链路完整
2. **发版走 GitHub Actions + Trusted Publishing** —— workflow 监听 `on: push: tags: ['v*']`，`uv build --wheel` → `pypa/gh-action-pypi-publish@release/v1` 用 OIDC 发布（PyPI 上配 Trusted Publisher，不存 API token）。开发者本地 `git tag vX.Y.Z && git push --tags` 就触发发布，同时打 GitHub Release。这是 httpx / uv / ruff / hatch 这批项目的主流模式
3. **删掉 13 轮遗留的 bundle 期代码** —— `packaging/{macos,debian}/`、`scripts/build.sh`、`scripts/run_macos.sh`、`packaging/{macos,debian}/setup_window.py` 全都是 14 轮要清理的目标。这些代码在 PyPI 路线下既不维护也不运行，留着只会误导读代码的人
4. **autostart 保持现状** —— 继续走设置菜单 / Web UI 的开关，调 `backends/autostart_*.py` 写 LaunchAgent plist / XDG .desktop。本轮一行不动
5. **模型下载换成 `modelscope` 库的 `snapshot_download`** —— 当前的自研 stdlib 下载器（`stt/downloader.py`）是为了在 `setup_window.py` 的 bundled `python-build-standalone` 里跑而专门写的 stdlib-only 实现；14 轮把 setup_window 整个删了，这个约束就消失了。既然走 PyPI 路线就用 modelscope 官方库的标准下载，不再自己造轮子。自研 downloader 连同 `stt/model_paths.py` 里的 SHA256 锁一起简化成"告诉 modelscope 下哪个 repo 的哪个 revision"

## 非目标（明确不做）

- **不做 macOS TCC / Accessibility 权限的 thin `.app` wrapper**。memory 里提到过这个思路（Karabiner 做法），但它属于"小白体验优化"，不是本轮的事。本轮用户自己在系统偏好设置里给 `whisper-input` 进程授权，文档里讲清楚即可
- **不加 `tests/`**。本轮是分发路线切换，不是写测试轮。测试套是更后面的事
- **不动 autostart 代码**。继续走设置菜单开关，不加 `--install-autostart` 之类的 CLI 子命令
- **不改 STT 推理逻辑 / 不升级模型 / 不改热键 / 不改 Web UI** —— 业务代码本轮保持冻结
- **不做 DMG / DEB / AppImage** —— 这正是本轮要删的东西

## 待 Plan 阶段明确的问题

下面这些是我（用户）现在还没想清楚、希望 Agent 在 PLAN.md 里给出方案 / 建议的点：

1. **PyPI 包名冲突检查**。`whisper-input` 这个名字在 PyPI 上是不是已经被占了？如果占了用什么 fallback 名（`whisper-input-tool`？`sensevoice-input`？）
2. **版本号从哪里开始**。当前 `pyproject.toml` 的 version 是多少？首发 PyPI 版本应该 bump 到 `0.5.0` 还是直接 `1.0.0`？我倾向前者，但想听 Agent 意见
3. **macOS 权限注册的归属问题**。PyPI tool 装出来的 `whisper-input` 可执行文件实际上是 uv tool / pipx venv 里的 Python 调用，系统看到的进程是 Python 本身，不是 "whisper-input.app"。首次使用热键时系统会弹授权框给哪个进程？这个路径要验证并在文档里讲清楚
4. **`modelscope` 库的替换面**。当前 `stt/downloader.py` 和 `stt/model_paths.py` 里有哪些代码会被 modelscope `snapshot_download` 直接吃掉？调用点是哪里（`sense_voice.py` 的加载路径、`config_manager.py` 的 manifest 查询、setup_window 的 bootstrap 调用，这三个都要识别）？`modelscope` 主包的依赖膨胀成本（transitive deps）值不值得，有没有更轻的子包（比如只依赖 `modelscope[framework]` 或 `modelscope-hub` 之类）
5. **Linux 上 `evdev` 的权限问题**。PyPI 装出来没有 postinst 脚本把用户加进 `input` 组，首次使用热键会 permission denied。这个怎么引导用户解决
6. **删除的文件清单**。把所有要删的 bundle 期文件列出来（packaging/、scripts/build.sh、scripts/run_macos.sh、setup_window.py、stt/downloader.py 等），顺便识别有没有"以为能删但其实被某处 import"的东西
7. **GitHub Actions workflow 的 OS 矩阵**。wheel 是 pure-python（`py3-none-any`）还是需要按 OS 出多个 wheel？如果是 pure-python 的话单个 job 就够；如果某个依赖（`evdev` / `pynput` / `sounddevice`）触发 platform-specific wheel，需要在 matrix 里出 macos + linux wheel

## 验收标准

一个本轮可以宣布"干完了"的状态：

1. `uv build --wheel` 在干净 checkout 上跑通，产出 `dist/whisper_input-X.Y.Z-py3-none-any.whl`
2. 在全新的 macOS 和 Linux 机器（或干净的 venv）上，`uv tool install --from ./dist/whisper_input-X.Y.Z-py3-none-any.whl whisper-input` 装完，能 `whisper-input` 启动，跑完"热键 → 录音 → SenseVoice 转写 → 粘贴"完整链路
3. PyPI 上能搜到包（test.pypi.org 至少过一遍 dry run，正式 pypi.org 由我手动批准后发）
4. 文档里有一份"用户安装步骤" + "开发者发版流程"的指引（README 补一段即可，不需要单独文档）
5. 仓库里没有任何 bundle / DEB / DMG 期的代码或脚本残留
6. `uv run ruff check .` 通过

## 参考上下文

- [13 轮 SUMMARY](../13-目录重构/SUMMARY.md) 详细记录了本轮的前置条件：src layout、hatchling editable build、console script 入口、`importlib.resources` 资源定位、三种运行模式的语义切分
- 13 轮 SUMMARY 的"后续 TODO"里第 1 条就是"14 轮：PyPI tool 化"，本轮是对那份 TODO 的兑现
- memory `project_distribution_pivot.md` 记录了转向的 why 和 how
