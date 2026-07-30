# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Keep this file **short**. It's an orientation doc for a fresh AI session, not the architecture reference — that's `PUPA_ARCHITECTURE.md`. If you're about to add a paragraph explaining *how* something works, it probably belongs there or in `OBS_CONFIG.md` instead, with just a pointer left here.

## Project Overview

PUPA is a real-time "VJ brain" that automates OBS Studio scene switching based on live audio analysis. It listens to a system audio input device, extracts bass/mid/high frequency bands, detects kicks/drops/breaks, and drives scene changes over the OBS WebSocket v5 API. Code and logs are Italian/English mixed (comments and print/log messages are largely in Italian).

This **is** a git repository (GitHub: `fehrdai/pupa`) — two machines run PUPA against their own local OBS instance: a Windows dev/test box and a Linux live-show rig (`farefesta@192.168.1.107`, SSH key `~/.ssh/pupa_linux`). Each machine has its own `secrets_local.py` (gitignored, not synced) with machine-specific OBS host/port/password and audio device settings — see `secrets_local.example.py` for the template. Quick iteration during a work session typically happens via direct `scp` deploy + remote compile-check rather than a full `git commit`/`push`/`pull` cycle each time; commit to git for durable snapshots, not every tweak.

## Commands

No build step or test suite — a script-based project run directly with Python.

```bash
python pupa.py                 # main VJ brain loop (connects to OBS + starts audio capture)
python list_audio_devices.py   # list audio input devices (to find AUDIO_DEVICE_NAME/PULSE_SOURCE)
python test_obs.py             # quick standalone check that the OBS WebSocket connection/credentials work
python -m py_compile *.py      # compile-check after any edit, on BOTH machines if deployed to both
```

Dependencies: `pip install -r requirements.txt` (sounddevice, numpy, obsws-python, pyyaml).

## Where things live

- **`PUPA_ARCHITECTURE.md`** — what PUPA is, how it decides what to show, the audio pipeline, file map, current state, known bugs, roadmap.
- **`OBS_CONFIG.md`** — the OBS side specifically: scene collection structure, the `_A`/`_B` naming contract, WebSocket API gotchas, hotkeys, setup steps.
- **`LIGHTS_CONFIG.md`** — the QLC+/lighting side: OS2L channel map per fixture, the single-VC-page requirement, Web API setup for diagnostics.
- **`PUPA_DEVELOPMENT_LOG.md`** — chronological technical diary. Terse, for picking up context fast, not a narrative.
- **`LINUX_PORTING.md`** — deployment/setup guide for a new or reconnected machine.

## Mandatory doc-update policy

Agreed with the operator 2026-07-17 — **follow this without being asked each time**:
- **`CLAUDE.md`, `OBS_CONFIG.md`, `PUPA_DEVELOPMENT_LOG.md`: update at the close of every working session**, even a short one. A few lines is enough; the point is nothing significant goes undocumented.
- **`PUPA_ARCHITECTURE.md`, `LINUX_PORTING.md`: update occasionally** — when a stable feature actually lands, an unresolved bug is found, or a concrete future project appears (e.g. QLC+ integration). Not every session.

## Known non-obvious behavior

- `scenes_config.yaml` **is** loaded at runtime (`brain.py`'s `_load_scenes_config()`) and validated against whatever OBS actually has (`validate_scenes()` in `pupa.py` at startup) — unlike a config file, editing it has a real effect. See `OBS_CONFIG.md` for its structure.
- `scenes_config.yaml` is now **PUPA-generated**: `brain.discover_and_merge_config()` reads OBS's scene-naming convention (`_A`/`_B`/`_kick`/`_color`/`_wave`, `_color` scenes containing a `slideshow`-kind source detected as slides) and fills in whatever the file doesn't already specify. No scene names are hardcoded in Python anymore — see `OBS_CONFIG.md` for the full naming contract.
- OBS credentials/audio device: per-machine in `secrets_local.py`, not hardcoded in `pupa.py`.
- Scene names in OBS must match the `_A`/`_B`/`_kick`/`_color`/`_wave` suffix convention (`scene_discovery.py`) exactly (case-sensitive), except `black_color` which is the one mandatory name (fallback + warning if missing).
- QLC+ lighting sync (`qlc_controller.py`, OS2L protocol, TCP port 9996) mirrors PUPA's color/strobe/ambient decisions onto DMX channels when QLC+ is running — graceful no-op if unreachable, auto-reconnects if QLC+ starts late or drops mid-show. Strobe is frame-accurate via Master (not the fixture's own autonomous Strobe channel), including the burst's own color (white/accent), not just on/off. See `LIGHTS_CONFIG.md` for the channel map and setup requirements (single VC page!), `PUPA_DEVELOPMENT_LOG.md` for the build/debug story.
- Identity colors are pure RGB primaries only (red/green/blue) — `yellow_color` was removed entirely (both the OBS scenes and all code references), never re-add it as a rotating identity. `brain.get_light_outputs()` has 3 selectable modes (`sync`/`alternate`/`inverse`, only `inverse` is wired up as the live default — the others are complete but commented out, no hotkey selector yet).
- `debug.log` rotates at 2MB x 6 backups (`debug_logger.py`) — big enough to cover a full show; if a live-test analysis session finds only a fraction of the run's log survived, don't just re-raise the cap again, check whether logging volume itself grew unexpectedly first.
