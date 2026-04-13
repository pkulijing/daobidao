# Linux 安装体验优化

## 背景

在 `8-GitHub CI与版本管理` 的实施过程中，审视 `debian/postinst` 时发现当前 Linux 安装体验存在多个不舒服的设计：

1. **deb 的 postinst 里 curl 装 uv 是反模式**
   - 主流发行版 policy 不鼓励 postinst 从网络 fetch 不受签名保护的 shell 脚本执行
   - `curl -LsSf https://astral.sh/uv/install.sh | sh` 这种模式放在 `sudo apt install` 的动作链里，相当于让 apt 触发了无签名的任意代码执行
   - 对安全意识强的用户不友好

2. **postinst 里跑 `uv sync` + 预下载模型会拉几 GB 数据**
   - `uv sync` 会下载 PyTorch（CUDA 版约 2GB），funasr 等依赖
   - 然后预下载 SenseVoice-Small 模型约 500MB
   - 导致 `apt install whisper-input.deb` 命令可能卡 5-10 分钟
   - 这完全违反 deb 语境下用户"秒级完成安装"的预期

3. **失败处理复杂**
   - postinst 失败会让 apt 把包标成"已解压但配置失败"的半装状态
   - 恢复需要 `sudo dpkg --configure -a` 或重新安装，对普通用户不友好

4. **GUI 前端装 deb 的场景下 stdout 用户看不到**
   - Discover、GNOME Software 这些图形前端装包时，postinst 的进度输出用户完全看不到
   - 用户以为卡死，实际在后台下载 torch
   - curl 的进度条在这种场景下毫无意义

## 核心判断

Linux 用户大概率是开发者，预装 `uv` 对他们不是负担。当前方案用 postinst 自动 curl 装 uv 是"照顾非开发者"的过度设计，代价是引入了反模式和极差的体验。

## 需求

改造 `debian/postinst` 和相关打包逻辑，让 `apt install whisper-input.deb` 变成一个**秒级完成的纯文件分发动作**：

### 目标行为

| 环节 | 现在 | 目标 |
|---|---|---|
| uv | postinst 自动 curl 装 | README 要求预装，postinst 检测不到则**明确报错退出**（不再 curl） |
| Python 依赖（torch/funasr 等） | postinst 里 `uv sync` | 延迟到**首次启动时**由 `/usr/bin/whisper-input` launcher 处理（现有的兜底路径就是这个，只是被 postinst 抢跑了） |
| SenseVoice 模型 | postinst 预下载 500MB | 延迟到**首次启动时**，或更晚，延迟到**第一次录音时** |
| apt install 时长 | 5-10 分钟 | < 5 秒 |
| 失败场景 | postinst 挂掉 = 包半装 | postinst 只做轻量工作（复制文件、加 input 组），几乎不会失败 |

### postinst 应该保留的工作

- 把 `$SUDO_USER` 加到 `input` 组（读 `/dev/input/*` 需要）
- `gtk-update-icon-cache` 刷新图标缓存
- `update-desktop-database` 刷新 desktop 数据库
- 友好的安装完成提示（告诉用户要注销重登 + 需要预装 uv + 首次启动会下载依赖）

### postinst 应该移除的工作

- curl 装 uv 的整段逻辑
- `uv sync` 整段
- SenseVoice 模型预下载整段

### launcher `/usr/bin/whisper-input` 需要加强

- 启动时先检查 `uv` 是否在 PATH，不在则 `notify-send` 报错 + 终端输出指引
- 首次启动时感知 venv 不存在（这段逻辑已经存在），用 `notify-send` 提示"正在安装依赖，请稍候（约 5-10 分钟，需下载 PyTorch）"
- `uv sync` 完成后再给一个 notify "安装完成"
- 然后正常启动

### README 需要更新

- "系统要求 > Linux" 加一行明确要求：**需先安装 uv**（给出官方一行 curl 命令或 `pipx install uv`）
- "下载安装包 > Linux" 下加一段说明：首次启动会自动下载依赖（torch + funasr + 模型，约 2.5GB），需要联网和耐心

## 验证

装好后的体验应该是：

1. `sudo apt install ./whisper-input_0.3.x.deb` → 几秒钟完成
2. 注销重登（为了 input 组生效）
3. 应用菜单里点 Whisper Input
4. 首次启动弹 notify "正在安装依赖..."
5. 等 5-10 分钟
6. 弹 notify "安装完成"，程序正常启动进托盘

## 本次不做

- 不提供离线安装包（不把 torch 打进 deb，deb 体积会爆炸）
- 不签 deb 包（没有 GPG 密钥基础设施）
- 不适配非 apt 系（yum/pacman 暂不支持）
- 不改 CI workflow（8 那轮刚搭好，这轮只改打包逻辑，CI 会自动验证 deb 依赖可解析）

## 环境准备

需要在 Ubuntu 24.04+ 机器上验证，当前工作机是 macOS 无法直接测试。
