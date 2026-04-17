#!/bin/bash
# 本地构建 + 安装（macOS），测试 uv tool install 完整流程。
#
# 场景：调试 TCC / launcher / init / uninstall 等需要真实 uv tool 环境的功能时，
# 避免反复发 alpha/beta 到 PyPI 再等 CI。直接本地构建 wheel 并 uv tool install。
#
# 清理（模拟全新用户）：
#   - ~/Applications/Whisper Input.app
#   - ~/Library/LaunchAgents/com.whisper-input.plist
#   - TCC 授权（Accessibility + ListenEvent）
#   - uv tool 下的 whisper-input 包
# 保留（避免重复下载）：
#   - ~/Library/Application Support/Whisper Input/（配置）
#   - ~/.cache/modelscope/...（模型，~231 MB）
#   如需一并清理，用 --wipe-all。

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

WIPE_ALL=0
if [[ "${1:-}" == "--wipe-all" ]]; then
    WIPE_ALL=1
fi

if [[ "$(uname)" != "Darwin" ]]; then
    echo "本脚本针对 macOS。Linux 下直接 uv tool install . --force --compile-bytecode"
    exit 1
fi

APP_BUNDLE="$HOME/Applications/Whisper Input.app"
LAUNCH_AGENT="$HOME/Library/LaunchAgents/com.whisper-input.plist"
VENV_PATH_FILE="$HOME/.config/whisper-input/venv-path"
CONFIG_DIR="$HOME/Library/Application Support/Whisper Input"
BUNDLE_ID="com.whisper-input.app"

echo "=========================================="
echo "  本地构建 + 安装 whisper-input"
echo "=========================================="

# ── 1. 清理 ───────────────────────────────────────
echo ""
echo "[1/4] 清理旧版本..."

if [ -f "$LAUNCH_AGENT" ]; then
    launchctl bootout "gui/$(id -u)/com.whisper-input" 2>/dev/null || true
    rm -f "$LAUNCH_AGENT"
    echo "  ✓ LaunchAgent"
fi

if [ -d "$APP_BUNDLE" ]; then
    rm -rf "$APP_BUNDLE"
    echo "  ✓ .app bundle"
fi

tccutil reset Accessibility "$BUNDLE_ID" >/dev/null 2>&1 || true
tccutil reset ListenEvent "$BUNDLE_ID" >/dev/null 2>&1 || true
echo "  ✓ TCC 授权"

rm -f "$VENV_PATH_FILE"

if uv tool list 2>/dev/null | grep -q "^whisper-input"; then
    uv tool uninstall whisper-input >/dev/null 2>&1
    echo "  ✓ uv tool 包"
fi

if [[ "$WIPE_ALL" == "1" ]]; then
    if [ -d "$CONFIG_DIR" ]; then
        rm -rf "$CONFIG_DIR"
        echo "  ✓ 配置文件"
    fi
    rm -rf "$HOME/.cache/modelscope/hub/models/iic/SenseVoiceSmall-onnx" \
           "$HOME/.cache/modelscope/hub/models/iic/SenseVoiceSmall" \
           "$HOME/.cache/modelscope/hub/iic/SenseVoiceSmall-onnx" \
           "$HOME/.cache/modelscope/hub/iic/SenseVoiceSmall"
    echo "  ✓ 模型缓存"
fi

# ── 2. 构建 launcher ──────────────────────────────
echo ""
echo "[2/4] 构建 launcher..."
bash launcher/macos/build.sh

# ── 3. 构建 wheel ─────────────────────────────────
echo ""
echo "[3/4] 构建 wheel..."
rm -rf dist/
uv build
WHEEL=$(ls dist/whisper_input-*.whl)
echo "  $WHEEL"

# ── 4. uv tool install ────────────────────────────
echo ""
echo "[4/4] uv tool install..."
uv tool install --compile-bytecode "$WHEEL"

echo ""
echo "=========================================="
echo "  完成"
echo "=========================================="
echo ""
echo "下一步："
echo "  whisper-input --init     # 安装 .app bundle + 下载模型"
echo "  whisper-input            # 正常启动"
echo ""
echo "完全重新来一次（连配置和模型也清）："
echo "  bash $0 --wipe-all"
