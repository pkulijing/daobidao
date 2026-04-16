#!/bin/bash
# 编译 Whisper Input macOS launcher (universal binary: arm64 + x86_64)
#
# 产物输出到 src/whisper_input/assets/macos/，随 wheel 分发。
# CI 和本地开发均使用此脚本。
#
# 用法: bash launcher/macos/build.sh
# 需要: Xcode Command Line Tools (clang)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SRC="$SCRIPT_DIR/main.m"
OUT_DIR="$REPO_ROOT/src/whisper_input/assets/macos"
OUT="$OUT_DIR/whisper-input-launcher"

mkdir -p "$OUT_DIR"

echo "[build] 编译 arm64..."
clang -o "${OUT}-arm64" "$SRC" \
    -framework Cocoa -ldl -fobjc-arc -O2 -arch arm64

echo "[build] 编译 x86_64..."
clang -o "${OUT}-x86_64" "$SRC" \
    -framework Cocoa -ldl -fobjc-arc -O2 -arch x86_64

echo "[build] 合成 universal binary..."
lipo -create "${OUT}-arm64" "${OUT}-x86_64" -output "$OUT"
rm "${OUT}-arm64" "${OUT}-x86_64"

echo "[build] 完成: $OUT ($(du -h "$OUT" | cut -f1))"
file "$OUT"

# 同时生成 icns 图标
PNG="$REPO_ROOT/src/whisper_input/assets/whisper-input.png"
ICNS="$OUT_DIR/AppIcon.icns"
if [ -f "$PNG" ]; then
    echo "[build] 生成 AppIcon.icns..."
    ICONSET=$(mktemp -d)/AppIcon.iconset
    mkdir -p "$ICONSET"
    for SIZE in 16 32 64 128 256 512; do
        sips -z $SIZE $SIZE "$PNG" --out "$ICONSET/icon_${SIZE}x${SIZE}.png" >/dev/null 2>&1
    done
    for SIZE in 16 32 128 256 512; do
        S2=$((SIZE * 2))
        sips -z $S2 $S2 "$PNG" --out "$ICONSET/icon_${SIZE}x${SIZE}@2x.png" >/dev/null 2>&1
    done
    iconutil -c icns "$ICONSET" -o "$ICNS"
    rm -rf "$(dirname "$ICONSET")"
    echo "[build] 图标完成: $ICNS"
fi
