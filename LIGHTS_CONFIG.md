# LIGHTS_CONFIG.md — QLC+ lighting rig, OS2L channel map, Web API

Companion to `OBS_CONFIG.md` (that one's the video/OBS side; this one's the physical lights). See `PUPA_DEVELOPMENT_LOG.md` (2026-07-29 entries) for the full story of how this was built and debugged.

## Hardware

- 2x generic RGBW PAR fixtures ("Generic Dimmer", 7-channel mode), daisy-chained on DMX Universe 1 via an FT232R USB-DMX interface.
- Channel layout per fixture (1-indexed, matches the physical fixture's own manual): Ch1=Master, Ch2=Red, Ch3=Green, Ch4=Blue, Ch5=Strobe (0-7 off, 8-255 speed), Ch6=mode (0-10 direct RGB, 11+ macro/auto modes), Ch7=sub-parameter for whichever mode Ch6 selects.
- Fixture 1: DMX address 1 (QLC+ internal Fixture ID `0`). Fixture 2: DMX address 8 (QLC+ internal Fixture ID `1`).
- **Both fixtures' physical DMX address must actually match what QLC+'s project assumes** (1 and 8) — a mismatch here (one was physically found set to address 10 instead of 8 during testing) causes that fixture to simply not respond to anything, with no error anywhere in the software chain. If a fixture doesn't respond, check its own address display/DIP switches before suspecting PUPA or QLC+.

## QLC+ project (`QLC+\pupa.qxw`)

One single Virtual Console page ("Pagina 1") — **this must stay a single page**. QLC+ only delivers external (OS2L) input to widgets on whichever VC page is currently frontmost; splitting the rig across multiple pages silently breaks whichever page isn't active. Learned the hard way — see the dev log for the debugging story.

10 sliders, each bound to one physical channel on one fixture (not shared/combined like an earlier draft):

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

`pupa.py`'s `QLC_CHANNEL_F1_*`/`QLC_CHANNEL_F2_*` constants mirror this table exactly — if the QLC+ project's ids ever change, update both places.

**Mode (Ch6) is deliberately NOT in this table** — it stays a manual, pre-show setting via QLC+'s Simple Desk (fixed at 0 = direct RGB mode for both fixtures). PUPA never touches it; the fixture's macro/auto modes aren't part of PUPA's reactive vocabulary. Confirm both fixtures' Ch6=0 before a show if anything seems off (a fixture stuck in an auto-cycling mode ignores R/G/B/Master entirely and looks like a hardware fault).

**Strobe channel (Ch5) is also not driven by PUPA** as of the Step 1 lighting-plan work — it's an autonomous onboard flasher on the fixture, not frame-accurate, so PUPA drives Master directly instead for real strobe sync (see `PUPA_DEVELOPMENT_LOG.md`). The F1_Strobe/F2_Strobe sliders still exist and are assigned, just unused by PUPA - available for manual use via Simple Desk if wanted.

## OS2L (runtime control, port 9996)

`qlc_controller.py` — persistent TCP client, `set_channel(id, value)` sends `{"evt":"cmd","id":<int>,"param":<0-255>}`. Auto-reconnects (throttled, `reconnect_interval`) if QLC+ starts after PUPA or drops mid-show. No profile/input-mapping needed for `cmd` — the `id` is the sender's own choice, matching the table above directly.

## Web API (diagnostics, port 9999) — NOT needed for a normal show

Only useful for debugging/inspecting live state without touching the QLC+ GUI. **Requires launching QLC+ with `-w`**: `qlcplus-qml.exe -w -o "F:\Desktop\pupa\QLC+\pupa.qxw"` (not a GUI checkbox — see `docs.qlcplus.org/advanced/web-interface`). WebSocket at `ws://127.0.0.1:9999/qlcplusWS`, pipe-separated text protocol. `qlc_web_monitor.py` connects and writes a JSON snapshot (`logs/qlc_live_state.json`) of every channel value + widget list — use this instead of screenshots to check "is the rig actually doing what I think." Normal show operation doesn't need `-w` at all; OS2L and DMX output work identically either way.
