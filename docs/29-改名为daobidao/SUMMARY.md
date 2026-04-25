# 改名为 daobidao —— 开发总结

## 背景

项目原名 `whisper-input`，存在三大痛点：

- 辨识度低：PyPI / GitHub 上"本地语音输入工具"叫这个名字的项目一抓一大把
- **名字误导**："Whisper" 让人误以为我们用的是 OpenAI Whisper，实际上 round 26 之后已经迁到阿里 Qwen3-ASR，与 Whisper 没有任何关系
- 中文用户群体看到 "Whisper Input" 不会立刻 get 到这是什么工具

为了让中文用户一眼看懂、辨识度拉满，本轮把项目改名为 **`daobidao`（叨逼叨）**——中文口语里"说个不停"的意思，正好契合语音输入工具的定位。

走「发新包 + 老包变 shim」的标准 OSS 改名路线（参考 `python-Levenshtein` → `Levenshtein`、`pycrypto` → `pycryptodome`），不走"删了重来"——老用户 `pip install -U whisper-input` 仍可拿到新版。

## 实现方案

### 关键设计

1. **新包发布 + 老包薄壳转发**
   - 新 PyPI 项目 `daobidao` 1.0.0 是真正的发布目标
   - 在 `shim/whisper-input/` 下建独立子项目，发布 `whisper-input` 0.9.0：薄壳一个，`Requires-Dist: daobidao`，console script 调用时打一行迁移提示后转发到 `daobidao.__main__:main`
   - 顶层 `__init__.py` 用 `from daobidao import *` 兜底，让仍把 `whisper_input` 当库 import 的老代码不立刻 ImportError（深路径 `from whisper_input.X import Y` 不保证）

2. **大版本号语义：1.0.0**
   - 改名前最后版本 `0.7.2`，新包直接跳到 `1.0.0`
   - 用户从 PyPI 看到 `whisper-input 1.0.0 (deprecated) → daobidao 1.0.0` 是清晰的"改名版本"语义
   - shim 包用 0.9.0，不抢 daobidao 的 1.0.0

3. **老用户无感迁移：`_legacy_migration.py`**
   - 第一次跑 `daobidao` 时，自动把老路径搬到新路径（写入标记文件 `~/.daobidao_migrated_from_whisper_input` 实现幂等性）
   - 涵盖 macOS：`~/Library/Application Support/Whisper Input/` → `Daobidao/`、`~/Library/Logs/Whisper Input/` → `Daobidao/`、`~/Applications/Whisper Input.app` → `Daobidao.app`、`com.whisper-input` LaunchAgent bootout
   - 涵盖 Linux：`~/.config/whisper-input/` → `~/.config/daobidao/`、`~/.local/state/whisper-input/` → `~/.local/state/daobidao/`
   - **不动模型缓存**：`~/.cache/modelscope/hub/iic/Qwen3-ASR-onnx/` 路径跟项目名无关，无需迁移
   - **macOS TCC 必须重授权**：bundle ID 从 `com.whisper-input.app` → `com.daobidao.app`，TCC 视为新 app，辅助功能 + 麦克风权限要重新点。这是 macOS 的设计，不可绕过，README 公告里明确告知

4. **拉丁字母 + 中文双显示策略**
   - 系统层（Bundle 名、`.desktop` 的 `Name=`、横幅、HTML `<title>`）一律用 `Daobidao`，规避操作系统对 CJK 字符的偶发兼容性问题
   - 中文 locale (`zh.json`) 文案用「叨逼叨」，加深中文用户的产品认知
   - `Name[zh_CN]=叨逼叨` 让 GNOME 系桌面在中文环境下显示中文名

5. **批量替换避免 token 浪费**
   - 早期采用一文件一文件 `Edit` 的笨办法被用户当场制止，改用 Python 脚本批量替换 + 排除列表（排除 `docs/0-*` ~ `docs/28-*` 历史快照、`uv.lock`），一次处理 128 个文件
   - 历史 `docs/N-XX/{PROMPT,PLAN,SUMMARY}.md` **保留原貌**——它们是 frozen-in-time 的开发档案，回填会失真
   - 活的索引文档（`README` × 2、`BACKLOG.md`、`CLAUDE.md`、`docs/DEVTREE.md`）才需要同步更新

### 开发内容概括

按 PLAN 12 步全部落地：

| Step | 内容 |
|------|------|
| 1 | `pyproject.toml`：name → `daobidao`、version → `1.0.0`、scripts → `daobidao = "daobidao.__main__:main"`、URLs/keywords/coverage 全改；`src/whisper_input/` → `src/daobidao/` |
| 2 | 资源文件改名：`whisper-input.png` → `daobidao.png`、`whisper-input.desktop` → `daobidao.desktop`、launcher 二进制 `whisper-input-launcher` → `daobidao-launcher` |
| 3 | 新增 `daobidao/_legacy_migration.py`，在 `__main__.main()` 入口处用 `contextlib.suppress(Exception)` 调用一次 |
| 4 | macOS launcher：`launcher/macos/main.m` 的 venv-path 路径、错误对话框文案、`_DAOBIDAO_BUNDLE` env key、import 语句全改；`build.sh` OUT 路径同步 |
| 5 | i18n：`zh.json` / `en.json` / `fr.json` 三份文案搜替 `Whisper Input` / `whisper-input`，zh 用「叨逼叨」 |
| 6 | `install.sh` / `scripts/setup.sh` / `scripts/dev_reinstall.sh`：所有命令名、bundle 路径、卸载提示全部 daobidao 化 |
| 7 | `.github/workflows/build.yml` / `release.yml`：注释里的项目名引用 |
| 8 | 测试：`tests/conftest.py` 中 `WHISPER_INPUT_QWEN3_DIR` env → `DAOBIDAO_QWEN3_DIR`，所有 `from whisper_input...` → `from daobidao...`，monkeypatch target 同步 |
| 9 | 文档：README × 2 顶部插改名公告 + 标题 + 命令；`BACKLOG.md` / `CLAUDE.md` / `DEVTREE.md`（节点 N29 + 节点索引行 + Epic 集成与分发轮次列表） |
| 10 | shim：`shim/whisper-input/` 含 `pyproject.toml`(0.9.0、Requires-Dist daobidao)、`README.md`(改名公告)、`src/whisper_input/{__init__,__main__}.py`(薄壳) |
| 11 | 验证：`uv run ruff check .` 0 error、`uv run pytest` 250 通过 38 skip(本机缺 Qwen3-ASR 模型缓存)、`uv sync` 重生成 lockfile（daobidao 1.0.0 替换 whisper-input 0.7.2） |
| 12 | 本文档 |

### 额外产物

- `shim/whisper-input/` 子项目（独立 pyproject + 转发壳代码 + 自己的 `.gitignore`）—— 本轮**不立即发布**到 PyPI，等 daobidao 1.0.0 上线验证后再手动 publish
- `daobidao/_legacy_migration.py` ~150 行，纯 stdlib，幂等性靠 marker file + `Path.exists()` 双重保险
- Python 批量替换脚本（一次性使用，已删）—— 排除清单值得记一笔：`docs/[0-9]*-*/`、`uv.lock`、`*.egg-info/`、`.git/`

## 局限性

1. **shim 包尚未发布到 PyPI**：本轮只在 `shim/whisper-input/` 下准备好代码与 pyproject。需要先发 daobidao 1.0.0 → 验证 `pip install daobidao` 能跑通 → 再 `cd shim/whisper-input && uv build && uv publish` 推 0.9.0。这个手动步骤进 BACKLOG。

2. **macOS 真机端到端验证未完成**：本轮主要在 Linux 上跑。`_legacy_migration.py` 的 macOS 分支（`Library/Application Support`、TCC bundle bootout）只做了静态代码 review，没有真机验证。下次有 macOS 机器时跑一遍 `daobidao --init` + 看 `~/Library/Logs/Daobidao/daobidao.log` 是否有迁移日志。

3. **GitHub 仓库改名是用户人工操作**：代码里所有 URL 已经一次性写成 `pkulijing/daobidao`。GitHub 后台 rename 一键完成，老地址会自动 redirect，但需要用户登录后台手动操作。

4. **shim 的 `from daobidao import *` 兜底有限**：daobidao 的 `__init__.py` 没显式 `__all__`，老代码 `from whisper_input.config_manager import X` 这种深路径 import 仍会 ImportError。这不是承诺范围（whisper-input 本来就是 console-script-only 工具，不是给人当库用的），README 公告里说清楚了。

5. **`docs/0-*` ~ `docs/28-*` 内的历史命名未回填**：刻意保留，那是历史快照。如果未来有人写 [docs/14-PyPI分发/SUMMARY.md](../14-PyPI分发/SUMMARY.md) 时看到 `whisper-input` 字样不要感到困惑——那是 round 14 写下的事实。

6. **PyPI 名 `daobidao` 占用风险已规避**：动手前确认 https://pypi.org/project/daobidao/ 404 可用。1.0.0 第一次 push 后即生效。

## 后续 TODO

- [ ] **发布 daobidao 1.0.0 到 PyPI**：`git tag v1.0.0 && git push --tags`，等 `.github/workflows/release.yml` 自动跑通
- [ ] **手动发布 shim/whisper-input 0.9.0**：`cd shim/whisper-input && uv build && uv publish`（需要 PyPI token 或单独配 trusted publishing）
- [ ] **macOS 真机验证迁移路径**：`uv run daobidao --init` → 看 bundle / LaunchAgent / TCC 重授权流程
- [ ] **GitHub 仓库改名**：用户登录后台 → Settings → Rename → `whisper-input` → `daobidao`，老 URL 自动 redirect
- [ ] **（可选）icon 视觉重做**：`daobidao.png` 当前只是把 `whisper-input.png` 改了文件名，内容没换。日后若有设计师介入可以做一个跟「叨逼叨」语义匹配的新图标
- [ ] **跑过几个版本之后下线 shim**：等老用户都迁完（半年到一年），可以考虑把 shim 标记 `Deprecated` 不再更新
