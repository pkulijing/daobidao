# 改名为 daobidao

## 背景

当前项目名 `whisper-input` 辨识度低：

- 几乎所有"本地语音输入工具"的开发者都会想叫这个名字，PyPI、GitHub 上一搜一大把
- 名字里的 "Whisper" 容易让人误以为我们用的是 OpenAI Whisper 模型；实际现在跑的是阿里云的 Qwen3-ASR（早期是 SenseVoice）
- 中文用户群体看到 "Whisper Input" 也不会立刻 get 到这是干啥的

## 目标

把项目改名为 **`daobidao`**（叨逼叨，中文口语里"说个不停"的意思，正好契合语音输入工具的定位），让中文用户一眼就懂、辨识度拉满。

## 改名策略

走「发新包 + 老包变 shim」路线（开源圈"改名"的标准操作），不走"删了重来"：

1. PyPI 上注册新包 `daobidao`，作为今后真正的发布目标
2. 老包 `whisper-input` 后续版本改造成薄壳：`Requires-Dist: daobidao`，console script 调用时打一行迁移提示
3. 老用户 `pip install -U whisper-input` 仍可拿到新版（间接装上 daobidao），平滑过渡
4. README 顶部贴改名公告

## 范围

**需要改：**

- Python 包名：`whisper_input` → `daobidao`（import 路径全部跟着换）
- PyPI 项目名：`whisper-input` → `daobidao`（pyproject.toml）
- console script 名：`whisper-input` → `daobidao`
- 资源文件名：`whisper-input.png` / `whisper-input.desktop` 等
- macOS 应用名 / Bundle ID：`Whisper Input` / `com.whisperinput.*` 走改名
- 所有非 docs/ 下的文档（README、README.zh-CN、BACKLOG、CLAUDE.md 等）
- 安装脚本 / setup 脚本里出现的命令名、路径名
- CI workflow 里跟包名相关的部分
- `whisper-input` 老包改造成 shim（一个最小转发包）

**不改：**

- `docs/` 下**历史开发记录**（每轮的 `PROMPT.md` / `PLAN.md` / `SUMMARY.md` / 补充文档），保留原貌不回填
- `uv.lock` 不手动改，重新 `uv sync` 生成

**注意：`docs/` 下**活的索引/总览文档**仍然要改**，例如：

- `docs/DEVTREE.md`（开发树根节点 / 文字描述里的项目名）
- 本轮自己的 `docs/29-改名为daobidao/` 内容（PROMPT/PLAN/SUMMARY 用新名字写）

## 不在本轮范围

- 项目 logo / icon 视觉重做（保持现有图标）
- 域名 / 网站建设
- GitHub 仓库重命名（人类自行操作，本轮不涉及）

## 交付

- 新包名下完整可跑、测试全过、ruff 无错
- shim 包目录（用于 `whisper-input` 后续小版本发布）
- README 顶部贴改名公告
- 发布流程文档（CLAUDE.md / SUMMARY.md）讲清楚两个包怎么联动发布
