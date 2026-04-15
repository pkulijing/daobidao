#!/bin/bash
# 构建 Whisper Input 安装包 - 自动检测平台
set -e

# 本脚本可从任意 CWD 调用,统一 cd 到仓库根目录再跑
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

PKG_NAME="whisper-input"
VERSION=$(grep '^version' pyproject.toml | head -1 | sed 's/.*"\(.*\)".*/\1/')
COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "")

# src layout 重构后,源树直接整棵拷贝,不再逐文件枚举。
# 需要同步进 bundle 的文件:
#   - src/whisper_input/      (所有 Python 源码 + assets 包数据)
#   - pyproject.toml          (setup_window.py 运行 uv sync 的输入)
#   - uv.lock                 (依赖锁定)
#   - .python-version         (uv 选 python 版本的依据)
# __pycache__/ 和 *.pyc 在 cp 之后统一清理。
copy_src_tree() {
    local dest="$1"
    mkdir -p "$dest"
    cp -R src "$dest/src"
    cp pyproject.toml uv.lock .python-version "$dest/"
    find "$dest/src" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
    find "$dest/src" -type f -name '*.pyc' -delete 2>/dev/null || true
    if [ -n "$COMMIT" ]; then
        echo "$COMMIT" > "$dest/src/whisper_input/_commit.txt"
    fi
}

# 图标源(assets 已经迁到 package data)
SOURCE_PNG="src/whisper_input/assets/whisper-input.png"
SOURCE_DESKTOP="src/whisper_input/assets/whisper-input.desktop"

# ========================================
# macOS 构建
# ========================================
build_macos() {
    APP_NAME="Whisper Input"
    BUILD_DIR="build/macos"
    CACHE_DIR="$BUILD_DIR/cache"
    FINAL_APP="$BUILD_DIR/$APP_NAME.app"
    # 关键:整个构建在不带 .app 后缀的 staging 目录里完成。一旦目录名带 .app,
    # macOS LaunchServices 会把它注册成 managed app,后续对 bundle 内部的写入
    # 会被 App Management 拦截报 EPERM(且时机随机,有时第一次能跑通,反复 build
    # 几次后就开始失败)。staging 阶段 LS 完全不知情,cp 全程不受干扰;最后用
    # mv 一次性改名为 .app,mv 是单次 inode rename,瞬间完成,没有窗口期。
    APP_BUNDLE="$BUILD_DIR/staging-bundle"
    DMG_NAME="WhisperInput_${VERSION}.dmg"

    echo "正在构建 $APP_NAME v$VERSION (macOS) ..."

    for cmd in sips iconutil hdiutil curl shasum tar; do
        if ! command -v $cmd &>/dev/null; then
            echo "错误: 未找到 $cmd，请在 macOS 上运行此脚本"
            exit 1
        fi
    done

    if ! command -v uv &>/dev/null; then
        echo "错误: 未找到 uv，请先安装: curl -LsSf https://astral.sh/uv/install.sh | sh"
        exit 1
    fi

    # 读取 python-build-standalone 元信息
    # shellcheck disable=SC1091
    source packaging/macos/python_dist.txt
    if [ -z "${URL:-}" ] || [ -z "${SHA256:-}" ] || [ -z "${PYTHON_VERSION:-}" ]; then
        echo "错误: packaging/macos/python_dist.txt 缺少必要字段"
        exit 1
    fi
    PY_TARBALL="$CACHE_DIR/$(basename "$URL")"
    PY_EXTRACT="$CACHE_DIR/python-${PYTHON_VERSION}"

    # 清理旧构建内容，保留 cache（python tarball 复用）
    # 注意:旧 .app 内部文件可能被 macOS LaunchServices 加了 com.apple.provenance
    # 受保护 xattr,普通 rm 会静默失败,留下半残骸,下次 cp -R 覆盖时报 EPERM。
    # 先 chmod -R u+w 强制可写,再 rm,最后校验确实删干净了。
    mkdir -p "$BUILD_DIR" "$CACHE_DIR"
    for entry in "$BUILD_DIR"/*; do
        [ -e "$entry" ] || continue
        case "$(basename "$entry")" in
            cache) continue ;;
        esac
        chmod -R u+w "$entry" 2>/dev/null || true
        rm -rf "$entry"
        if [ -e "$entry" ]; then
            echo "错误: 无法清理旧构建产物: $entry"
            echo "请手动 rm -rf 后重试,或在 系统设置 → 隐私与安全性 →"
            echo "应用程序管理 中给当前终端授权后再 build。"
            exit 1
        fi
    done

    # 1. 下载 + 校验 + 解压 python-build-standalone
    echo "[1/5] 准备 python-build-standalone $PYTHON_VERSION ..."
    if [ -f "$PY_TARBALL" ]; then
        ACTUAL=$(shasum -a 256 "$PY_TARBALL" | awk '{print $1}')
        if [ "$ACTUAL" != "$SHA256" ]; then
            echo "    cache 命中但 sha256 不匹配，重新下载"
            rm -f "$PY_TARBALL"
        else
            echo "    cache 命中: $PY_TARBALL"
        fi
    fi
    if [ ! -f "$PY_TARBALL" ]; then
        echo "    下载 $URL"
        curl -fL --progress-bar "$URL" -o "$PY_TARBALL.tmp"
        ACTUAL=$(shasum -a 256 "$PY_TARBALL.tmp" | awk '{print $1}')
        if [ "$ACTUAL" != "$SHA256" ]; then
            echo "错误: sha256 校验失败"
            echo "  期望: $SHA256"
            echo "  实际: $ACTUAL"
            rm -f "$PY_TARBALL.tmp"
            exit 1
        fi
        mv "$PY_TARBALL.tmp" "$PY_TARBALL"
    fi
    rm -rf "$PY_EXTRACT"
    mkdir -p "$PY_EXTRACT"
    tar -xzf "$PY_TARBALL" -C "$PY_EXTRACT"
    # python-build-standalone 解出来是 python/ 目录
    if [ ! -x "$PY_EXTRACT/python/bin/python3" ]; then
        echo "错误: 解压后的目录结构异常: $PY_EXTRACT"
        exit 1
    fi

    # 2. 生成 .icns 图标
    echo "[2/5] 生成应用图标 ..."
    ICONSET_DIR="$BUILD_DIR/AppIcon.iconset"
    mkdir -p "$ICONSET_DIR"

    if [ ! -f "$SOURCE_PNG" ]; then
        echo "错误: 未找到图标源文件 $SOURCE_PNG"
        echo "请先运行: uv run python scripts/generate_icon.py"
        exit 1
    fi

    for size in 16 32 128 256 512; do
        sips -z $size $size "$SOURCE_PNG" --out "$ICONSET_DIR/icon_${size}x${size}.png" >/dev/null
        double=$((size * 2))
        if [ $double -le 1024 ]; then
            sips -z $double $double "$SOURCE_PNG" --out "$ICONSET_DIR/icon_${size}x${size}@2x.png" >/dev/null
        fi
    done

    iconutil --convert icns "$ICONSET_DIR" --output "$BUILD_DIR/AppIcon.icns"
    rm -rf "$ICONSET_DIR"

    # 3. 组装 .app bundle
    echo "[3/5] 组装 .app bundle ..."
    RES="$APP_BUNDLE/Contents/Resources"
    DEST="$RES/app"
    mkdir -p "$APP_BUNDLE/Contents/MacOS"
    mkdir -p "$DEST"

    sed "s/VERSION_PLACEHOLDER/$VERSION/g" packaging/macos/Info.plist > "$APP_BUNDLE/Contents/Info.plist"
    # 注意:不要在这里就写 Contents/MacOS/whisper-input。一旦 launcher 落地,
    # macOS LaunchServices 会立刻把整个 .app 注册为 managed app,后续 cp -R
    # python 会被 App Management 全部拦截报 EPERM。launcher 留到最后一步写。
    cp "$BUILD_DIR/AppIcon.icns" "$RES/"

    # bundle 内置 python（从 cache 整棵复制，保留权限和符号链接）
    cp -R "$PY_EXTRACT/python" "$RES/python"
    PY_SIZE=$(du -sh "$RES/python" | cut -f1)
    echo "    已打包 python-build-standalone $PYTHON_VERSION ($PY_SIZE)"

    # 造一个嵌套的子 .app 解决 TCC 权限归属：
    # python 二进制被 macOS 按 "python3" 这个文件名归属，进不了 .app 的权限簿。
    # 嵌套 .app 用 Info.plist + 重命名的 python 二进制 + 图标，让 TCC 把权限、
    # dock 图标、cmd-tab 名称都挂到 "Whisper Input" 上。
    # libpython 通过 ../lib 相对路径查找，在嵌套 .app/Contents/lib 做 symlink
    # 指回 Resources/python/lib。
    RUNTIME_APP="$RES/Whisper Input.app"
    RUNTIME_BIN="$RUNTIME_APP/Contents/MacOS/whisper-input"
    mkdir -p "$RUNTIME_APP/Contents/MacOS"
    mkdir -p "$RUNTIME_APP/Contents/Resources"
    # cp -L 解 symlink，得到一份独立的 binary；dyld 用 runtime.app 路径做 @executable_path
    cp -L "$RES/python/bin/python3" "$RUNTIME_BIN"
    chmod 755 "$RUNTIME_BIN"
    # @executable_path/../lib → runtime.app/Contents/lib → ../../python/lib
    ln -s "../../python/lib" "$RUNTIME_APP/Contents/lib"
    # 图标（和主 .app 用同一张）
    cp "$BUILD_DIR/AppIcon.icns" "$RUNTIME_APP/Contents/Resources/AppIcon.icns"
    cat > "$RUNTIME_APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>Whisper Input</string>
    <key>CFBundleDisplayName</key>
    <string>Whisper Input</string>
    <key>CFBundleIdentifier</key>
    <string>com.whisper-input.runtime</string>
    <key>CFBundleExecutable</key>
    <string>whisper-input</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleVersion</key>
    <string>$VERSION</string>
    <key>CFBundleShortVersionString</key>
    <string>$VERSION</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSMicrophoneUsageDescription</key>
    <string>Whisper Input 需要麦克风权限来录制语音并进行语音识别</string>
</dict>
</plist>
PLIST
    echo "    已创建嵌套 Whisper Input.app (TCC 归属 + 图标 + 名称)"

    # bundle 内置 uv（用户无需自行安装）
    UV_BIN=$(command -v uv)
    cp "$UV_BIN" "$RES/uv"
    chmod 755 "$RES/uv"
    UV_SIZE=$(du -sh "$RES/uv" | cut -f1)
    echo "    已打包 uv ($UV_SIZE)"

    # 应用源码(src layout 整棵 + pyproject 三件套)
    copy_src_tree "$DEST"
    cp packaging/macos/setup_window.py "$DEST/"

    # launcher 留到最后:它一旦落地就会触发 LaunchServices 注册。即便如此,
    # 此时 staging-bundle 还没改名为 .app,LS 不会把它当 managed app 看。
    cp packaging/macos/whisper-input.sh "$APP_BUNDLE/Contents/MacOS/whisper-input"
    chmod 755 "$APP_BUNDLE/Contents/MacOS/whisper-input"

    # 整个 staging 完成,mv 改名为 .app。mv 是单次 inode rename,
    # LaunchServices 来不及在 mv 中途插手。
    mv "$APP_BUNDLE" "$FINAL_APP"
    APP_BUNDLE="$FINAL_APP"

    # 4. 创建 DMG
    echo "[4/5] 创建 DMG 安装包 ..."
    DMG_TEMP="$BUILD_DIR/dmg_temp"
    mkdir -p "$DMG_TEMP"
    cp -R "$APP_BUNDLE" "$DMG_TEMP/"
    ln -s /Applications "$DMG_TEMP/Applications"

    hdiutil create \
        -volname "$APP_NAME" \
        -srcfolder "$DMG_TEMP" \
        -ov -format UDZO \
        "$BUILD_DIR/$DMG_NAME" >/dev/null

    rm -rf "$DMG_TEMP"

    echo ""
    echo "[5/5] 构建完成!"
    echo ""
    APP_SIZE=$(du -sh "$APP_BUNDLE" | cut -f1)
    DMG_SIZE=$(du -sh "$BUILD_DIR/$DMG_NAME" | cut -f1)
    echo "========================================="
    echo "  $APP_NAME v$VERSION (macOS)"
    echo "========================================="
    echo "  .app: $APP_BUNDLE ($APP_SIZE)"
    echo "  .dmg: $BUILD_DIR/$DMG_NAME ($DMG_SIZE)"
    echo "========================================="
}

# ========================================
# Linux DEB 构建
# ========================================
build_linux() {
    BUILD_DIR="build/deb/${PKG_NAME}_${VERSION}"
    BUILD_OPT="$BUILD_DIR/opt/whisper-input"

    echo "正在构建 ${PKG_NAME} v${VERSION} (Linux DEB) ..."

    rm -rf "$BUILD_DIR"

    mkdir -p "$BUILD_DIR/DEBIAN"
    mkdir -p "$BUILD_OPT"
    mkdir -p "$BUILD_DIR/usr/bin"
    mkdir -p "$BUILD_DIR/usr/share/applications"
    mkdir -p "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps"

    # 应用源码(src layout 整棵 + pyproject 三件套)
    copy_src_tree "$BUILD_OPT"

    # setup_window.py 和 python_dist.txt 是 Linux 运行期必需的引导资源
    cp packaging/debian/setup_window.py packaging/debian/python_dist.txt \
        "$BUILD_OPT/"

    # 系统级 icon 和 .desktop(来自 package data)
    cp "$SOURCE_DESKTOP" "$BUILD_DIR/usr/share/applications/"
    cp "$SOURCE_PNG" "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps/"

    sed "s/VERSION_PLACEHOLDER/${VERSION}/g" packaging/debian/control \
        > "$BUILD_DIR/DEBIAN/control"
    cp packaging/debian/postinst "$BUILD_DIR/DEBIAN/"
    cp packaging/debian/prerm "$BUILD_DIR/DEBIAN/"
    cp packaging/debian/postrm "$BUILD_DIR/DEBIAN/"
    chmod 755 "$BUILD_DIR/DEBIAN/postinst"
    chmod 755 "$BUILD_DIR/DEBIAN/prerm"
    chmod 755 "$BUILD_DIR/DEBIAN/postrm"

    cp packaging/debian/whisper-input.sh "$BUILD_DIR/usr/bin/whisper-input"
    chmod 755 "$BUILD_DIR/usr/bin/whisper-input"

    find "$BUILD_OPT" -type f -exec chmod 644 {} \;
    chmod 644 "$BUILD_DIR/usr/share/applications/whisper-input.desktop"
    chmod 644 "$BUILD_DIR/usr/share/icons/hicolor/256x256/apps/whisper-input.png"

    dpkg-deb --build "$BUILD_DIR"

    echo ""
    echo "========================================="
    echo "  ${PKG_NAME} v${VERSION} (Linux DEB)"
    echo "========================================="
    echo "  .deb: build/deb/${PKG_NAME}_${VERSION}.deb"
    echo "========================================="
}

# ========================================
# 平台检测
# ========================================
case "$(uname)" in
    Darwin) build_macos ;;
    Linux)  build_linux ;;
    *)
        echo "错误: 不支持的平台 $(uname)"
        exit 1
        ;;
esac
