**English** | [中文](README.zh-CN.md)

# Daobidao

> **🎉 Renamed**: this project used to be called `whisper-input`. Starting
> with v1.0.0 it has been renamed to `daobidao` (叨逼叨, Chinese onomatopoeia
> for non-stop talking — fits a voice input tool). The legacy package name
> still works (`pip install whisper-input` redirects to `daobidao`), but new
> releases land on the new name. Use `uv tool install daobidao` going forward.
> See [docs/29-改名为daobidao/SUMMARY.md](docs/29-改名为daobidao/SUMMARY.md).

[![Build](https://github.com/pkulijing/daobidao/actions/workflows/build.yml/badge.svg)](https://github.com/pkulijing/daobidao/actions/workflows/build.yml)
[![codecov](https://codecov.io/gh/pkulijing/daobidao/branch/master/graph/badge.svg)](https://codecov.io/gh/pkulijing/daobidao)
[![PyPI](https://img.shields.io/pypi/v/daobidao.svg)](https://pypi.org/project/daobidao/)

Cross-platform voice input tool — hold a hotkey, speak, release to have speech transcribed and typed into the focused window.

Uses Alibaba Qwen team's [Qwen3-ASR](https://www.modelscope.cn/models/zengshuishui/Qwen3-ASR-onnx) as the STT engine — an encoder-decoder LLM-style ASR with strong multilingual coverage (Chinese, English, Japanese, Korean, Cantonese, and more), built-in punctuation, inverse text normalization, and casing. Direct inference via Microsoft `onnxruntime`, fully offline after first download. Two variants are available via the settings page: **0.6B** (default, ~990 MB, ~1.5s for a 10s utterance on Apple Silicon) and **1.7B** (~2.4 GB, highest accuracy).

Supports **Linux (X11)** and **macOS**.

## Features

- Local speech recognition, works offline
- Multi-language mixed input (Chinese, English, etc.)
- Configurable hotkey (distinguishes left/right modifier keys)
- Browser-based settings UI + system tray
- Auto-start on login
- Automatic platform detection with matching backend

## System Requirements

### Linux
- **Ubuntu 24.04+ / Debian 13+** (X11 desktop environment)
- Any x86_64 CPU (`onnxruntime` CPU inference, RTF ~ 0.1, latency < 1s for short utterances)

### macOS
- macOS 12+ (Monterey or later)
- Apple Silicon (recommended) or Intel Mac, both use CPU ONNX inference

## Installation

### One-liner (recommended)

On macOS or Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/pkulijing/daobidao/master/install.sh | sh
```

The script interactively picks a language (中文 / English), then installs `uv`, Python 3.12, required system libraries, and `daobidao` itself. It runs `daobidao --init` (pre-downloads the ~990 MB Qwen3-ASR 0.6B ONNX model; on macOS also installs `~/Applications/Daobidao.app`) and finally asks whether to launch the app immediately. It's safe to re-run — already-installed pieces are skipped, and `uv tool install --upgrade` upgrades `daobidao` to the latest version.

On Linux the script will offer to add the current user to the `input` group (requires `sudo`; takes effect after a logout/login cycle).

> **Note**: `curl | sh` trusts this repo. If you want to review the script first, download it with `curl -LsSf <URL> -o install.sh` and inspect it before running.

### Manual installation

#### macOS

```bash
# Install system dependency
brew install portaudio

# Install the tool (--compile-bytecode skips the first-run .pyc compile step)
uv tool install --compile-bytecode daobidao

# One-time setup: install .app bundle + download STT model (~990 MB for Qwen3-ASR 0.6B)
daobidao --init

# Run
daobidao
```

**First-run permissions required in System Settings > Privacy & Security:**

1. **Accessibility** (for global hotkey listening and text input)
2. **Microphone** (for voice recording; the system will prompt on first recording)

> **Note**: On first run (or via `daobidao --init`), the tool installs a minimal `.app` bundle at `~/Applications/Daobidao.app`. macOS permission dialogs and System Settings entries will show "Daobidao" — grant Accessibility to that entry. To fully uninstall, run `daobidao --uninstall` before `uv tool uninstall daobidao`.

#### Linux

```bash
# Install system dependencies (see table below for details)
sudo apt install xdotool xclip pulseaudio-utils libportaudio2 \
                 libgirepository-2.0-dev libcairo2-dev gir1.2-gtk-3.0 \
                 gir1.2-ayatanaappindicator3-0.1

# Add yourself to the input group (evdev needs /dev/input/* access)
sudo usermod -aG input $USER && newgrp input

# Install the tool (--compile-bytecode skips the first-run .pyc compile step)
uv tool install --compile-bytecode daobidao

# One-time setup: download STT model (~990 MB for Qwen3-ASR 0.6B)
daobidao --init

# Run
daobidao
```

**System dependency reference:**

| Package | Purpose | Notes |
|---------|---------|-------|
| `xdotool`, `xclip` | Text input | xclip for X11 clipboard, xdotool to simulate Shift+Insert paste |
| `libportaudio2` | Audio recording | PortAudio library, runtime dependency of Python `sounddevice` |
| `pulseaudio-utils` | Sound notifications | Provides `paplay` for start/stop recording sounds |
| `libgirepository-2.0-dev`, `libcairo2-dev` | Build dependencies | Headers for compiling `pygobject` and `pycairo` C extensions |
| `gir1.2-gtk-3.0` | Recording overlay | GTK 3 typelib for the recording status overlay |
| `gir1.2-ayatanaappindicator3-0.1` | System tray icon | AppIndicator typelib, runtime dependency of `pystray` on Linux |

On first run, `daobidao` downloads the Qwen3-ASR ONNX model (~990 MB for the 0.6B default) via `modelscope.snapshot_download` to `~/.cache/modelscope/hub/`. After one successful download, the app is fully offline. You can switch to the 1.7B variant later from the in-app settings page (pulls an additional ~2.4 GB).

#### From Source (Contributors)

```bash
git clone https://github.com/pkulijing/daobidao
cd daobidao
bash scripts/setup.sh
uv run daobidao
```

## Usage

```bash
# Specify hotkey
daobidao -k KEY_FN          # macOS: Fn/Globe key
daobidao -k KEY_RIGHTALT    # Linux: Right Alt key

# More options
daobidao --help
```

A browser settings page opens automatically on startup; you can also access it via the system tray icon.

### How to use

1. Start the app, then hold the hotkey to begin recording
   - macOS default: Right Command key
   - Linux default: Right Ctrl key
2. Speak into the microphone
3. Release the hotkey, wait for recognition
4. The recognized text is automatically typed at the cursor position

## Release Flow (Maintainers)

PyPI distribution via GitHub Actions tag trigger + Trusted Publishing (OIDC):

1. Bump `version` in `pyproject.toml`
2. `git commit -am "release: v0.5.1"` and push to master
3. `git tag v0.5.1 && git push --tags`
4. [`.github/workflows/release.yml`](.github/workflows/release.yml) triggers automatically: verify tag matches version -> `uv build` -> publish to PyPI via `pypa/gh-action-pypi-publish` -> create GitHub Release

## Configuration

Config file `config.yaml`, also editable via the browser settings UI:

| Setting | Description | macOS Default | Linux Default |
|---------|-------------|--------------|--------------|
| `hotkey` | Trigger hotkey | `KEY_RIGHTMETA` | `KEY_RIGHTCTRL` |
| `qwen3.variant` | STT model size (`0.6B` / `1.7B`) | `0.6B` | `0.6B` |
| `sound.enabled` | Recording sound notification | `true` | `true` |
| `ui.language` | Interface language (zh/en/fr) | `zh` | `zh` |

## Known Limitations

- Linux supports X11 only; Wayland is not yet supported
- Super/Win key is intercepted by GNOME desktop, not recommended as hotkey
- macOS requires Accessibility permission for global hotkey monitoring
- First run downloads the Qwen3-ASR 0.6B ONNX model (~990 MB from ModelScope); switching to 1.7B later pulls another ~2.4 GB
- Current flow is press-to-talk / release-to-transcribe (batch mode) — real-time streaming is planned for a future release

## Technical Architecture

The project uses src layout with all Python code under `src/daobidao/`, installable as a standard package. The entry point is the `daobidao` console script (equivalent to `python -m daobidao`).

```
Hold hotkey -> HotkeyListener (daobidao.backends) -> AudioRecorder (sounddevice)
Release     -> stt.Qwen3ASRSTT (onnxruntime) -> InputMethod -> Text typed into focused window
```

Platform backends (`daobidao.backends`) auto-select at runtime via `sys.platform`:
- **Linux**: evdev for keyboard events + xclip/xdotool clipboard paste
- **macOS**: pynput global keyboard listener + pbcopy/pbpaste + Cmd+V paste

STT inference (`daobidao.stt.qwen3`):
- Model: Qwen3-ASR ONNX int8 from `zengshuishui/Qwen3-ASR-onnx` on ModelScope, downloaded via `modelscope.snapshot_download` to `~/.cache/modelscope/hub/`. Two variants side-by-side (0.6B / 1.7B), switchable via the settings page
- Runtime: Microsoft official `onnxruntime`, no torch / transformers dependency
- 3-stage pipeline: `conv_frontend.onnx` → `encoder.int8.onnx` → `decoder.int8.onnx` (28-layer KV-cache autoregressive decoder)
- Log-mel feature extraction: ~100 lines of pure numpy, bit-aligned with Whisper's reference extractor (rtol=1e-4)
- Tokenization: HuggingFace `tokenizers` (Rust byte-level BPE, ~10 MB) loading Qwen3-ASR's `vocab.json` + `merges.txt` directly — no `transformers` dependency
- Dependency tree: `onnxruntime + tokenizers + modelscope + numpy` (modelscope base is only 36 MB, no torch/transformers)

Common features:
- 300ms delay on modifier key press to distinguish combos (e.g., Ctrl+C) from single triggers
- Clipboard paste instead of key simulation, avoiding CJK encoding issues
- Unified CPU inference path, zero code difference between macOS/Linux

## License

MIT
