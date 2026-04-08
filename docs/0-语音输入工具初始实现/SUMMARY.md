# 开发总结：Linux 语音输入工具

## 开发项背景

Ubuntu 上缺少类似 Mac 微信输入法的语音输入体验。调研后确认无现成好方案（微信/豆包/搜狗均无 Linux 语音模块），决定自行实现。

## 实现方案

### 关键设计

- **双引擎架构**：本地 SenseVoice-Small（中文准确度最佳、速度极快）+ 云端豆包 ASR，通过配置文件和系统托盘切换
- **evdev 热键监听**：直接读取键盘设备事件，可区分左右修饰键，突破普通键盘库的限制
- **剪贴板粘贴输入**：避免 xdotool type 对 CJK 字符的兼容问题，保存并恢复原剪贴板内容

### 开发内容概括

| 模块 | 功能 |
|---|---|
| main.py | 主入口、配置加载、系统托盘、模块串联 |
| hotkey.py | evdev 键盘事件监听，支持区分左右修饰键 |
| recorder.py | sounddevice 音频录制，输出 16kHz WAV |
| stt_sensevoice.py | 本地 SenseVoice-Small 语音识别 |
| stt_doubao.py | 火山引擎 WebSocket ASR |
| input_method.py | 剪贴板 + xdotool 粘贴输入 |
| config.yaml | 全局配置文件 |
| setup.sh | 一键安装脚本 |

### 额外产物

- 配置文件模板 config.yaml
- 安装脚本 setup.sh

## 局限性

- 需要 root 权限或 input 组权限读取键盘设备
- Fn 键无法被捕获（键盘固件拦截）
- 仅支持 X11，Wayland 需要替换 xdotool/xclip 为 wtype/wl-clipboard
- 豆包 ASR 的 WebSocket 协议实现基于公开文档，可能需要根据实际 API 调整

## 后续 TODO

- [ ] 实际运行测试并调试
- [ ] Wayland 支持（wtype + wl-clipboard）
- [ ] 流式识别（边说边出字）
- [ ] 添加 LLM 后处理（标点优化、格式化）
- [ ] systemd service 自启动
