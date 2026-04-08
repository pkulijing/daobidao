#!/bin/bash
# Whisper Input 安装脚本

set -e

echo "=== Whisper Input 安装 ==="
echo ""

# 检查系统依赖
echo "1. 检查系统依赖..."
MISSING=""
for cmd in xdotool xclip paplay; do
    if ! command -v $cmd &>/dev/null; then
        MISSING="$MISSING $cmd"
    fi
done
if ! ldconfig -p 2>/dev/null | grep -q libportaudio; then
    MISSING="$MISSING libportaudio2"
fi

if [ -n "$MISSING" ]; then
    echo "   安装缺少的系统依赖:$MISSING"
    sudo apt-get update
    sudo apt-get install -y xdotool xclip pulseaudio-utils libportaudio2
else
    echo "   系统依赖已满足 ✓"
fi

# 检查用户是否在 input 组（evdev 需要）
echo ""
echo "2. 检查 input 组权限..."
if groups | grep -qw input; then
    echo "   用户已在 input 组 ✓"
else
    echo "   将用户加入 input 组..."
    sudo usermod -aG input $USER
    newgrp input
fi

# 检查 uv
echo ""
echo "3. 检查 uv..."
if ! command -v uv &>/dev/null; then
    echo "   uv 未安装，请先安装: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
else
    echo "   uv $(uv --version) ✓"
fi

# 安装 Python 依赖
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo ""
echo "4. 使用 uv 安装 Python 依赖..."
cd "$SCRIPT_DIR"

# index 已在 pyproject.toml 中配置（清华镜像 + PyTorch CUDA）
uv sync

echo ""
echo "=== 安装完成 ==="
echo ""
echo "使用方法:"
echo "  cd $SCRIPT_DIR"
echo ""
echo "  # 运行（需要 sudo 或 input 组权限读取键盘设备）"
echo "  sudo $(which uv) run python main.py"
echo ""
echo "  # 或使用 input 组权限（重新登录后）"
echo "  uv run python main.py"
echo ""
echo "  # 指定引擎和热键"
echo "  uv run python main.py -e sensevoice -k KEY_RIGHTALT"
echo ""
echo "  # 使用豆包云端（需先配置 .env.local 中的凭证）"
echo "  uv run python main.py -e doubao"
