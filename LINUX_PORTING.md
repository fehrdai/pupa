# LINUX_PORTING.md — setting up / reconnecting to the Linux show rig

Rewritten 2026-07-17 (previous version, 2026-07-03, predated the dual-OBS-instance setup and the scp-based iteration workflow that's now standard). Update this occasionally — when the deployment workflow itself changes, not every session.

## Current setup (not "OBS stays on Windows" — that's outdated)

Both machines run PUPA against their **own local OBS instance** — Windows dev box and the Linux live rig (`farefesta@192.168.1.107`, SSH key `~/.ssh/pupa_linux`) each have a full scene collection that must be kept in parity manually (see `OBS_CONFIG.md`). Each machine has its own `secrets_local.py` (gitignored) — Windows connects to itself over its LAN IP, Linux connects to `localhost:4455`.

## Connecting

```bash
ssh -i ~/.ssh/pupa_linux farefesta@192.168.1.107
```

## Iteration workflow (quick edits during a session)

Real day-to-day flow is `scp` + remote compile-check, **not** a full git round-trip for every tweak:

```bash
scp -i ~/.ssh/pupa_linux brain.py pupa.py farefesta@192.168.1.107:/home/farefesta/Desktop/pupa/
ssh -i ~/.ssh/pupa_linux farefesta@192.168.1.107 "cd /home/farefesta/Desktop/pupa && python3 -m py_compile brain.py pupa.py && echo COMPILE_OK"
```

Commit to git for durable snapshots (end of a work block, before a show), not for every constant tweak — `git pull` before starting work on a machine, `git push` after, so the two machines don't silently diverge.

## Starting / stopping `pupa.py` remotely

```bash
# start (background, survives the SSH session ending)
ssh -i ~/.ssh/pupa_linux farefesta@192.168.1.107 "cd /home/farefesta/Desktop/pupa && nohup python3 pupa.py > /tmp/pupa_stdout.log 2>&1 & disown"

# stop
ssh -i ~/.ssh/pupa_linux farefesta@192.168.1.107 "pkill -f 'python3 pupa.py'"

# check it's actually running (pgrep -f often false-matches its own invocation)
ssh -i ~/.ssh/pupa_linux farefesta@192.168.1.107 "ps aux | grep '[p]upa.py'"
```

If Ctrl+C in an interactive terminal doesn't seem to respond, a plain `SIGINT` sent this way works fine — the code's own `KeyboardInterrupt` handler is not the problem; it's usually terminal focus/lag on the operator's side.

## System dependencies

`sounddevice` needs PortAudio at the OS level:
```bash
sudo apt install libportaudio2 portaudio19-dev python3-dev
pip install -r requirements.txt
```

## Audio input

`secrets_local.py` on Linux uses PulseAudio/PipeWire, not a Windows device index:
```python
AUDIO_DEVICE_NAME = "pulse"
PULSE_SOURCE = "alsa_input.pci-0000_00_1b.0.analog-stereo"  # find via `python list_audio_devices.py`
AUDIO_INPUT_GAIN_PCT = 40  # capture gain, set via pactl at startup (pupa.py's _set_capture_gain)
```
Linux has no "Stereo Mix" — the equivalent is a PulseAudio/PipeWire "Monitor" source (`*.monitor`), already what `list_audio_devices.py` looks for.

## Monitor alternation (2 physical show outputs)

`secrets_local.py` on Linux also carries the physical monitor mapping — **verify with `get_monitor_list()` on this specific machine, don't assume it matches another rig**:
```python
MONITOR_SHOW1_INDEX = 1  # e.g. DisplayPort-0
MONITOR_SHOW2_INDEX = 2  # e.g. HDMI-A-0
MONITOR_BLACK_SCENE = "black_master"
```
Requires `wmctrl` for the projector-raising mechanism (see `OBS_CONFIG.md`/`PUPA_ARCHITECTURE.md`) — `sudo apt install wmctrl` if missing.

## Cross-machine audio test rig (Windows → Linux, for remote dev without being at the venue)

Windows' audio output is physically cabled into this machine's line-in, so whatever plays on the Windows dev box acts as "live music" for testing PUPA on Linux remotely. Needs, on Windows: VB-Cable routed correctly (default playback → CABLE Input, `pupa.py` reads CABLE Output) **and** `audio_bridge.py` running (`python audio_bridge.py`, background) to bridge CABLE Output to the physical output wired to Linux — this bypasses a broken native Windows "Listen" loopback for that specific device (regressed after a Windows Update, 2026-07-16). Full detail and troubleshooting history in the `project_windows_linux_audio_test_wiring` memory note (not in this repo — ask if it needs pulling into a doc here).

## What's NOT in the repo (`.gitignore`)

`secrets_local.py`, `logs/`, `debug.log*`, `audio_levels.log*`, `__pycache__/`, `.claude/`.
