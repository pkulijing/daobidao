# Whisper Input

Linux 语音输入工具 —— 按住快捷键说话，松开后自动将识别结果输入到当前焦点窗口。

使用本地 [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) 模型，无需联网，支持中英日韩粤语混合识别。

## 功能特性

- 本地语音识别，离线可用
- 中英文等多语种混合输入
- 可配置快捷键（支持区分左右修饰键）
- 浏览器设置界面 + 系统托盘
- 支持开机自启动
- 提供 DEB 安装包

## 系统要求

- Ubuntu / Debian（X11 桌面环境）
- Python 3.12+
- NVIDIA GPU（推荐，CPU 也可运行）
- [uv](https://docs.astral.sh/uv/) 包管理器

## 快速开始

### 方式一：脚本安装

```bash
git clone <repo-url>
cd whisper-input
bash setup.sh
```

`setup.sh` 会自动检查并安装系统依赖（xdotool、xclip、libportaudio2 等），将当前用户加入 `input` 组，并通过 `uv sync` 安装 Python 依赖。

### 方式二：DEB 安装包

```bash
bash build_deb.sh
sudo dpkg -i build/deb/whisper-input_0.1.0.deb
sudo apt-get -f install  # 补全系统依赖
```

安装后可在应用菜单中找到 Whisper Input。

### 运行

```bash
# 需要 input 组权限读取键盘设备（setup.sh 已处理，重新登录后生效）
uv run python main.py

# 或用 sudo 临时运行
sudo uv run python main.py

# 指定快捷键
uv run python main.py -k KEY_RIGHTALT

# 更多选项
uv run python main.py --help
```

启动后会自动打开浏览器设置页面，也可通过系统托盘图标访问。

## 使用方法

1. 启动程序后，按住快捷键（默认右 Ctrl）开始录音
2. 对着麦克风说话
3. 松开快捷键，等待识别完成
4. 识别结果自动输入到当前光标位置

## 配置

配置文件 `config.yaml`，也可通过浏览器设置界面修改：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `hotkey` | 触发快捷键 | `KEY_RIGHTCTRL` |
| `sensevoice.device` | 推理设备 | `cuda` |
| `sensevoice.language` | 识别语种（auto 自动检测） | `auto` |
| `input_method` | 输入方式 | `clipboard` |
| `sound.enabled` | 录音提示音 | `false` |

支持的快捷键：`KEY_RIGHTCTRL`、`KEY_LEFTCTRL`、`KEY_RIGHTALT`、`KEY_LEFTALT`、`KEY_CAPSLOCK`、`KEY_F1`~`KEY_F12` 等。

## 已知限制

- 仅支持 X11，暂不支持 Wayland
- Super/Win 键在 GNOME 下会被桌面拦截，不建议使用
- 首次运行需下载 SenseVoice 模型（约 500MB）
- 首次 DEB 安装需下载 PyTorch（约 2GB）

## 技术架构

```
按住快捷键 → HotkeyListener (evdev) → AudioRecorder (sounddevice)
松开快捷键 → SenseVoiceSTT (FunASR) → InputMethod (clipboard + xdotool)
                                       → 文本输入到焦点窗口
```

- **evdev** 直接读取键盘硬件事件，可区分左右修饰键
- **剪贴板粘贴**而非 xdotool 模拟按键，避免中文输入乱码
- 修饰键按下后有 300ms 延迟，用于区分组合键（如 Ctrl+C）和单独触发

## License

MIT
