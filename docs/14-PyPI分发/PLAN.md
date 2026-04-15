# 14 轮实施计划 — PyPI 标准分发

## Context

**为什么做这个变更**

13 轮把项目改成 src layout + 真 package 之后，用户回头复盘，意识到前面几轮（特别是 7/8/9/10/11 轮）把大量精力投到"小白一键安装"体验——手搓 `.app` bundle、手搓 `.deb`、塞 `python-build-standalone`、写 setup_window 三阶段引导——**是在项目还很不正规的阶段（目录混乱、没测试、推理引擎笨重）就过度追求包装层**。正确的顺序应该是：先把代码质量 / 架构打好（13/12/10 轮已做），分发用最标准的 PyPI 方式让懂命令行的用户先用上，反馈驱动后续迭代；等项目框架稳定后再回头投资小白体验。

**本轮目标**

把 whisper-input 发布到 PyPI，让用户通过 `uv tool install whisper-input` / `pipx install whisper-input` / `pip install whisper-input` 装上就能跑。同时删掉 13 轮留下的所有 bundle 期死代码（`packaging/`、`scripts/build.sh`、`scripts/run_macos.sh`、两份 setup_window.py）。模型下载换成 `modelscope.snapshot_download`（官方库），删掉自研 stdlib 下载器。autostart 代码保持"设置菜单开关"不变，只删掉已死的 `.app` bundle trampoline 分支。发版走 GitHub Actions tag 触发 + PyPI Trusted Publishing (OIDC)，和 httpx / uv / ruff / hatch 这批主流项目同一种模式。

**非目标**

- 不做 macOS TCC thin `.app` wrapper（"小白体验优化"，未来再说）
- 不加 `tests/`
- 不改 STT 推理逻辑 / 不升级模型 / 不改热键 / 不改 Web UI
- 不做 DMG / DEB / AppImage

## 关键事实（plan 阶段已确认）

- **PyPI 包名 `whisper-input` 空闲**：`https://pypi.org/pypi/whisper-input/json` 返回 404，直接注册 Trusted Publisher 即可
- **当前版本** `0.4.0`（`pyproject.toml:3`），首发 PyPI 版本 bump 到 **`0.5.0`**（不是 1.0.0——1.0.0 意味"对用户稳定完备"，还不到）
- **wheel 为 pure-python** (`py3-none-any.whl`)：本项目无 C 扩展，平台差异完全靠 `pyproject.toml` 里 `sys_platform` marker 分流（evdev 仅 Linux、pynput 仅 macOS）。**GitHub Actions 单 job 单 wheel 即可**
- **`modelscope 1.35.4` 的 base install 只 36 MB**（用户已在 `/tmp/demo` 实测），transitive deps 只有 `filelock / packaging / requests / setuptools / tqdm / urllib3`。torch 和所有重依赖都在 extras 里，不装 extras 就拉不到
- **`modelscope.snapshot_download` API** 已验证（在 `/tmp/demo/.venv`）：
  - `from modelscope import snapshot_download` ✓
  - `allow_patterns: List[str] | str` 支持 HF 兼容的 glob 过滤 ✓
  - `cache_dir: str | Path | None` 可覆盖默认 `~/.cache/modelscope/hub/` ✓
  - `revision: Optional[str]` 支持 branch/tag ✓
  - **返回 `str`**（不是 `Path`，写代码时注意 `Path(...)` 包一下）
- **自研 `stt/downloader.py` 的 SHA256 锁** 不再需要——modelscope 库自己有文件完整性校验，我们追新版本时改 `revision` 参数即可
- **`autostart_macos.py:_bundle_trampoline()`** 检测 `/Contents/Resources/app/` 在 `__file__` 里，PyPI 路径永不命中，是纯 dead code，可整段删
- **`autostart_linux.py`** 优先读 `/usr/share/applications/whisper-input.desktop`（DEB 装的）的分支同样变成 dead path，fallback 到 `whisper_input.assets` package data 是 PyPI 路径的唯一走法
- **`assets/whisper-input.desktop:10`** `Exec=/usr/bin/whisper-input` 硬编码 DEB 路径，PyPI 装不存在这个路径，需要改成 `Exec=whisper-input`（走 PATH）
- **`config_manager.py` 的 `_generate_yaml()`** 里有 stale 注释 "首次启动自动从 GitHub release(走 ghproxy)下载 ~160MB"（10 轮遗留），需要更新
- **`assets/config.example.yaml`** 已在 13 轮末尾更新为 ModelScope 231MB 描述，不动
- **`.github/workflows/build.yml`** 现有 5 个 job（lint / version-check / build-macos / build-linux / release），后三个全部依赖要删的 `scripts/build.sh`。处理方案：保留 `lint` job 存活（README badge 继续指 build.yml），删除 build-* 和 release job，新增一个 `release.yml` 走 tag 触发 PyPI 发布

## 实施阶段

按"分阶段 commit，每 phase 独立可回滚"的原则拆成 6 个 phase。每个 phase 结束 `uv run ruff check .` 通过 + 能 `uv run whisper-input` 跑起来（除非 phase 本身就在改启动路径）。

---

### Phase 1 — 加 modelscope 依赖，改 sense_voice.py 走官方库下载

**目标**：模型下载链路切换成 `modelscope.snapshot_download`，老的 `stt/downloader.py` 和 `stt/model_paths.py` 暂时保留（死代码，phase 2 再删），以保证每个 commit 独立可回滚。

**修改**：

1. **`pyproject.toml`**
   - `dependencies` 加一行 `"modelscope>=1.35.4"`
   - 删除第 12-16 行 sherpa-onnx 历史注释（已过时）

2. **`src/whisper_input/stt/sense_voice.py`**
   - 删除 `from whisper_input.stt.downloader import download_model`
   - 删除 `from whisper_input.stt.model_paths import find_local_model`
   - 新增 `from modelscope import snapshot_download`
   - 重写 `load()` 的模型定位段：
     ```python
     def load(self) -> None:
         if self._session is not None:
             return

         # 主仓库：ONNX 量化模型 + tokens + am.mvn + config.yaml
         onnx_dir = Path(snapshot_download("iic/SenseVoiceSmall-onnx"))
         # 姐妹仓库（PyTorch 原版）只为取 BPE tokenizer 一个文件，
         # 用 allow_patterns 避免误拉 ~900 MB 的 torch 权重
         bpe_dir = Path(snapshot_download(
             "iic/SenseVoiceSmall",
             allow_patterns=["chn_jpn_yue_eng_ko_spectok.bpe.model"],
         ))
         bpe_file = bpe_dir / "chn_jpn_yue_eng_ko_spectok.bpe.model"

         print(f"[sensevoice] 加载 SenseVoice ONNX: {onnx_dir}")
         # ... 后续加载逻辑不变，但引用文件时：
         # model_quant.onnx  → onnx_dir / "model_quant.onnx"
         # tokens.json       → onnx_dir / "tokens.json"（如果代码引用了的话）
         # am.mvn            → onnx_dir / "am.mvn"
         # config.yaml       → onnx_dir / "config.yaml"
         # BPE model         → bpe_file
     ```
   - `_LANG_ID` 这些常量不动，`transcribe()` 不动

3. **`src/whisper_input/stt/__init__.py`**
   - 删除 `**懒加载原则**` docstring 里关于 setup_window 引导向导的那一段（14 行上下），改成只解释懒加载是为了避免 import 时触发 numpy/onnxruntime 链。懒加载本身继续保留（价值仍在：CLI 启动速度）

**验证**：
- `uv sync` 应拉到 modelscope 36 MB
- `uv run whisper-input` 能启动。首启时如果本地 `~/.cache/modelscope/hub/iic/SenseVoiceSmall-onnx/` 已有缓存（13 轮测试留下的可能在 `~/Library/Application Support/Whisper Input/models/` 不在 modelscope cache 路径），会重新下载——这是**刻意的**，我们切 cache 位置到 modelscope 默认路径
- 跑一次完整链路：按热键 → 说话 → 松开 → 检查是否识别出正确文本

**预期 ruff 失败点**：`sense_voice.py` 里移除的 import 如果还有 unused variable 引用，修掉。`Path(snapshot_download(...))` 这种写法 ruff 不会管。

---

### Phase 2 — 删 stt/downloader.py 和 stt/model_paths.py

**目标**：Phase 1 的 sense_voice.py 已经不再 import 这两个文件，现在可以物理删除。

**删除**：

- `src/whisper_input/stt/downloader.py`（整文件 ~180 行）
- `src/whisper_input/stt/model_paths.py`（整文件 ~170 行）

**双重检查**（用 Grep 确认没有遗漏 import）：
```
Grep: "from whisper_input.stt.downloader" / "from whisper_input.stt.model_paths"
Grep: "stt.downloader" / "stt.model_paths"
```
预期剩余的 match 只会在：
- `docs/` 下的历史文档（不动）
- `packaging/{macos,debian}/setup_window.py`（phase 3 会整体删）
- `CLAUDE.md`（phase 6 更新）

**验证**：`uv run ruff check .` 通过；`uv run whisper-input` 正常启动。

---

### Phase 3 — 删 bundle 期所有目录与脚本

**目标**：把 13 轮最后留下的一堆 bundle 期死代码一次性清掉。这是本轮代码量最大的 delete-only commit。

**删除**：

1. `packaging/` 整目录
   - `packaging/macos/setup_window.py` (~600 行)
   - `packaging/macos/whisper-input.sh`
   - `packaging/macos/Info.plist`
   - `packaging/macos/python_dist.txt`
   - `packaging/debian/setup_window.py` (~600 行)
   - `packaging/debian/whisper-input.sh`
   - `packaging/debian/control` / `postinst` / `prerm` / `postrm`
   - `packaging/debian/python_dist.txt`
2. `scripts/build.sh` (~330 行)
3. `scripts/run_macos.sh`

**保留**（本轮不删）：

- `scripts/setup_macos.sh` — 开发者 clone 仓库后跑一次的 dev 环境引导（Homebrew portaudio + uv + uv sync），仍有价值
- `scripts/setup_linux.sh` — 同上，且 `usermod -aG input` 逻辑对 Linux PyPI 用户也有引导意义（README 里会指向它）
- `scripts/generate_icon.py` — 图标生成脚本，dev 用

**顺手清理**：

- `src/whisper_input/backends/autostart_macos.py`
  - 删除 `_bundle_trampoline()` 函数（~20 行）
  - `_program_arguments()` 简化成：
    ```python
    def _program_arguments() -> list[str]:
        venv_script = os.path.join(sys.prefix, "bin", "whisper-input")
        if os.path.isfile(venv_script):
            return [venv_script]
        return [sys.executable, "-m", "whisper_input"]
    ```
  - 删除 docstring 里关于 bundle / setup_window 的描述

- `src/whisper_input/backends/autostart_linux.py`
  - 删除 `_load_desktop_template()` 中的 `SYSTEM_DESKTOP = "/usr/share/applications/whisper-input.desktop"` 分支
  - 统一从 `whisper_input.assets` package data 读模板
  - `SYSTEM_DESKTOP` 常量一起删

- `src/whisper_input/assets/whisper-input.desktop`
  - `Exec=/usr/bin/whisper-input` → `Exec=whisper-input`（PyPI tool / pipx 都会把这个 script 放到用户 PATH 上）

- `src/whisper_input/config_manager.py`
  - `_generate_yaml()` 里的 stale 注释 "首次启动自动从 GitHub release(走 ghproxy)下载 ~160MB" 改成 "首次启动自动从 ModelScope 下载 ~231MB（~/.cache/modelscope/hub/）"

- `src/whisper_input/version.py` — 检查是否还在从 package data 读 `_commit.txt`。Package 里的 `_commit.txt` 是 build.sh 写的，PyPI wheel 里不会有这个文件，所以 `__commit__` 会 fallback 到 `git rev-parse HEAD`（dev 模式）或空字符串（用户装的 PyPI wheel）。行为已经正确，**不动代码**

**验证**：
- `uv run whisper-input` 正常启动
- 设置页面开"开机自启"开关，确认写出的 plist（macOS）和 .desktop（Linux）内容正确——特别是 `ProgramArguments` / `Exec=` 指向 dev venv 的 `whisper-input` 可执行文件
- `uv run ruff check .` 通过

---

### Phase 4 — 版本号 bump + pyproject 元信息补全

**目标**：把 `pyproject.toml` 补齐 PyPI 发布所需的 metadata。

**修改** `pyproject.toml`：

1. `version = "0.4.0"` → `version = "0.5.0"`

2. 新增 / 补全字段：
   ```toml
   [project]
   name = "whisper-input"
   version = "0.5.0"
   description = "跨平台语音输入工具 —— 按住快捷键说话，松开自动输入（SenseVoice ONNX 本地推理）"
   readme = "README.md"
   license = { text = "MIT" }
   authors = [
       { name = "<用户名>", email = "<用户邮箱>" },  # 问用户
   ]
   requires-python = ">=3.12.13,<3.13"
   keywords = ["speech-recognition", "voice-input", "sensevoice", "asr", "stt"]
   classifiers = [
       "Development Status :: 4 - Beta",
       "Environment :: Console",
       "Environment :: X11 Applications",
       "Environment :: MacOS X",
       "Intended Audience :: End Users/Desktop",
       "License :: OSI Approved :: MIT License",
       "Operating System :: POSIX :: Linux",
       "Operating System :: MacOS :: MacOS X",
       "Programming Language :: Python :: 3",
       "Programming Language :: Python :: 3.12",
       "Topic :: Multimedia :: Sound/Audio :: Speech",
   ]

   [project.urls]
   Homepage = "https://github.com/pkulijing/whisper-input"
   Repository = "https://github.com/pkulijing/whisper-input"
   Issues = "https://github.com/pkulijing/whisper-input/issues"
   ```

3. 保留 `[tool.uv.index]` tuna 清华源（开发时用，不会影响 PyPI 发布——`uv build` 不从 index 拉东西）

**AskUserQuestion 点**：`authors` 字段用户名 / 邮箱需要用户确认。git 配置显示 `pkuyplijing@gmail.com`，README badge 用户名是 `pkulijing` —— 以哪个为准？还是用别的？

**验证**：
- `uv build --wheel` 跑通，产出 `dist/whisper_input-0.5.0-py3-none-any.whl`
- 用 `unzip -l dist/whisper_input-0.5.0-py3-none-any.whl` 检查：应该包含 `whisper_input/` 下所有 .py + `whisper_input/assets/*` 三件套（png / desktop / yaml），**不应该** 包含 `scripts/` 或 `packaging/`（hatchling 只打 `tool.hatch.build.targets.wheel.packages` 指定的目录）
- `uv run python -c "from whisper_input.version import __version__; print(__version__)"` 应打印 `0.5.0`

---

### Phase 5 — GitHub Actions release.yml + 改造 build.yml

**目标**：把 CI 从"构建 DEB/DMG + 打 GitHub Release"改成"lint + tag 触发 PyPI publish"。

**修改** `.github/workflows/build.yml`：

- **保留** `lint` job（README badge 继续能用）
- **删除** `version-check` job
- **删除** `build-macos` job（依赖 `scripts/build.sh`）
- **删除** `build-linux` job（同上）
- **删除** `release` job（发 DMG/DEB）
- 结果：build.yml 只剩 lint job，跑在 `push` 到任意分支 + `pull_request`。可考虑把文件改名成 `ci.yml` 但 README badge 也要同步改。**先保留文件名不动，内容精简**，phase 6 做文档时再评估改名

**新增** `.github/workflows/release.yml`：

```yaml
name: Release

on:
  push:
    tags:
      - "v*"

jobs:
  build-and-publish:
    runs-on: ubuntu-latest
    environment: pypi  # PyPI trusted publisher 必需
    permissions:
      id-token: write  # OIDC
      contents: write  # 创建 GitHub Release
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4

      - name: Check tag matches pyproject version
        run: |
          TAG="${GITHUB_REF#refs/tags/v}"
          VER=$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
          if [ "$TAG" != "$VER" ]; then
            echo "tag v$TAG ≠ pyproject version $VER"
            exit 1
          fi

      - name: Build wheel + sdist
        run: uv build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1
        # 默认 trusted publishing，无需 password
        # 默认 packages-dir: dist/

      - name: Create GitHub Release
        uses: softprops/action-gh-release@v2
        with:
          files: dist/*
          generate_release_notes: true
```

**PyPI 侧配置**（**用户手动做**，本轮 commit 不动它）：

1. 注册 PyPI 账号（如果没有）
2. 在 `pypi.org/manage/account/publishing/` 添加 pending publisher：
   - PyPI Project Name: `whisper-input`
   - Owner: `pkulijing`（GitHub 用户名，待用户确认）
   - Repository: `whisper-input`
   - Workflow filename: `release.yml`
   - Environment name: `pypi`
3. GitHub 仓库 Settings → Environments → New environment `pypi`（可不加保护规则）

**验证**：
- 本地 `act` 模拟或者跑一次 workflow_dispatch dry run（先别 push tag）
- 真发布时：`git tag v0.5.0 && git push --tags` → Actions 页面看到 release workflow 跑通 → `pip install whisper-input==0.5.0` 能装到
- 也可以先走 TestPyPI：在 workflow 里临时加 `repository-url: https://test.pypi.org/legacy/` 过一遍 dry run，再删掉改回 pypi.org

---

### Phase 6 — README + CLAUDE.md 文档同步

**目标**：文档反映 PyPI 路线，删掉 DEB/DMG 引用，加用户装和发版两套流程。

**修改** `README.md`（大改）：

- **保留**：开头的项目简介、功能特性、已知限制、使用方法
- **删除**：
  - "下载安装包" 章节（指向 Releases 页面的 DMG/DEB）
  - "DEB 安装包" 章节（`bash scripts/build.sh`）
  - "发版流程（维护者）" 原文（讲的是 master push → DMG/DEB build）
- **新增 / 重写**：
  - **"安装"章节**（替代"下载安装包"）：
    ```markdown
    ## 安装

    ### 推荐：从 PyPI 安装

    **macOS**：
    ```bash
    uv tool install whisper-input
    # 或 pipx install whisper-input
    ```

    **Linux**（Ubuntu 24.04+ / Debian 13+）：
    ```bash
    # 先装系统依赖
    sudo apt install xdotool xclip pulseaudio-utils libportaudio2 libgirepository-2.0-dev
    # 把自己加进 input 组（evdev 需要）
    sudo usermod -aG input $USER && newgrp input

    uv tool install whisper-input
    ```

    首次运行 `whisper-input` 会自动从 ModelScope 拉 ~231 MB 的 SenseVoice ONNX 模型
    到 `~/.cache/modelscope/hub/`，之后永久离线。

    ### 从源码安装（贡献者）

    ```bash
    git clone https://github.com/pkulijing/whisper-input
    cd whisper-input
    bash scripts/setup_macos.sh   # or setup_linux.sh
    uv run whisper-input
    ```
    ```
  - **"首次运行授权"章节** 保留 macOS 辅助功能 / 麦克风说明，**显式指出**：PyPI tool 装的 whisper-input 实际运行进程是 `~/.local/share/uv/tools/whisper-input/bin/python`（或 pipx 对应路径），macOS 系统授权对话框弹出的是这个 Python binary，不是 "whisper-input.app"，需要把 Python 二进制加入 系统设置 → 隐私与安全性 → 辅助功能 / 输入监听
  - **"发版流程"章节** 重写：
    ```markdown
    ## 发版流程（维护者）

    1. 本地 `pyproject.toml` bump `version` 字段
    2. `git commit -am "release: v0.5.1"` + push 到 master
    3. `git tag v0.5.1 && git push --tags`
    4. GitHub Actions `release.yml` 自动跑：校验 tag 和 version 一致 → `uv build` → Trusted Publishing 发到 PyPI → 创建 GitHub Release

    ### 首次发布前的一次性 PyPI 配置

    在 `pypi.org/manage/account/publishing/` 添加 pending publisher（见上面的 release.yml 流程）。
    ```

**修改** `CLAUDE.md`：

- **删除**："Build package" 章节（`bash scripts/build.sh`）
- **删除**：Commands 里的 bundle 相关描述
- **修改**："Architecture" 下 STT 段落：
  - `stt/downloader.py` / `stt/model_paths.py` 已删，从模块列表移除
  - 改成："模型由 `modelscope.snapshot_download` 从 ModelScope 拉到 `~/.cache/modelscope/hub/`，首次几十秒，之后永久离线"
- **修改**："Dependencies" 下的依赖表：加一行 `modelscope` (~36 MB)
- **修改**："Key Technical Decisions" 中的 "Model distribution via ModelScope direct download" → 改成走 `modelscope` 库
- **修改**："Upgrading the SenseVoice model" 章节：
  - 旧流程是改 `MODEL_VERSION` + 每个文件的 SHA256
  - 新流程："`stt/sense_voice.py` 的 `snapshot_download(..., revision=...)` 改成新 revision（默认 'master' 取最新），测试一下推理结果没回归就行"
- **新增**："Distribution" 一节，讲 PyPI tool install + 发版流程

**验证**：README / CLAUDE.md 人眼通读一遍，所有链接和路径都对得上仓库现状。

---

## 需要用户决策的开放问题

1. **authors 字段**：pyproject.toml 的 `authors` 用什么 name / email？
   - git user.email = `pkuyplijing@gmail.com`
   - README badge / GitHub owner = `pkulijing`
   - 需要用户明确："name" 填什么（英文名？中文名？GitHub handle？），email 用 `pkuyplijing@gmail.com` 吗
2. **GitHub Environments 的 `pypi` 环境是否加 protection rules**：比如要求 reviewer approval 才能 publish？对个人项目通常不加，但可以讨论
3. **首次发布是否先走 TestPyPI dry run**：workflow 里临时指向 `test.pypi.org` 过一遍确认链路没问题，再改回 pypi.org。用户是否要这层稳妥性
4. **macOS Info.plist 的 NSMicrophoneUsageDescription 对 PyPI tool 无效的问题**：PyPI 装不经过 bundle Info.plist，macOS 系统级授权对话框的文案会是默认的英文 "python wants to access the microphone" 而不是我们写的中文描述。这是 PyPI 路线的**固有局限**，本轮只在 README 里提一下，不修。未来如果做 thin `.app` wrapper 会解决，但那是 15 轮之后的事

## 删除文件清单（汇总）

```
packaging/                                    (整目录,~10 个文件)
scripts/build.sh
scripts/run_macos.sh
src/whisper_input/stt/downloader.py
src/whisper_input/stt/model_paths.py
```

## 修改文件清单（汇总）

```
pyproject.toml                                (phase 1: 加 modelscope; phase 4: 补 metadata)
src/whisper_input/stt/sense_voice.py          (phase 1)
src/whisper_input/stt/__init__.py             (phase 1: docstring 瘦身)
src/whisper_input/backends/autostart_macos.py (phase 3: 删 bundle 分支)
src/whisper_input/backends/autostart_linux.py (phase 3: 删 /usr/share 分支)
src/whisper_input/assets/whisper-input.desktop (phase 3: Exec 改)
src/whisper_input/config_manager.py           (phase 3: stale 注释)
.github/workflows/build.yml                   (phase 5: 精简为 lint only)
.github/workflows/release.yml                 (phase 5: 新增)
README.md                                     (phase 6: 大改)
CLAUDE.md                                     (phase 6)
docs/14-PyPI分发/PROMPT.md                    (已有)
docs/14-PyPI分发/PLAN.md                      (从 plan file sync,执行第一步)
docs/14-PyPI分发/SUMMARY.md                   (收尾时写)
```

## 端到端验证计划

**本地（执行期每个 phase 结束）**：
- `uv run ruff check .` 通过
- `uv run whisper-input` 能启动，完整跑一次 热键 → 录音 → 识别 → 粘贴 链路
- 设置页面 "开机自启" 开关打开/关闭，检查写出的 plist / .desktop 内容

**PyPI 发布链路（phase 5 验证 + 整轮收尾）**：
1. `uv build --wheel` 本地产出 `dist/whisper_input-0.5.0-py3-none-any.whl`
2. 在干净 venv 里 `uv tool install --from ./dist/whisper_input-0.5.0-py3-none-any.whl whisper-input` 装完
3. 新环境运行 `whisper-input`，走完完整链路（包括首次模型下载）
4. 推 tag 触发 GitHub Actions release.yml，看到 Actions 页面绿 + PyPI 页面出现 0.5.0
5. **全新机器**（或 fresh venv）`uv tool install whisper-input` 直接从 pypi.org 拉能跑

## 关键文件引用（阶段实施时参考）

| 文件 | 用途 |
|---|---|
| `src/whisper_input/stt/sense_voice.py:65-114` | `load()` 方法，phase 1 重写模型定位段 |
| `src/whisper_input/backends/autostart_macos.py:13-45` | `_bundle_trampoline()` + `_program_arguments()`，phase 3 瘦身 |
| `src/whisper_input/backends/autostart_linux.py:14-23` | `_load_desktop_template()` + `SYSTEM_DESKTOP`，phase 3 瘦身 |
| `src/whisper_input/config_manager.py:234-238` | stale "ghproxy 160MB" 注释位置，phase 3 修 |
| `src/whisper_input/assets/whisper-input.desktop:10` | `Exec=/usr/bin/whisper-input` → `Exec=whisper-input` |
| `pyproject.toml:3` | `version = "0.4.0"` → `"0.5.0"` |
| `pyproject.toml:7-26` | dependencies 数组，phase 1 加 modelscope |
| `.github/workflows/build.yml` | phase 5 精简为只剩 lint job |
