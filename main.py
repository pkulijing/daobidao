#!/usr/bin/env python3
"""Whisper Input - Linux 语音输入工具

按住快捷键说话，松开后自动将语音识别结果输入到当前焦点窗口。
支持中英文混合输入，使用本地 SenseVoice 模型。

用法:
    python main.py                    # 使用默认配置
    python main.py -k KEY_RIGHTALT    # 使用右Alt键
"""

import argparse
import contextlib
import os
import signal
import subprocess
import sys
import threading

import yaml

from hotkey import HotkeyListener
from input_method import type_text
from recorder import AudioRecorder


def load_config(config_path: str | None = None) -> dict:
    """加载配置文件。"""
    if config_path is None:
        config_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "config.yaml"
        )
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_stt_engine(config: dict):
    """根据配置创建 STT 引擎。"""
    engine = config.get("engine", "sensevoice")

    if engine == "sensevoice":
        from stt_sensevoice import SenseVoiceSTT

        sv_config = config.get("sensevoice", {})
        return SenseVoiceSTT(
            model=sv_config.get("model", "iic/SenseVoiceSmall"),
            device=sv_config.get("device", "cuda"),
            language=sv_config.get("language", "auto"),
        )
    else:
        raise ValueError(f"未知的 STT 引擎: {engine}")


def play_sound(path: str) -> None:
    """播放提示音。"""
    if path and os.path.exists(path):
        with contextlib.suppress(FileNotFoundError):
            subprocess.Popen(
                ["paplay", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )


class WhisperInput:
    """语音输入主控制器。"""

    def __init__(self, config: dict):
        self.config = config
        self.recorder = AudioRecorder(
            sample_rate=config.get("audio", {}).get("sample_rate", 16000),
            channels=config.get("audio", {}).get("channels", 1),
        )
        self.stt = create_stt_engine(config)
        self.input_method = config.get("input_method", "clipboard")
        self.sound_enabled = config.get("sound", {}).get("enabled", True)
        self.sound_start = config.get("sound", {}).get("start", "")
        self.sound_stop = config.get("sound", {}).get("stop", "")
        self._processing = False

    def on_key_press(self) -> None:
        """热键按下 - 开始录音。"""
        if self._processing:
            return
        print("[main] 🎤 开始录音...")
        if self.sound_enabled:
            play_sound(self.sound_start)
        self.recorder.start()

    def on_key_release(self) -> None:
        """热键释放 - 停止录音并识别。"""
        if not self.recorder.is_recording:
            return
        print("[main] ⏹ 停止录音，识别中...")
        if self.sound_enabled:
            play_sound(self.sound_stop)

        wav_data = self.recorder.stop()
        if not wav_data:
            print("[main] 未录到音频")
            return

        # 在后台线程中处理识别，避免阻塞热键监听
        self._processing = True
        threading.Thread(
            target=self._process, args=(wav_data,), daemon=True
        ).start()

    def _process(self, wav_data: bytes) -> None:
        """处理识别和输入（在后台线程中运行）。"""
        try:
            text = self.stt.transcribe(wav_data)
            if text:
                print(f"[main] ✅ 识别结果: {text}")
                type_text(text, method=self.input_method)
            else:
                print("[main] 未识别到文字")
        except Exception as e:
            print(f"[main] 识别失败: {e}")
        finally:
            self._processing = False

    def preload_model(self) -> None:
        """预加载模型（仅本地引擎需要）。"""
        if self.config.get("engine") == "sensevoice":
            cache_dir = os.environ.get(
                "MODELSCOPE_CACHE", "~/.cache/modelscope/hub"
            )
            print(f"[main] 预加载 SenseVoice 模型 (模型缓存目录: {cache_dir})")
            print(
                "[main] 首次运行会从 ModelScope 下载模型，可通过 MODELSCOPE_CACHE 环境变量修改下载目录"
            )
            self.stt._ensure_model()


def run_tray(wi: WhisperInput, config: dict) -> None:
    """运行系统托盘图标（可选）。"""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("[main] pystray/Pillow 未安装，跳过系统托盘")
        return

    def create_icon(color: str = "green") -> Image.Image:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        colors = {"green": "#4CAF50", "red": "#F44336", "gray": "#9E9E9E"}
        fill = colors.get(color, colors["green"])
        draw.ellipse([8, 8, 56, 56], fill=fill)
        # 麦克风图标（简化为矩形+半圆）
        draw.rectangle([24, 16, 40, 38], fill="white")
        draw.arc([20, 28, 44, 52], 0, 180, fill="white", width=3)
        draw.line([32, 52, 32, 58], fill="white", width=3)
        return img

    def quit_app(icon, item):
        icon.stop()
        os.kill(os.getpid(), signal.SIGTERM)

    menu = pystray.Menu(
        pystray.MenuItem("退出", quit_app),
    )

    icon = pystray.Icon("whisper-input", create_icon(), "Whisper Input", menu)
    icon.run_detached()


def main():
    parser = argparse.ArgumentParser(
        description="Whisper Input - Linux 语音输入工具"
    )
    parser.add_argument("-c", "--config", help="配置文件路径")
    parser.add_argument("-k", "--hotkey", help="热键 (如 KEY_RIGHTCTRL)")
    parser.add_argument("--no-tray", action="store_true", help="禁用系统托盘")
    parser.add_argument(
        "--no-preload", action="store_true", help="不预加载模型"
    )
    args = parser.parse_args()

    # 加载配置
    config = load_config(args.config)

    # 命令行参数覆盖配置
    if args.hotkey:
        config["hotkey"] = args.hotkey

    hotkey = config.get("hotkey", "KEY_RIGHTCTRL")
    engine = config.get("engine", "sensevoice")

    print("=" * 50)
    print("  Whisper Input - Linux 语音输入")
    print("=" * 50)
    print(f"  引擎: {engine}")
    print(f"  热键: {hotkey} (按住说话，松开输入)")
    print(f"  输入: {config.get('input_method', 'clipboard')}")
    print("=" * 50)

    # 创建主控制器
    wi = WhisperInput(config)

    # 预加载模型
    if not args.no_preload:
        wi.preload_model()

    # 启动系统托盘
    if not args.no_tray:
        run_tray(wi, config)

    # 启动热键监听
    listener = HotkeyListener(
        hotkey=hotkey,
        on_press=wi.on_key_press,
        on_release=wi.on_key_release,
    )

    # 优雅退出
    def signal_handler(sig, frame):
        print("\n[main] 正在退出...")
        listener.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    listener.start()

    print("[main] 就绪！按住热键开始说话")
    print("[main] Ctrl+C 退出")

    # 主线程等待
    signal.pause()


if __name__ == "__main__":
    main()
