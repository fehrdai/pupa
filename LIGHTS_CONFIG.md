# LIGHTS_CONFIG.md — QLC+ lighting rig, OS2L channel map, lights engine

Companion to `OBS_CONFIG.md` (that one's the video/OBS side; this one's the physical lights). Build/debug history: `PUPA_DEVELOPMENT_LOG.md` (2026-07-29 entries for the original mirror-the-video lights, **2026-09-19 for the redesign below**).

## STATO ATTUALE (2026-09-19, sera) — read this first

**The lights were redesigned from scratch (PU.luci session).** They are now a **separate process** (`lights/pupa_luci.py`) that listens to the audio itself and is **fully independent of the video** (no `brain.py`, no OBS scenes, no monitor phase, no identity colour). Built and validated **offline only**: engine simulation with synthetic audio + a real-time end-to-end test with fake audio/OBS/QLC+ (all PASS, see "Tests"). **Phase 2 progress (2026-09-19 evening, Linux+OBS+QLC+ on): DONE** - `lights/` + `audio_analyzer.py` + `shutdown_helpers.py` deployed to Linux; OBS control sources `PUPA_LUCI`, `PUPA_LUCI_LIVELLO_1..3` created; QLC+ relaunched by the operator (`-w -o ... -p`); **first real run with real music on the real rig, DMX read back through the QLC+ Web API**: F1/F2 RGB peak 178 (= level 2), ping-pong dominant-fixture changes 2.5/s (~kick rate), drop strobe = 2 bursts x 6 flashes in perfect F1/F2 sync (0.09-0.16 s steps), both fixtures dark <5% of samples, DMX fully back to 0 (Master included) after shutdown, kick detection->loop latency 16-19 ms. **NOT done**: real keys not yet bound to the new sources (operator), old light code still in `pupa.py`/`brain.py`, no operator verdict on how it looks, drop strobe fires too often on this track (3 in 30 s, see Tuning).

**BIG FINDING (2026-09-19): QLC+ 4.12.7 silently DROPS OS2L messages sent back-to-back.** Measured with a probe (6 channels per burst, 40 bursts): no pause between messages -> 113/240 channels lost (47%); 1 ms, 3 ms or 10 ms pause -> 0/240. (The plugin parses one TCP read at a time; concatenated JSON objects are discarded.) Consequences: (1) the new sender paces messages (`OS2L_MIN_GAP_S` = 2 ms, in `AsyncQLC`); (2) `shutdown_helpers.spegni_luci_qlc` now paces + does 2 passes (before, a clean shutdown left channels lit - Master F1 stayed at 255); (3) **`qlc_controller.py` and the OLD `pupa.py` lights still send in bursts** (`_qlc_set_rgb_both` = 6 back-to-back messages, blackout = a loop of zeros...) so roughly half of their commands were lost - a probable root cause of the operator's long-standing "le luci non vanno in sync con niente" (2026-08-01/09-12) that log analysis could never show, because the logs record what PUPA *sent*, not what QLC+ *applied*. **Lesson: validate lights by reading the DMX back (Web API), not only by lights.log.** `qlc_web_monitor.py` parser fixed too (QLC+ 4.12.7 returns 3 fields per channel, the old parser assumed 4 and lost channels).

**`pupa.py` still contains the OLD light code** (mirrors the kick pulse, `light_mode` F5/F6/F7, ambient wash, strobe from the shared video burst, wave floor…) and would still drive QLC+ if run as it is. **Do not run both at once** — they would fight over the same DMX channels. Phase 2 removes the old code from `pupa.py`/`brain.py` (see checklist). Until then, test `pupa_luci.py` with the old lights neutralised.

## Design (decided with the operator 2026-09-19)

| Question | Decision |
|---|---|
| Independence | Own process, own audio capture (`AudioAnalyzer`), own hotkey polling, own log. Nothing read from `brain.py`. Restart lights without touching video. |
| Intensity | **Bass = intensity.** Each kick pulses a fixture at an amplitude scaled by the kick's bass; decay locked to BPM. A continuous bass follower keeps lights reactive even if kicks aren't detected; a small floor proportional to the mids avoids the old "(0,0,0) between kicks" holes. |
| Fixtures | **Ping-pong on kicks** (sound-driven, not bar-driven): each detected kick goes to the other fixture. |
| Colour | **Own palette** (independent of the video identity). **Since 2026-09-19 evening (operator): ONE colour at a time on both fixtures, like PUPA live - only red, only green or only blue, then it rotates** (`SINGLE_COLOR = True` in `lights_config.py`; `False` restores a different colour per fixture). Rotation: every `PHRASE_BEATS` (32) beats and on drops/break exit. The new colour is applied when that fixture is dark (invisible swap). No yellow (reads green on this fixture); white reserved for strobe. |
| Break | `is_break` → kicks ignored, slow breathing (8 s period, 5–35%). |
| Drop | Own white strobe (6 flashes, half-period = beat/4 clamped 80–120 ms, frame-accurate via Master), **30 s cooldown** (was 8; 3 strobes in 30 s on real music was too many). **Pre-drop strobe NOT built** (the video's `_detect_runup` had 69 false positives before tuning; agenda). |
| On/off + level | **New hotkeys**: `PUPA_LUCI` (Show = on, Hide = off, fade) and `PUPA_LUCI_LIVELLO_1..3` (ceiling ×0.35/×0.70/×1.00, default 2). Both persist across restarts (read back from OBS). |
| CALM | Lights do **not** follow CALM yet — deliberately on the agenda (operator: "per ora mettiamolo in agenda"). |
| Legacy hotkeys | F8 manual white strobe, F11 blackout (incl. Master), F12 shutdown are handled by `pupa_luci.py` too (same sources as `pupa.py`). F5/F6/F7 (light mode), F9/F10 (solo lights/monitor) have **no effect on the new lights**. |

## Files (`lights/`)

- `lights_config.py` — hardware map + **every tunable** (tune here, never in the engine).
- `lights_engine.py` — pure decision module (`LightsEngine.tick(t, metrics) -> {"f1": (r,g,b,master), "f2": …}`) + `FrameSender` (diff-only sends, `MIN_SEND_STEP`, full resync every 2 s so a restarted QLC+ realigns itself).
- `pupa_luci.py` — the runner: audio → engine → OS2L. Threads: main loop (30 Hz), OBS control poller, non-blocking OS2L sender with per-channel coalescing. Logs to `lights/logs/lights.log` (its own dir: `os.chdir` at start so shared loggers don't collide with `pupa.py`'s rotating files).
- `create_obs_sources.py` — creates the new control sources in `PUPA_Control` (dry-run by default, **untested against a real OBS**).
- `test_lights_engine.py`, `test_pupa_luci_runner.py` — offline tests.

Additive change outside `lights/`: `audio_analyzer.py` `get_metrics()` now also returns `drop_event` (consume-once, same race fix as `is_kick`) and `last_kick_time`. `pupa.py` ignores them.

Run (Linux rig): `cd ~/Desktop/pupa && nohup python3 lights/pupa_luci.py > /tmp/luci.log 2>&1 < /dev/null & disown`. `--no-obs` = no hotkeys (default on, level 2). Stop: F12 (also stops `pupa.py`), or `kill -INT <pid>` (clean cascade: all channels → 0).

**`lights.log` (use this FIRST for any lights complaint)**: one `[SUM]` line every 10 s — ticks/s, OS2L sends/s, kicks, detection→loop latency (mean/max), max loop gap, **dark % while live**, bpm, power, level, break, colours, qlc/obs link state — plus `[EV]` lines for kicks (side, strength), break enter/exit, colour rotations, drops, hotkeys.

## Tests (run on any machine, no services needed)

```
python lights/test_lights_engine.py        # 26 checks, synthetic 86 s "night" @30 Hz
python lights/test_pupa_luci_runner.py     # ~20 s real time: fake audio/OBS/QLC+, hotkeys F8/F11/F12/on-off/level
```
Measured 2026-09-19: ping-pong alternation 47/47 kicks, kick visible in the same tick, 0.00 % dark ticks in groove, contrast weak/strong side at a kick 0.21, break breathing 9–62/255 (ceiling 72), drop = 6 flashes at 106 ms (expected 107 ms), second drop inside cooldown ignored, off fades to 0 within 0.5 s, level 1 peak 66 vs 133 at level 2, blackout all zeros incl. Master, OS2L traffic ≈32 msgs/s in groove (peak 80/s), 5/5 seeds. **Tuning (real music, 2026-09-19)**: `DROP_STROBE_COOLDOWN_S` 8 s lets the analyzer's drop rule fire ~every 10 s on this track (3 in 30 s) - raise to ~30 s if it looks like too much live. **Known tuning note**: with no kicks detected at all (kick-starved) the follower alone only reaches ~25 % brightness (`FOLLOW_GAIN` 0.30) — raise if that case matters live.

**Smoke test finding (fixed)**: with QLC+ down, `QLCController` reconnects block the caller ~2 s each on Windows loopback → the OS2L sender runs in its own thread (`AsyncQLC`); loop gap dropped 2021 ms → 34 ms.

## Phase 2 checklist (needs Linux + OBS on; operator present for the key binding)

1. Copy `lights/` + `audio_analyzer.py` to Linux (scp), `python3 -m py_compile lights/*.py audio_analyzer.py`, run `lights/test_lights_engine.py` there.
2. `python3 lights/create_obs_sources.py` (dry-run), then `--apply`. **Operator binds real keys** in OBS Settings → Hotkeys: Show of `PUPA_LUCI` (+ optional Hide on another key) and Show of each `PUPA_LUCI_LIVELLO_n`. Keys not chosen yet (existing: F1–F4 CALM, F5–F7, F8, F9, F10, F11, F12, Ctrl+L). Verify in the scene collection JSON (`tso_backup_before_scene_removal.json`) as done on 2026-09-18.
3. Smoke test without music: run `pupa_luci.py`, confirm QLC+ link, read DMX back via `qlc_web_monitor.py` (defaults `universe=1,start_address=1,count=20` only — other params hang the Web API).
4. **Remove the old light code from `pupa.py`/`brain.py`** in a SEPARATE commit (shared with the PUPA 1 session — coordinate): `_qlc_set_rgb_both` + gate/wave floor/ambient/strobe-colour blocks, Master strobe block, `light_mode`/`get_light_outputs*`/`get_ambient_light`/`LIGHT_*`/`AMBIENT_*`, F5–F7 controls, `pupa.py`'s own QLC connection and F8 handler. Keep `forced_mode` for the monitors (F9/F10 still black the monitors). Keep F11's monitor half and F12.
5. Live test with real music (Windows → VB-Cable → `audio_bridge.py` → UCA222 → Linux); judge with `lights.log` numbers first (dark %, latency, sends/s), then the operator's eyes.

## Agenda (not built)

- Lights following CALM (calm 3 = soft, no strobe) — operator deferred.
- Pre-drop strobe (needs an own run-up detector, not the video's).
- Hi-hat shimmer / use of `high`; colour-by-spectrum experiment; more palette (magenta/cyan) if primaries feel poor.
- A wav of the test music for reproducible offline replay (`audio_levels.log` samples every 5 s, unusable for replay).

## Hardware

- 2x generic RGBW PAR fixtures ("Generic Dimmer", 7-channel mode), daisy-chained on DMX Universe 1 via an FT232R USB-DMX interface.
- Channel layout per fixture (1-indexed, matches the physical fixture's own manual): Ch1=Master, Ch2=Red, Ch3=Green, Ch4=Blue, Ch5=Strobe (0-7 off, 8-255 speed), Ch6=mode (0-10 direct RGB, 11+ macro/auto modes), Ch7=sub-parameter for whichever mode Ch6 selects.
- Fixture 1: DMX address 1 (QLC+ internal Fixture ID `0`). Fixture 2: DMX address 8 (QLC+ internal Fixture ID `1`).
- **Both fixtures' physical DMX address must actually match what QLC+'s project assumes** (1 and 8) — a mismatch here (one was physically found set to address 10 instead of 8 during testing) causes that fixture to simply not respond to anything, with no error anywhere in the software chain. If a fixture doesn't respond, check its own address display/DIP switches before suspecting PUPA or QLC+.

## QLC+ project (`QLC+\pupa.qxw`)

One single Virtual Console page ("Pagina 1") — **this must stay a single page**. QLC+ only delivers external (OS2L) input to widgets on whichever VC page is currently frontmost; splitting the rig across multiple pages silently breaks whichever page isn't active. Learned the hard way — see the dev log for the debugging story.

10 sliders, each bound to one physical channel on one fixture:

| Slider | OS2L id (`cmd`) | Fixture | Channel offset |
|---|---|---|---|
| F1_Master | 13 | 0 | 0 (Ch1) |
| F1_Red | 10 | 0 | 1 (Ch2) |
| F1_Green | 11 | 0 | 2 (Ch3) |
| F1_Blue | 12 | 0 | 3 (Ch4) |
| F1_Strobe | 14 | 0 | 4 (Ch5) |
| F2_Master | 18 | 1 | 0 (Ch1) |
| F2_Red | 15 | 1 | 1 (Ch2) |
| F2_Green | 16 | 1 | 2 (Ch3) |
| F2_Blue | 17 | 1 | 3 (Ch4) |
| F2_Strobe | 19 | 1 | 4 (Ch5) |

`lights/lights_config.py` `CH` (and, until removed, `pupa.py`'s `QLC_CHANNEL_F1_*`/`F2_*`) mirror this table exactly — if the QLC+ project's ids ever change, update both places.

**Mode (Ch6) is deliberately NOT in this table** — it stays a manual, pre-show setting via QLC+'s Simple Desk (fixed at 0 = direct RGB mode for both fixtures). PUPA never touches it. Confirm both fixtures' Ch6=0 before a show if anything seems off (a fixture stuck in an auto-cycling mode ignores R/G/B/Master entirely and looks like a hardware fault).

**Strobe channel (Ch5) is not driven by PUPA** — it's an autonomous onboard flasher, not frame-accurate; strobe is done by toggling Master per frame (drop strobe, F8). The F1_Strobe/F2_Strobe sliders exist, assigned, unused (Simple Desk for manual use). Shutdown still zeroes them.

## OS2L (runtime control, port 9996)

`qlc_controller.py` — persistent TCP client, `set_channel(id, value)` sends `{"evt":"cmd","id":<int>,"param":<0-255>}`. `TCP_NODELAY` on. Auto-reconnects (throttled, `reconnect_interval`) if QLC+ starts after PUPA or drops mid-show — **the reconnect blocks the caller** (~2 s on Windows loopback), so the lights runner calls it from its own sender thread. No profile/input-mapping needed for `cmd` — the `id` is the sender's own choice, matching the table above directly. QLC+ must be started with `-p` (operate mode) on Linux or there is no DMX output; needs `DISPLAY=:0.0`.

## Web API (diagnostics, port 9999) — NOT needed for a normal show

Only useful for debugging/inspecting live state without touching the QLC+ GUI. **Requires launching QLC+ with `-w`**: `qlcplus-qml.exe -w -o "F:\Desktop\pupa\QLC+\pupa.qxw"` (Linux: `qlcplus -w -o … -p`). WebSocket at `ws://127.0.0.1:9999/qlcplusWS`, pipe-separated text protocol. `qlc_web_monitor.py` connects and writes a JSON snapshot (`logs/qlc_live_state.json`) of every channel value + widget list — use this instead of screenshots to check "is the rig actually doing what I think." The Web API is fragile: one malformed request (wrong universe/count) hangs it until QLC+ restarts. Normal show operation doesn't need `-w` at all.
