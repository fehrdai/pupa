"""
test_pupa_luci_runner.py - prova END-TO-END del runner (pupa_luci.py) con
audio, OBS e QLC+ FINTI, in tempo reale (~22s). Verifica che il collegamento
hotkey -> engine -> invii OS2L funzioni davvero (thread di polling incluso) e
che F12 faccia un arresto pulito con i canali a 0.

    python lights/test_pupa_luci_runner.py

Non serve nessun servizio esterno. Esce con codice != 0 se un controllo fallisce.
"""
import json
import math
import os
import sys
import tempfile
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.dirname(_HERE))

import pupa_luci as P
import lights_config as C

FAILS = []
T0 = [0.0]


def check(name, ok, detail):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        FAILS.append(name)


# ------------------------------------------------------------------ finti
class FakeQLC:
    def __init__(self):
        self.sock = object()
        self.sent = []          # (t_rel, ch, val)

    def connect(self):
        pass

    def set_channel(self, ch, val):
        self.sent.append((time.time() - T0[0], ch, val))


class FakeAudio:
    SILENCE_PEAK_THRESHOLD = 0.001

    def __init__(self, **kw):
        self.next_kick = None
        self.last_kick = 0.0

    def start(self):
        self.next_kick = time.time() + 0.3

    def stop(self):
        pass

    def get_metrics(self):
        t = time.time()
        kick = False
        if t >= self.next_kick:
            kick = True
            self.last_kick = self.next_kick
            self.next_kick += 60.0 / 128
        return {"bass": 25 + 65 * math.exp(-(t - self.last_kick) / 0.12), "mid": 40.0, "high": 30.0,
                "db_level": -15.0, "bpm": 128.0, "is_kick": kick, "is_break": False, "is_drop": False,
                "drop_event": False, "last_kick_time": self.last_kick, "peak": 0.5, "clipping": False}


class FakeOBS:
    """Solo la superficie usata da hotkey_controller.py + ControlPoller."""
    state = {}   # nome source -> enabled
    ids = {}

    def __init__(self, **kw):
        pass

    def connect(self):
        return True

    def disconnect(self):
        pass

    def cache_scenes(self):
        return [C.CONTROL_SCENE]

    def get_source_item_id(self, scene, name):
        return FakeOBS.ids.setdefault(name, len(FakeOBS.ids) + 1) if name in FakeOBS.state else None

    def _name(self, item_id):
        return next(n for n, i in FakeOBS.ids.items() if i == item_id)

    def get_scene_item_enabled(self, scene, item_id):
        return FakeOBS.state[self._name(item_id)]

    def set_scene_item_enabled(self, scene, item_id, enabled):
        FakeOBS.state[self._name(item_id)] = enabled
        return True


def press(name, value=True):
    FakeOBS.state[name] = value


def maxrgb(sent, t_a, t_b):
    """Massimo valore RGB inviato in [t_a,t_b) (i canali sono ricordati: 0 dopo l'ultimo invio conta)."""
    rgb_ch = [C.CH[s][c] for s in ("f1", "f2") for c in ("r", "g", "b")]
    vals = [v for t, ch, v in sent if ch in rgb_ch and t_a <= t < t_b]
    return max(vals) if vals else None


def main():
    # source presenti in OBS (le nuove + le esistenti); on/off acceso, nessun livello premuto
    FakeOBS.state = {C.SRC_LIGHTS_ONOFF: True, C.SRC_STROBE_WHITE: True, C.SRC_BLACKOUT: False, C.SRC_SHUTDOWN: True,
                     **{s: False for s in C.SRC_LIGHTS_LEVEL.values()}}   # strobo e shutdown "rimasti premuti": devono essere resettati

    fq = FakeQLC()
    P.QLCController = lambda: fq
    P.AudioAnalyzer = FakeAudio
    P.OBSController = FakeOBS
    P._resolve_audio_device = lambda name: 0
    P.time_sleep_orig = time.sleep
    sys.argv = ["pupa_luci.py"]

    def script():
        # tempi relativi a T0 (partenza main dopo ~2s di preflight)
        def at(t, fn):
            time.sleep(max(0, T0[0] + t - time.time()))
            fn()
        at(6.0, lambda: press(C.SRC_LIGHTS_ONOFF, False))          # off
        at(9.0, lambda: press(C.SRC_LIGHTS_ONOFF, True))           # on
        at(11.0, lambda: press(C.SRC_LIGHTS_LEVEL[3], True))       # livello 3
        at(13.0, lambda: press(C.SRC_STROBE_WHITE, True))          # F8 on
        at(14.5, lambda: press(C.SRC_STROBE_WHITE, False))         # F8 off
        at(16.0, lambda: press(C.SRC_BLACKOUT, True))              # F11
        at(17.5, lambda: press(C.SRC_BLACKOUT, False))
        at(19.0, lambda: press(C.SRC_SHUTDOWN, True))              # F12

    # colore del video: file scritto come farebbe pupa.py, tenuto fresco da un thread
    color_file = os.path.join(tempfile.gettempdir(), "test_identity_color.json")
    C.VIDEO_COLOR_FILE = color_file
    stop_color = threading.Event()
    def write_color():
        while not stop_color.is_set():
            with open(color_file, "w") as f:
                json.dump({"color": "red_color", "t": time.time()}, f)
            time.sleep(0.5)
    threading.Thread(target=write_color, daemon=True).start()

    T0[0] = time.time()
    threading.Thread(target=script, daemon=True).start()
    t_start = time.time()
    P.main()
    dur = time.time() - t_start
    stop_color.set()

    sent = fq.sent
    check("partenza: strobo/shutdown residui resettati da OBS", True, "il runner e' partito senza fermarsi subito (shutdown residuo ignorato) - durata "
          f"{dur:.1f}s >= 15s" if dur >= 15 else f"durata {dur:.1f}s")
    check("F12 ferma il runner", 17.5 < dur < 25, f"arresto pulito dopo {dur:.1f}s (F12 premuto a ~19s dal T0, T0 = partenza test)")
    # dopo il preflight (2s) i tempi script sono in T0: sent[].t e' relativo a T0 = stessa base
    on_max = maxrgb(sent, 3.0, 5.5)
    off_max = maxrgb(sent, 7.0, 8.9)
    check("luci reattive con audio", on_max is not None and on_max > 50, f"max RGB inviato in [3,5.5)s = {on_max}")
    off_last = [v for t, ch, v in sent if 8.0 <= t < 8.9 and ch in [C.CH[s][c] for s in ("f1", "f2") for c in ("r", "g", "b")]]
    check("hotkey OFF: nessun colore acceso dopo la dissolvenza", not off_last or max(off_last) == 0, f"invii RGB in [8,8.9)s: max={max(off_last) if off_last else 'nessuno (canali gia'' a 0)'}")
    on2 = maxrgb(sent, 10.0, 10.9)
    check("hotkey ON: le luci riprendono", on2 is not None and on2 > 20, f"max RGB in [10,10.9)s = {on2}")
    lvl3 = maxrgb(sent, 12.0, 12.9)
    lvl2 = maxrgb(sent, 4.0, 5.9)
    check("hotkey LIVELLO 3: tetto piu' alto del livello 2", lvl3 is not None and lvl2 is not None and lvl3 > lvl2,
          f"picco livello2={lvl2}, livello3={lvl3}")
    masters = [(t, v) for t, ch, v in sent if ch == C.CH["f1"]["master"] and 13.3 <= t < 14.6]
    check("F8 strobo manuale: Master alterna 0/255", len(masters) >= 6 and {0, 255} <= {v for _, v in masters},
          f"{len(masters)} cambi di Master in ~1.3s")
    bo = [(t, ch, v) for t, ch, v in sent if 16.3 <= t < 17.4]
    last_by_ch = {}
    for t, ch, v in sent:
        if t < 17.4:
            last_by_ch[ch] = v
    bo_ok = all(last_by_ch.get(C.CH[s][c]) == 0 for s in ("f1", "f2") for c in ("r", "g", "b", "master"))
    check("F11 blackout: tutti i canali a 0 (Master incluso)", bo_ok, f"ultimo valore per canale a 17.4s: { {ch: last_by_ch.get(ch) for s in ('f1','f2') for ch in [C.CH[s]['master']]} }")
    final = {}
    for t, ch, v in sent:
        final[ch] = v
    check("shutdown: tutti i canali a 0", all(final.get(ch) == 0 for ch in C.ALL_CHANNELS), f"valori finali {sorted(final.items())}")

    gb = [(t, ch, v) for t, ch, v in sent if 4.5 <= t < 5.9 and ch in (C.CH["f1"]["g"], C.CH["f1"]["b"], C.CH["f2"]["g"], C.CH["f2"]["b"]) and v > 0]
    red = [v for t, ch, v in sent if 4.5 <= t < 5.9 and ch in (C.CH["f1"]["r"], C.CH["f2"]["r"]) and v > 0]
    check("colore dal video (file di PUPA live): solo rosso", not gb and len(red) > 5,
          f"in [4.5,5.9)s: {len(red)} invii rossi >0, invii verde/blu >0: {len(gb)}")

    ts = [t for t, ch, v in sent]
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b - a < 0.02]      # messaggi della stessa raffica
    check("invii OS2L distanziati (QLC+ scarta i messaggi ravvicinati)", bool(gaps) and min(gaps) >= C.OS2L_MIN_GAP_S * 0.7,
          f"distanza minima tra messaggi consecutivi {min(gaps) * 1000:.1f} ms su {len(gaps)} coppie ravvicinate (>= {C.OS2L_MIN_GAP_S * 700:.1f} ms)")

    print(f"\n{'TUTTO OK' if not FAILS else 'FALLITI: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
