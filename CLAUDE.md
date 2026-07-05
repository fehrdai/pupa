# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

PUPA is a real-time "VJ brain" that automates OBS Studio scene switching based on live audio analysis. It listens to a system audio input device, extracts bass/mid/high frequency bands, detects kicks/drops, and drives scene changes over the OBS WebSocket v5 API. All code and logs are Italian/English mixed (comments, print statements, and log messages are largely in Italian).

Not a git repository — there is no version control in this directory, so treat file history (see "File Naming Conventions" below) as the only record of prior iterations.

## Commands

There is no build step, package manifest, or test suite — this is a small script-based project run directly with Python.

```bash
# Run the main VJ brain loop (connects to OBS + starts audio capture)
python pupa.py

# List available audio input devices (needed to find the correct device ID)
python list_audio_devices.py

# Quick standalone check that the OBS WebSocket connection/credentials work
python test_obs.py
```

Dependencies (no requirements.txt exists; install manually as needed):
```bash
pip install sounddevice numpy obsws-python pyyaml
```

## Architecture

### Active runtime (files actually imported by `pupa.py`)
- **`pupa.py`** — Entry point / main loop. Connects to OBS, starts audio capture, polls at ~20Hz (`time.sleep(0.05)`), and on each frame asks `brain.py` for the next scene and tells `obs_controller.py` to switch if needed.
- **`obs_controller.py`** — Thin wrapper around `obsws_python.ReqClient` (OBS WebSocket v5). Handles scene caching, current-scene lookup, and `switch_scene()` with an optional Fade transition override.
- **`audio_analyzer.py`** — Runs a `sounddevice.InputStream` callback that does an FFT per audio block, extracts bass/mid/high band magnitudes with AGC-style dynamic normalization, and derives `is_kick` / `is_drop` / `is_break` boolean events from bass deltas and thresholds.
- **`brain.py`** — Decision logic, currently the "Hybrid Couples Model" (`HybridCouplesModel`, module-level singleton `model`). Scenes are organized as **couples**: a main scene (`*_A`) paired with a pool of filler/transition scenes (`*_B`). Logic: stay on the current `_A` scene for a fixed `COUPLE_DURATION` (240s); within that window, a kick toggles A↔B, and a drop forces an immediate return to A; once the 4-minute timer expires, a new (non-repeating, tracked via a `deque(maxlen=5)`) couple is chosen.
- **`logger.py`** — Sets up a `logging.FileHandler` writing to `logs/pupa.log` and exposes `log_decision(...)` for structured scene-switch log lines.

### Important: config files are not wired into the active code path
`config.yaml`, `COUPLES_CONFIG.yaml`, and `scenes.yaml` exist but **are not loaded by `pupa.py` or `brain.py`** — the current versions hardcode their own values instead:
- `pupa.py` has a hardcoded `CONFIG` dict (OBS host/port/password, audio device ID) at the top of the file, rather than reading `config.yaml`.
- `brain.py` has a hardcoded `COUPLES` dict (scene pairing + durations) at the top of the file, rather than reading `COUPLES_CONFIG.yaml`.
- `scenes.yaml` is descriptive documentation of the scene pool/tags but isn't parsed anywhere in the active code.

If asked to change OBS credentials, audio device, couple durations, or the B-scene pools, edit the hardcoded values directly in `pupa.py` / `brain.py` — editing the YAML files alone will have no effect unless the loading logic is added.

### File naming conventions (iteration history, no git)
Since there's no VCS, prior versions are kept side-by-side using suffixes rather than being deleted:
- `*_old.py` / `*._old.py` (e.g. `pupa_old.py`, `brain._old.py`, `obs_controller_old.py`, `logger_old.py`, `scenes_old.yaml`, `config_old.yaml`) — superseded versions of the corresponding active file. Not imported by anything active.
- `*_FIXED*` (e.g. `config_FIXED_pwd.yaml`) — patched variants referenced by the migration docs, sometimes not yet merged into the active file.
- `obs_control.py` vs `obs_controller.py` — `obs_control.py` is the older/simpler OBS wrapper used only by `pupa_old.py`; `obs_controller.py` is the active one used by `pupa.py`.
- `build_pupa.py` — a one-off scaffolding script from the original v0.1 "Heartbeat" setup that writes out an entire initial project structure (config.yaml, scenes.yaml, obs_controller.py, brain.py, logger.py, pupa.py, README.md) as string literals. It's a historical setup generator, not part of the runtime.
- Root-level `.md` files (`README_MIGRATION.md`, `FILE_CHECKLIST.md`, `SETUP_v04_COUPLES.md`) are migration/setup notes for specific version bumps (v0.3 Kill Switch → v0.4 Disciplined Couples), written as changelogs/instructions rather than living documentation.

### OBS scene naming contract
Scene names in OBS must match the `_A` / `_B` suffix convention used in `brain.py`'s `COUPLES` dict exactly (case-sensitive) — `_A` scenes are the "body" visuals, `_B` scenes are audio-reactive filler/transition shaders. `scenes.yaml` documents the intended tag/energy metadata per scene even though it isn't parsed at runtime.

### Known non-obvious behavior
- `pupa.py`'s `CONFIG` currently contains a real OBS WebSocket password in plaintext, matching the value duplicated across `config.yaml`, `test_obs.py`, and several `*_old.py` files.
- The kill-switch (RMS silence detection → force black scene) described in `README_MIGRATION.md` for v0.3 is **not present** in the current `brain.py`/`audio_analyzer.py` — it appears to have been dropped when moving to the v0.4 "Couples" model.
