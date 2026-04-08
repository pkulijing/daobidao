# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Whisper Input is a Linux desktop voice input tool: hold a hotkey, speak, release to have speech transcribed and typed into the focused window. Uses evdev for keyboard events, SenseVoice (FunASR) for local STT, and clipboard+xdotool for text input. X11 only.

## Commands

```bash
# Install dependencies
uv sync

# Run (requires root or user in 'input' group)
uv run python main.py
uv run python main.py -k KEY_RIGHTALT    # custom hotkey
uv run python main.py --no-tray          # no system tray
uv run python main.py --no-preload       # skip model preload
uv run python main.py -c /path/config.yaml

# Lint (ruff)
uv run ruff check .

# Build DEB package
bash build_deb.sh
```

No automated test suite exists.

## Architecture

Event-driven pipeline orchestrated by `WhisperInput` in `main.py`:

```
HotkeyListener (evdev) → AudioRecorder (sounddevice, 16kHz mono)
                        → SenseVoiceSTT (FunASR, local model)
                        → InputMethod (clipboard + xdotool Ctrl+V)
```

Key modules:
- **main.py** — Entry point, CLI args, `WhisperInput` controller, system tray setup
- **hotkey.py** — `HotkeyListener`: evdev keyboard monitoring with 300ms combo-key detection for modifier keys
- **recorder.py** — `AudioRecorder`: sounddevice capture → WAV bytes
- **stt_sensevoice.py** — `SenseVoiceSTT`: FunASR SenseVoice-Small, lazy model loading
- **input_method.py** — `type_text()`: save clipboard → copy text → Ctrl+V paste → restore clipboard
- **config_manager.py** — YAML config with priority: CLI flag → project dir → `~/.config/whisper-input/` → `/opt/whisper-input/`
- **settings_server.py** — Built-in HTTP server serving web UI + REST API for settings

## Key Technical Decisions

- **evdev** over Xlib: distinguishes left/right modifier keys
- **Clipboard paste** over xdotool typing: avoids CJK encoding issues
- **Web UI settings** over GTK: avoids PyGObject venv complexities, uses stdlib `http.server`
- **300ms delay** on modifier key press: detects whether user is pressing a combo (e.g., Ctrl+C) vs triggering recording

## Ruff Configuration

Configured in `pyproject.toml` with rules: I (isort), N (pep8-naming), UP (pyupgrade), B (flake8-bugbear), SIM (flake8-simplify), RUF. Ignores RUF001/RUF002/RUF003 (Unicode punctuation). Line length: 80.

## Dependencies

Managed with `uv`. PyTorch (CUDA 12.1) from SJTU mirror, everything else from Tsinghua mirror. See `pyproject.toml` for index configuration.
