"""
PUPA LUCI - runner autonomo delle luci QLC+ (PU.luci, 2026-09-19).

Processo SEPARATO da pupa.py: cattura l'audio per conto suo, decide con
lights_engine.py (reattivo al suono, indipendente dal video) e parla da solo
con QLC+ via OS2L. Si avvia/riavvia senza toccare il video:

    cd ~/Desktop/pupa && python3 lights/pupa_luci.py            # Linux (rig live)
    python lights/pupa_luci.py --no-obs                          # senza hotkey OBS (test)

Hotkey (source fittizie in PUPA_Control, stesso trucco di hotkey_controller.py;
il tasto reale si lega in OBS > Impostazioni > Hotkey, vedi LIGHTS_CONFIG.md):
    PUPA_LUCI               Mostra = accese / Nascondi = spente (dissolvenza)
    PUPA_LUCI_LIVELLO_1..3  livello di intensita' (solo Mostra, autopulizia)
    PUPA_STROBE_WHITE (F8)  strobo bianco manuale a toggle
    PUPA_BLACKOUT (F11)     buio totale (Master incluso) finche' attivo
    PUPA_SHUTDOWN (F12)     arresto pulito (pupa.py ascolta la stessa source)

Il polling di OBS gira in un thread a parte: una chiamata WebSocket lenta non
puo' mai ritardare un frame di luce.

Riusa i moduli condivisi (audio_analyzer/qlc_controller/obs_controller/
hotkey_controller/shutdown_helpers) dalla cartella padre, come exhibition/.
"""
import argparse
import os
import subprocess
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
# chdir: i logger condivisi (debug_logger.py, audio_analyzer.py) scrivono in
# ./logs relativo alla cartella corrente - qui finiscono in lights/logs/,
# mai mischiati con quelli di pupa.py (due processi sullo stesso file rotante
# si pesterebbero alla rotazione).
os.chdir(_HERE)
sys.path.insert(0, _PARENT)
sys.path.insert(0, _HERE)

import sounddevice as sd

import lights_config as C
from lights_engine import LightsEngine, FrameSender
from debug_logger import setup_debug_logger
from qlc_controller import QLCController
from audio_analyzer import AudioAnalyzer
from obs_controller import OBSController
from hotkey_controller import BinaryControl, MultiLevelControl
from shutdown_helpers import shutdown_step, spegni_luci_qlc

try:
    from secrets_local import OBS_HOST, OBS_PORT, OBS_PASSWORD, AUDIO_DEVICE_NAME
except ImportError:
    print("[ERROR] secrets_local.py mancante o incompleto (cartella padre).")
    sys.exit(1)


def _opt(name):
    try:
        import secrets_local
        return getattr(secrets_local, name)
    except (ImportError, AttributeError):
        return None


PULSE_SOURCE = _opt("PULSE_SOURCE")
AUDIO_INPUT_GAIN_PCT = _opt("AUDIO_INPUT_GAIN_PCT")
KICK_THRESHOLD_BASS_MIN = _opt("KICK_THRESHOLD_BASS_MIN")
KICK_THRESHOLD_BASS_DELTA = _opt("KICK_THRESHOLD_BASS_DELTA")
if PULSE_SOURCE:
    os.environ.setdefault("PULSE_SOURCE", PULSE_SOURCE)

_log = setup_debug_logger(name="pupa_luci", log_file="lights.log")
lights_log = _log.debug


def say(msg):
    """Console + lights.log."""
    print(msg)
    lights_log(msg)


def _resolve_audio_device(name):
    """Come pupa.py: per nome ad ogni avvio, mai per indice (instabile su PipeWire)."""
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d["max_input_channels"] > 0:
            return i
    raise RuntimeError(f"Device audio '{name}' non trovato (list_audio_devices.py, poi secrets_local.py).")


def _set_capture_gain(pulse_source, gain_pct):
    try:
        subprocess.run(["pactl", "set-source-volume", pulse_source, f"{gain_pct}%"],
                       check=True, capture_output=True, timeout=5)
        say(f"[AUDIO] Gain di cattura {gain_pct}% su '{pulse_source}'")
    except Exception as e:
        say(f"[AUDIO] WARN: gain di cattura non impostato ({e})")


class AsyncQLC:
    """Invii OS2L in un thread a parte, con coalescenza per canale (vale
    sempre l'ULTIMO valore richiesto). Trovato dal test di fumo su Windows: con
    QLC+ spento, ogni tentativo di riconnessione di QLCController blocca il
    chiamante ~2s (il rifiuto di connessione su loopback Windows non e'
    immediato) - dentro il loop luci sarebbe un buco di 2s ogni 5s. Qui il
    loop non aspetta mai la rete."""

    def __init__(self, qlc):
        self.qlc = qlc
        self._pending = {}
        self._lock = threading.Lock()
        self._evt = threading.Event()
        self._stop = False
        self._thread = threading.Thread(target=self._run, daemon=True, name="luci-qlc-send")
        self._thread.start()

    def set_channel(self, ch, val):
        with self._lock:
            self._pending[ch] = val
        self._evt.set()

    def _run(self):
        while not self._stop:
            self._evt.wait(0.5)
            self._evt.clear()
            with self._lock:
                batch, self._pending = self._pending, {}
            for ch, val in batch.items():
                self.qlc.set_channel(ch, val)

    def stop(self):
        """Ferma il thread (svuota prima quello che e' in coda)."""
        self._stop = True
        self._evt.set()
        self._thread.join(timeout=5)


class ControlPoller(threading.Thread):
    """Legge gli hotkey OBS in un thread a parte e ne espone lo stato con un
    lock. Se OBS non c'e' (o cade) il resto funziona coi valori di default e
    la connessione viene ritentata ogni CONTROL_RECONNECT_S."""

    def __init__(self, use_obs=True):
        super().__init__(daemon=True, name="luci-controls")
        self.use_obs = use_obs
        self.lock = threading.Lock()
        self.state = {"on": C.DEFAULT_ON, "level": C.DEFAULT_LEVEL, "blackout": False,
                      "strobe": False, "shutdown": False, "connected": False}
        self.obs = None
        self.ctl = {}
        self._stop_evt = threading.Event()

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def _set(self, **kw):
        with self.lock:
            self.state.update(kw)

    def connect_once(self):
        """Connette e risolve le source; imposta lo stato di partenza da OBS
        (on/off e livello persistono tra i riavvii, come CALM MODE)."""
        if not self.use_obs:
            return False
        obs = OBSController(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD)
        if not obs.connect():
            say("[HOTKEY] OBS non raggiungibile - hotkey luci disattivati (default: accese, livello "
                f"{C.DEFAULT_LEVEL}); riprovo ogni {C.CONTROL_RECONNECT_S:.0f}s")
            return False
        scenes = obs.cache_scenes()
        ctl = {
            "onoff": BinaryControl("Luci on/off", C.CONTROL_SCENE, C.SRC_LIGHTS_ONOFF),
            "level": MultiLevelControl("Luci livello", C.CONTROL_SCENE, C.SRC_LIGHTS_LEVEL),
            "strobe": BinaryControl("Strobo bianco manuale", C.CONTROL_SCENE, C.SRC_STROBE_WHITE),
            "blackout": BinaryControl("Blackout", C.CONTROL_SCENE, C.SRC_BLACKOUT),
            "shutdown": BinaryControl("Shutdown", C.CONTROL_SCENE, C.SRC_SHUTDOWN),
        }
        for c in ctl.values():
            c.resolve(obs, scenes)
        # Stato di partenza da OBS.
        init = {"connected": True}
        if ctl["onoff"].active:
            init["on"] = bool(obs.get_scene_item_enabled(C.CONTROL_SCENE, ctl["onoff"].item_id))
        if ctl["level"].active and ctl["level"].resolved_level in C.LEVEL_SCALE:
            init["level"] = ctl["level"].resolved_level
        if ctl["blackout"].active:
            init["blackout"] = bool(obs.get_scene_item_enabled(C.CONTROL_SCENE, ctl["blackout"].item_id))
        # Reset di sicurezza (come pupa.py): strobo manuale e shutdown non devono
        # ereditare uno stato "premuto" rimasto da un processo morto - il
        # blackout NO, e' condiviso con pupa.py che lo resetta al suo avvio.
        for key in ("strobe", "shutdown"):
            c = ctl[key]
            if c.active and obs.get_scene_item_enabled(C.CONTROL_SCENE, c.item_id):
                c.force(obs, False)
                say(f"[HOTKEY] {c.name}: resettata a spenta all'avvio (era rimasta attiva)")
        self.obs, self.ctl = obs, ctl
        self._set(**init)
        say(f"[HOTKEY] stato iniziale da OBS: {init}")
        return True

    def run(self):
        last_try = time.monotonic()
        while not self._stop_evt.wait(C.CONTROL_POLL_S):
            if self.obs is None:
                if self.use_obs and time.monotonic() - last_try >= C.CONTROL_RECONNECT_S:
                    last_try = time.monotonic()
                    try:
                        self.connect_once()
                    except Exception as e:
                        lights_log(f"[HOTKEY] riconnessione fallita: {e}")
                continue
            try:
                r = self.ctl["onoff"].poll(self.obs)
                if r is not None:
                    self._set(on=r)
                r = self.ctl["level"].poll(self.obs)
                if r in C.LEVEL_SCALE:
                    self._set(level=r)
                r = self.ctl["strobe"].poll(self.obs)
                if r is not None:
                    self._set(strobe=r)
                r = self.ctl["blackout"].poll(self.obs)
                if r is not None:
                    self._set(blackout=r)
                r = self.ctl["shutdown"].poll(self.obs)
                if r:
                    self._set(shutdown=True)
            except Exception as e:
                say(f"[HOTKEY] OBS caduto ({e}) - controlli ai valori correnti, riprovo")
                self.obs = None
                self._set(connected=False)
                last_try = time.monotonic()

    def stop(self):
        self._stop_evt.set()
        try:
            if self.obs:
                self.obs.disconnect()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description="PUPA luci (QLC+), reattive al suono, indipendenti dal video")
    ap.add_argument("--no-obs", action="store_true", help="non usare OBS (nessun hotkey): default acceso, livello 2")
    args = ap.parse_args()

    print("=" * 70)
    print("  PUPA LUCI - reattive al suono, indipendenti dal video")
    print("=" * 70)

    qlc = QLCController()
    qlc.connect()
    say("[QLC] Connesso (OS2L)" if qlc.sock is not None else "[QLC] Non raggiungibile - riprovo da solo (throttled)")

    ctrl = ControlPoller(use_obs=not args.no_obs)
    ctrl.connect_once()

    if PULSE_SOURCE and AUDIO_INPUT_GAIN_PCT is not None:
        _set_capture_gain(PULSE_SOURCE, AUDIO_INPUT_GAIN_PCT)
    device = _resolve_audio_device(AUDIO_DEVICE_NAME)
    say(f"[AUDIO] Device '{AUDIO_DEVICE_NAME}' -> index {device}")
    audio = AudioAnalyzer(device=device, kick_threshold_bass_min=KICK_THRESHOLD_BASS_MIN,
                          kick_threshold_bass_delta=KICK_THRESHOLD_BASS_DELTA)
    audio.start()
    time.sleep(2)
    pf = audio.get_metrics()
    if pf.get("clipping"):
        say(f"[AUDIO] ALERT: CLIPPING gia' in preflight (picco={pf.get('peak', 0):.2f})")
    elif pf.get("peak", 0) < AudioAnalyzer.SILENCE_PEAK_THRESHOLD:
        say(f"[AUDIO] ALERT: nessun segnale in preflight (picco={pf.get('peak', 0):.3f})")
    else:
        say(f"[AUDIO] Preflight OK: picco={pf.get('peak', 0):.2f}")

    snap = ctrl.snapshot()
    engine = LightsEngine(on=snap["on"], level=snap["level"])
    engine.set_blackout(snap["blackout"])
    engine.pop_events()
    aqlc = AsyncQLC(qlc)
    sender = FrameSender(aqlc.set_channel)
    ctrl.start()
    say(f"[LUCI] partenza: on={snap['on']} livello={snap['level']} blackout={snap['blackout']} "
        f"tick={C.TICK_HZ}Hz palette={list(C.COLORS)}")

    period = 1.0 / C.TICK_HZ
    next_t = time.monotonic()
    w = {"t0": time.time(), "ticks": 0, "sends": 0, "kicks": 0, "lat": [], "gap_max": 0.0,
         "dark": 0, "live": 0}
    last_tick_wall = None
    try:
        while True:
            now = time.time()
            snap = ctrl.snapshot()
            if snap["shutdown"]:
                say("[LUCI] F12 shutdown ricevuto")
                raise SystemExit(0)
            engine.set_on(snap["on"])
            engine.set_level(snap["level"])
            engine.set_blackout(snap["blackout"])
            engine.set_manual_strobe(snap["strobe"])

            m = audio.get_metrics()
            frame = engine.tick(now, m)
            w["sends"] += sender.send(frame, now)

            # --- misure per lights.log
            w["ticks"] += 1
            if last_tick_wall is not None:
                w["gap_max"] = max(w["gap_max"], now - last_tick_wall)
            last_tick_wall = now
            if m.get("is_kick"):
                w["kicks"] += 1
                lk = m.get("last_kick_time", 0.0)
                if lk > 0:
                    w["lat"].append(now - lk)   # rilevamento nel callback audio -> qui (poi +~qualche ms di rete OS2L)
            if engine.power > 0.5 and not engine.in_break and not engine.quiet and not snap["blackout"]:
                w["live"] += 1
                if max(max(frame["f1"][:3]), max(frame["f2"][:3])) < 3:
                    w["dark"] += 1
            for ev in engine.pop_events():
                if ev.startswith("KICK"):
                    lights_log(f"[EV] {ev}")
                else:
                    say(f"[EV] {ev}")

            if now - w["t0"] >= C.SUMMARY_EVERY_S:
                dur = now - w["t0"]
                lat = w["lat"]
                say(f"[SUM] {dur:.0f}s tick={w['ticks'] / dur:.1f}/s invii={w['sends'] / dur:.0f}/s "
                    f"kick={w['kicks']} lat_kick_ms(media/max)={(1000 * sum(lat) / len(lat)) if lat else 0:.0f}/"
                    f"{(1000 * max(lat)) if lat else 0:.0f} gap_loop_max_ms={1000 * w['gap_max']:.0f} "
                    f"buio%={(100 * w['dark'] / w['live']) if w['live'] else 0:.1f} "
                    f"bpm={engine.bpm:.0f} power={engine.power:.2f} livello={engine.level} on={engine.on} "
                    f"break={engine.in_break} colori={engine.side_color[0]}/{engine.side_color[1]} "
                    f"qlc={'ok' if qlc.sock is not None else 'GIU'} obs={'ok' if snap['connected'] else 'no'}")
                w.update({"t0": now, "ticks": 0, "sends": 0, "kicks": 0, "lat": [], "gap_max": 0.0, "dark": 0, "live": 0})

            next_t += period
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                next_t = time.monotonic()   # in ritardo: riparti da adesso, niente raffica di recupero
    except KeyboardInterrupt:
        say("[STOP] Ctrl+C ricevuto")
    except SystemExit:
        pass
    except Exception as e:
        import traceback
        say(f"[ERROR] {e}")
        traceback.print_exc()
    finally:
        say("[LUCI] Arresto pulito.")
        # prima si ferma il thread di invio (niente race con lo spegnimento), poi
        # si spegne dal thread principale col QLCController vero.
        shutdown_step("stop invii QLC+", aqlc.stop)
        shutdown_step("spegni luci QLC+", lambda: spegni_luci_qlc(qlc, C.ALL_CHANNELS))
        shutdown_step("stop audio", audio.stop)
        shutdown_step("stop controlli OBS", ctrl.stop)


if __name__ == "__main__":
    main()
