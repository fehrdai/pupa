# OBS_CONFIG.md — OBS setup, scene collection, and WebSocket gotchas

Everything specific to the OBS side of PUPA: how the scene collection is organized, what has to match exactly between OBS and the code, and the WebSocket API quirks that cost real debugging time to find. `PUPA_ARCHITECTURE.md` covers the decision logic; this file covers what OBS itself needs to look like and behave like for that logic to work.

Renamed from `SETUP_v04_COUPLES.md` 2026-07-17 (that file described the long-superseded v0.4 fixed-duration model and referenced files/variables that no longer exist).

## OBS UI gotcha: "can't drag/resize anything in Preview" is a whole-collection lock, not a per-source one

If NOTHING can be dragged or resized in the Preview panel, on ANY source/scene, and the per-source "Locked" toggle is confirmed off (`sceneItemLocked: false` via WebSocket) - check the scene collection's own `preview_locked` field (visible in the collection's JSON under `~/.config/obs-studio/basic/scenes/`, not exposed via `obsws_python`). This is a collection-wide Preview lock, separate from per-source locking. **Fix: right-click directly on the Preview panel itself (not on a source) → uncheck "Lock Preview"** - this cost real troubleshooting time before being found, since every per-source check (locked state, transform values) looked correct.

## Asset files (Linux rig)

**Standard folder: `/home/farefesta/fare@festa/`** — every video/image source used by the current scene collection lives here (plus a `slide/` subfolder for the slideshow). `/home/farefesta/Desktop/Videofare@festa/` is the **old** location, kept only for superseded/unused files with an `_old` suffix (reorganized 2026-07-17) — don't add new assets there, and don't assume a same-named file in each folder is the same file (several weren't, before this cleanup — always compare checksums, not just names/sizes, before deleting or overwriting).

## Scene-collection file naming — a real gotcha, verify don't assume

The Linux rig's `~/.config/obs-studio/basic/scenes/` can contain a JSON file whose **name doesn't match the collection's display name** (a leftover from a past in-app rename or "scene removal" operation) — as of 2026-07-17, the collection named `"tso"` is actually stored in a file called `tso_backup_before_scene_removal.json`, while an actual `tso.json` also exists on disk, is stale, and is NOT what's loaded. Trusting the filename cost real time. **Always confirm via `get_scene_collection_list()` (WebSocket) which collection is truly active, and check the JSON's own internal `"name"` field, not just its filename**, before reading or editing scene-collection JSON directly.

Also: OBS does **not** appear to autosave promptly on this rig — a collection can sit unsaved in memory for days if nothing forces a write. To force a save without restarting OBS (which would lose the in-memory state if it then crashed): switch to a different scene collection and back via `set_current_scene_collection()` — OBS writes the outgoing collection to disk before switching away from it.

## Connecting to OBS

- WebSocket server must be enabled in OBS (Tools → WebSocket Server Settings), with a password matching `secrets_local.py`'s `OBS_PASSWORD` on that machine.
- Windows dev box and Linux show rig each run their **own local OBS instance** — Windows connects to itself (its own LAN IP, historically `192.168.1.102:4455`), Linux connects to `localhost:4455`. They are not sharing one OBS; the scene collections must be kept in parity manually (see below).
- `obs_controller.py`'s `OBSController` wraps `obsws_python.ReqClient`. `pupa.py` calls `validate_scenes()` at startup, comparing `scenes_config.yaml` against what this specific OBS instance actually has — missing scenes/transitions get filtered out gracefully rather than crashing (falls back to a "degenerate mode" flashing a single scene if almost nothing is left).

## Scene naming convention (current, since the 2026-07-24 discovery refactor)

**PUPA has no hardcoded scene names in the code.** At startup it reads whatever scenes actually exist in this OBS collection and sorts them by **desinenza (suffix)** — this is the entire contract a new operator needs to follow to point PUPA at a different show's content, with zero code edits. See `scene_discovery.py` for the actual regexes (all anchored full-match, e.g. `^(.+)_A$` — a scene like `backup_A_old` deliberately does **not** match, only an exact `..._A` ending does).

| Suffix | Role | Required? |
|---|---|---|
| `_A` | primary/video scene | at least one, or PUPA drops into `DEGENERATE_MODE` (flashes a single scene instead of alternating A/B) |
| `_B` | secondary scene, shared pool across **all** `_A` (any scene_A can show any scene_B) | at least one |
| `_kick` | kick-reactive variant, one shown instead of/alongside a plain kick accent | optional |
| `_color` | full-screen solid-color scene (`color_source_v3` + `gradient_source`), used for strobe bursts, kick-synced tint pulses, and the black-pause feature | optional — **except `black_color`, which must always exist** |
| `_wave` | audio waveform/spectrum scene, tinted to match an identity's color | optional |

**`black_color` is mandatory.** It's the universal fallback: if it's missing, PUPA warns loudly at startup and every colored flash / white strobe that would have used a color scene is replaced by plain black instead — nothing crashes, but the whole show reads differently. Don't rename or delete it without a replacement in place.

**Slides are not a naming category.** A scene is treated as a slide (beat-locked advance, energy-scaled transition speed — same rules as any other slide) if it contains a nested source whose OBS input kind is `slideshow` (any version, e.g. `slideshow_v2` — checked via `unversionedInputKind`, not the exact versioned string), regardless of what the scene itself is named. This is deliberate: slides are meant to be interchangeable with video content, distinguished by what they *contain*, not by a special name. A scene can be `_A` or `_B` and still be a slide.

**`scenes_config.yaml` is now a PUPA-generated artifact, not a hand-authored one.** At every startup, `brain.discover_and_merge_config()` fills in any *missing* section (`couples`, `strobe_color_pool`, `identity_sets`) from the live discovery above and writes the result back to the file — so it's inspectable after the fact, but an operator starting a brand-new show doesn't need to touch it at all if the scene names follow the convention. A section that's already explicitly present in the file is left untouched (config wins over discovery when both exist) — this is the escape hatch for curated pairings the naming convention can't express, e.g. `meta_pair_duos` (still fully manual, see `PUPA_ARCHITECTURE.md`) or a hand-picked `couples` mapping instead of the "every scene_A gets the whole scene_B pool" default.

- **Identity bundles** (`scenes_config.yaml`'s `identity_sets`, one per non-black `_color` scene when auto-discovered): ties together `{color, wave_kick, waveform}` — `waveform` is matched by shared base name (`red_color` pairs with `red_wave`) when one exists, `wave_kick` is assigned round-robin across the discovered `_kick` scenes. `transition` isn't derivable from OBS at all (creative/aesthetic, see the per-transition-intensity note below) and is left unset unless the config explicitly sets it — PUPA's existing per-field fallback in `validate_scenes()` handles a missing field gracefully.
- **Color RGB values**: read live from OBS (the scene's own `color_source_v3` item, excluding shared/structural ones like `color_overlay`/`black_overlay`/`PUPA_CALM_*`/`PUPA_LOOP_SCENE`) for any `_color` scene never manually tuned. `pupa.py`'s `IDENTITY_OVERLAY_RGB` keeps a few hand-tuned overrides (yellow darkened, green lightened — both were "si vede poco" at their true OBS color) that still win over the live read.
- **Utility scenes** (no suffix, not discovered as content): `PUPA_Control` (hidden control surface — never put on air), `webcam`, `farefesta`.

Case-sensitive, exact suffix match — this is the whole naming contract.

### What's still NOT discoverable from OBS (creative/curatorial, stays hardcoded or config-driven)

- **`meta_pair_duos`** (fixed scene_A pairing for the DJ-changeover-adaptation idea) — experimental, operator-acknowledged as possibly-discardable, fully manual in `scenes_config.yaml`.
- **`STROBE_COLOR_WEIGHTS`** (`brain.py`) — per-state weighting of which `_color` a strobe burst favors (black/white dominant + one energy-linked accent). Literal `_color` names as dict keys, by design — this is aesthetic judgment (which color "feels" like which energy), not something OBS's scene list can express. Gracefully ignores any listed color that isn't actually in `STROBE_COLOR_POOL`, falling back to a uniform random choice if none of a state's weighted colors survived validation.
- **Transition "intensity"** (`TRANSITION_INTENSITY_RANK`, `couple_transitions` in `scenes_config.yaml`) — same reasoning, deferred to a future live-tuning hotkey/UI rather than automated.

## Shared nested overlay sources (`color_overlay`, `black_overlay`)

Two `color_source_v3` inputs, each created **once** and then added as a scene item to multiple scenes (`create_input` for the first scene, `create_scene_item` referencing the same input name for the rest) — changing the input's color/opacity updates it everywhere it's nested simultaneously, no per-scene runtime iteration needed.

- **`color_overlay`** — nested in the 4 scene_A + 4 real scene_B (not the slide scene) + the 4 `_kick` scenes (12 scenes total). Tints the current identity's color, pulsing on kick with decay (see `PUPA_ARCHITECTURE.md`).
- **`black_overlay`** — nested in the same 12 scenes **plus** the 4 `_wave` scenes (13+4). Sits above `color_overlay` in z-order. Drives both a bar-locked "breathing" pulse and an in-out breath during black-pause moments.
- Both were created via one-off Python scripts (not part of `pupa.py`'s runtime) — if the scene collection is ever rebuilt from scratch, these need to be recreated the same way on **both** machines. There is no in-OBS record of "this is a PUPA-managed overlay" beyond the source name.

## WebSocket API gotchas (found the hard way — don't rediscover these)

- **`color_source_v3`'s `color` setting is a 32-bit integer in ABGR order** (alpha in the highest byte, then B, then G, then R) — not RGBA/ARGB. Confirmed by reading real values off existing color scenes before writing any code. Build it as `(alpha << 24) | (b << 16) | (g << 8) | r`.
- **`SetSceneItemTransform` does not update the actual Program/Projector output**, even though `GetSceneItemTransform` immediately confirms the new values were stored, and even with a disable→enable toggle on every call (which does work for some other invalidation cases, e.g. `flash_scene()`). Confirmed reproducible live on OBS 32.1.2, 2026-07-17, using the exact mechanism that also failed on 2026-07-06 — this is a real OBS/projector rendering limitation, not a bug in `obs_controller.py`. **Do not build audio-reactive scale/rotation/position/crop via this API** — use `SetInputSettings`-driven approaches instead (opacity/color on a nested source, which **does** render correctly in Program). See `project_pulse_animator_agenda` for the investigation and the "Move Transition" / "Audio Move" OBS plugin (native filter, different code path) as a possible alternative if scale/rotation reactivity is needed later.
- **Scene-item z-order**: index `0` = bottom/background, higher index = more in front. Not documented anywhere obvious — confirmed by creating an item, reading back its index vs. an existing item's, and checking which rendered on top.
- **`slideshow_v2` defaults matter and are easy to get wrong when replicating a scene across machines**: `slide_mode` defaults to `mode_auto` (auto-advances on its own timer) unless explicitly set to `mode_manual` for kick-driven control; `playback_behavior` defaults to `stop_restart`, which resets the slide index to 0 every time the scene leaves and re-enters Program — set `always_play` so progress persists across visits. `GetInputSettings` **omits fields still at their type default**, so a scene that "looks the same" across two machines can silently differ — check `GetInputDefaultSettings('slideshow_v2')` if unsure, don't assume.
- **No random-jump hotkey exists** for `slideshow_v2` — only `SlideShow.PlayPause` / `Restart` / `Stop` / `NextSlide` / `PreviousSlide`. A "random slide" feature has to be built as a variable-step `NextSlide` burst, not a native jump.
- **Canvas resolution differs per machine** (Windows 1920x1080, Linux 1280x1024 as of 2026-07-17) — any script computing full-canvas bounds must call `get_video_settings()` live, never hardcode either machine's resolution.
- **Monitor index mapping is machine-specific**, verified via `get_monitor_list()`: on the Linux rig, `0`=DVI-I-1 (regia, unused), `1`=DisplayPort-0 (show1), `2`=HDMI-A-0 (show2) — stored in that machine's `secrets_local.py` as `MONITOR_SHOW1_INDEX`/`MONITOR_SHOW2_INDEX`, don't assume it's the same elsewhere.
- **`GetStats` field names** (wrapped in `OBSController.get_render_stats()`, 2026-07-21): `active_fps`, `average_frame_render_time`, `render_skipped_frames`, `render_total_frames`, `output_skipped_frames`, `output_total_frames`, `cpu_usage` (OBS's own self-reported CPU%, not the OS process CPU) — confirmed live on this install via `obsws_python`. Polled continuously by `runtime_monitor.py` now; previously only sampled by hand via a separate script during a specific test.
- **Transition names are whatever this specific OBS install has registered** (language + plugin dependent) — verified via `get_scene_transition_list()`, not assumed. On this install: `Stinger, Fade, Taglio, Scivola, Dissolvenza, Burn, Displace, Luma Wipe, Move, Blur`. There is no `"Cut"`/`"Flash"`/`"Strobe"` transition name — those are PUPA's own internal labels for *when* a hard cut happens, not real OBS transition names.

## Monitor alternation on Windows (2026-07-22) — OBS must NOT run as Administrator

Ported the Linux stacking mechanism to Windows (`window_manager.py`, `pywin32`) - works identically, but only if OBS and `pupa.py` run at the **same** privilege level. Found live: with OBS elevated (Administrator) and `pupa.py` not, Windows' UIPI denies *any* window manipulation between them - not just `SetForegroundWindow` (which fails anyway for an unrelated reason, see below), but `OpenProcess`, `SetWindowPos`, `PostMessage` too. **Fix: remove "Run this program as an administrator" from OBS's exe/shortcut properties (Compatibility tab), relaunch OBS normally.** If OBS genuinely needs elevation for something (e.g. Game Capture of an elevated game/app), the alternative is elevating `pupa.py` to match - not done by default, a real security-posture choice left to the operator.

Separately, `SetForegroundWindow` itself always fails for a background process with no real keyboard focus (a normal Windows restriction, unrelated to elevation) - doesn't matter here, since these are dedicated fullscreen Projectors: `SetWindowPos(HWND_TOP, ..., SWP_NOACTIVATE)` raises them in Z-order without needing true focus, which is all that's needed.

## Monitor alternation (2 physical show outputs, Linux rig)

Architecture: **4 static Projector windows opened once at startup, never closed/reopened during a session** — 2 per physical output (a "Programma" projector mirroring live Program, and a "Sorgente: black_color" projector, stacked at the same X11 position). "Alternation" is just `wmctrl -i -a <window_id>` raising whichever of the two is currently wanted — no window creation/destruction at runtime, which is what made the earlier open/close-per-flip approach fragile under load (see `PUPA_ARCHITECTURE.md` known-bugs section for the CPU/GPU angle on that fragility).

## Performance notes (Linux rig specifically)

The Linux rig's GPU (AMD Radeon HD 7770 "Cape Verde", ~2012) is genuinely weak for this workload. Established findings, don't re-test these:
- `hw_decode: true` on the ffmpeg video sources makes things **worse** (severe frame skipping) — ruled out conclusively 2026-07-14, leave `hw_decode: false`.
- Always-on GPU-active filters regardless of scene visibility (native "Scale To Sound", "Noise Displacement") were a major CPU/GPU sink — replaced with Shadertastic equivalents 2026-07-14.
- The same class of problem (OBS at 150-200% CPU) **recurred 2026-07-17** after the 2026-07-15 restructuring added several `Gradient` sources — not yet confirmed as the cause, `radeontop` is the right tool to check live GPU usage per-filter if this needs re-diagnosing.
