"""macOS .app bundle 管理 — 解决 TCC 权限归属问题。

通过生成一个包含原生 launcher 的 .app bundle，让 macOS TCC 系统
将权限归属于 "Whisper Input" 而非 "python3.12"。

launcher（预编译的 universal binary，随 wheel 分发）在自身进程内
dlopen(libpython) 运行 whisper_input，因此 TCC 看到的进程二进制
始终是我们的 launcher，不是 Python。
"""

import importlib.resources
import os
import stat
import subprocess
import sys

# .app bundle 安装位置
APP_NAME = "Whisper Input"
APP_BUNDLE_NAME = f"{APP_NAME}.app"
APP_INSTALL_DIR = os.path.expanduser("~/Applications")
APP_BUNDLE_PATH = os.path.join(APP_INSTALL_DIR, APP_BUNDLE_NAME)

# launcher 配置
CONFIG_DIR = os.path.expanduser("~/.config/whisper-input")
VENV_PATH_FILE = os.path.join(CONFIG_DIR, "venv-path")

# bundle 标识
BUNDLE_ID = "com.whisper-input.app"

# 环境变量：标识当前进程由 bundle launcher 启动
BUNDLE_ENV_KEY = "_WHISPER_INPUT_BUNDLE"


def get_app_bundle_path() -> str:
    """返回 .app bundle 路径。"""
    return APP_BUNDLE_PATH


def is_app_bundle_installed() -> bool:
    """检查 .app bundle 是否已安装且完好。"""
    exe = os.path.join(
        APP_BUNDLE_PATH, "Contents", "MacOS", "whisper-input"
    )
    plist = os.path.join(APP_BUNDLE_PATH, "Contents", "Info.plist")
    return os.path.isfile(exe) and os.path.isfile(plist)


def is_launched_from_bundle() -> bool:
    """判断当前进程是否从 .app bundle 启动。"""
    return os.environ.get(BUNDLE_ENV_KEY) == "1"


def _build_info_plist() -> str:
    """生成 Info.plist 内容。"""
    from whisper_input.version import __version__

    return f"""\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleDisplayName</key>
    <string>{APP_NAME}</string>
    <key>CFBundleIdentifier</key>
    <string>{BUNDLE_ID}</string>
    <key>CFBundleVersion</key>
    <string>{__version__}</string>
    <key>CFBundleShortVersionString</key>
    <string>{__version__}</string>
    <key>CFBundleExecutable</key>
    <string>whisper-input</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>Whisper Input 需要使用麦克风来进行语音识别。</string>
</dict>
</plist>
"""


def _get_prebuilt_assets():
    """获取预编译的 launcher binary 和 icns 图标。

    返回 (launcher_ref, icns_ref)，均为 importlib.resources
    的 Traversable 对象。
    """
    macos_assets = importlib.resources.files(
        "whisper_input.assets"
    ).joinpath("macos")
    launcher = macos_assets.joinpath("whisper-input-launcher")
    icns = macos_assets.joinpath("AppIcon.icns")
    return launcher, icns


def install_app_bundle() -> str:
    """生成/更新 .app bundle。返回安装路径。

    使用随 wheel 分发的预编译 universal binary，
    不需要 Xcode Command Line Tools。
    """
    print("[install-app] 开始安装 Whisper Input.app...")

    # 1. 获取预编译资源
    launcher_ref, icns_ref = _get_prebuilt_assets()

    # 2. 创建 .app 目录结构
    contents = os.path.join(APP_BUNDLE_PATH, "Contents")
    macos_dir = os.path.join(contents, "MacOS")
    resources_dir = os.path.join(contents, "Resources")

    for d in (macos_dir, resources_dir):
        os.makedirs(d, exist_ok=True)

    # 3. 复制预编译 launcher
    exe_path = os.path.join(macos_dir, "whisper-input")
    print("[install-app] 安装 launcher...")
    with open(exe_path, "wb") as out:
        out.write(launcher_ref.read_bytes())
    os.chmod(exe_path, stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP
             | stat.S_IROTH | stat.S_IXOTH)  # 755

    # 4. 写入 Info.plist
    plist_path = os.path.join(contents, "Info.plist")
    with open(plist_path, "w", encoding="utf-8") as f:
        f.write(_build_info_plist())

    # 5. 复制预生成图标
    print("[install-app] 安装图标...")
    icns_path = os.path.join(resources_dir, "AppIcon.icns")
    with open(icns_path, "wb") as out:
        out.write(icns_ref.read_bytes())

    # 6. Ad-hoc 签名
    print("[install-app] 签名...")
    subprocess.run(
        ["codesign", "--force", "--sign", "-", "--deep",
         APP_BUNDLE_PATH],
        capture_output=True,
    )

    # 7. 保存 venv 路径
    _save_venv_path()

    print(f"[install-app] 安装完成: {APP_BUNDLE_PATH}")
    return APP_BUNDLE_PATH


def _save_venv_path() -> None:
    """将当前 venv 的 sys.prefix 保存到配置文件。"""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(VENV_PATH_FILE, "w", encoding="utf-8") as f:
        f.write(sys.prefix + "\n")


def update_venv_path() -> None:
    """更新已保存的 venv 路径（用于 uv tool upgrade 后）。"""
    if os.path.isfile(VENV_PATH_FILE):
        _save_venv_path()


def launch_via_bundle(extra_args: list[str] | None = None) -> None:
    """通过 open -a 启动 .app bundle，当前进程退出。"""
    cmd = ["/usr/bin/open", "-a", APP_BUNDLE_PATH]
    if extra_args:
        cmd += ["--args", *extra_args]
    subprocess.Popen(cmd)
    sys.exit(0)


def restart_via_bundle() -> None:
    """在 bundle 模式下重启应用。"""
    subprocess.Popen(["/usr/bin/open", "-a", APP_BUNDLE_PATH])
    # 给 open 一点时间启动新进程
    import time
    time.sleep(0.5)
    os._exit(0)
