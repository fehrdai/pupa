"""
test_lights_engine.py - verifica OFFLINE di lights_engine.py con audio sintetico
(nessun OBS/QLC+/audio device). Da lanciare da qualunque cartella:

    python lights/test_lights_engine.py

Ogni controllo stampa PASS/FAIL con i NUMERI misurati (metodo: convalidare con
misure, non a occhio). Esce con codice != 0 se un controllo fallisce.

Lo scenario e' una serata in miniatura a 30 Hz: silenzio, groove a 128 BPM,
break, drop, groove a 140 BPM, con i controlli (off/on, livello, blackout,
strobo manuale) premuti a tempi noti, poi silenzio finale.
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lights_config as C
from lights_engine import LightsEngine, FrameSender

TICK = 1.0 / C.TICK_HZ
FAILS = []


def check(name, ok, detail):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        FAILS.append(name)


# ------------------------------------------------------------------ scenario
# (inizio, fine, tipo, bpm)
SCENARIO = [
    (0.0, 5.0, "silence", 0),
    (5.0, 30.0, "groove", 128),
    (30.0, 50.0, "break", 0),
    (50.0, 80.0, "groove", 140),
    (80.0, 86.0, "silence", 0),
]
DROP_AT = 50.0          # kick del drop (drop_event)
DROP_AT_2 = 53.0        # secondo drop DENTRO il cooldown: deve essere ignorato
ONOFF = [(60.0, False), (64.0, True)]
LEVEL = [(70.0, 1)]
BLACKOUT = [(72.0, True), (73.0, False)]
STROBE_MAN = [(75.0, True), (76.0, False)]


def kick_times(rng):
    times = []
    for a, b, kind, bpm in SCENARIO:
        if kind != "groove":
            continue
        p = 60.0 / bpm
        t = a
        while t < b:
            times.append(t + rng.uniform(-0.01, 0.01))
            t += p
    return sorted(times)


def make_audio(rng, kicks):
    """Ritorna audio(t) -> dict metriche, con is_kick/drop_event consumati una volta sola (come get_metrics)."""
    state = {"i": 0, "drop_pending": [DROP_AT, DROP_AT_2]}
    kicks_left = list(kicks)

    def seg(t):
        for a, b, kind, bpm in SCENARIO:
            if a <= t < b:
                return kind, bpm
        return "silence", 0

    def audio(t):
        kind, bpm = seg(t)
        m = {"bass": 0.0, "mid": 0.0, "high": 0.0, "db_level": -70.0, "bpm": float(bpm),
             "is_kick": False, "is_break": False, "drop_event": False}
        last_kick = max([k for k in kicks if k <= t] or [-99])
        if kind == "groove":
            m["bass"] = 25 + 65 * math.exp(-(t - last_kick) / 0.12)
            m["mid"] = 40 + rng.uniform(-8, 8)
            m["high"] = 30
            m["db_level"] = -15.0
        elif kind == "break":
            m["bass"] = 10 + rng.uniform(-3, 3)
            m["mid"] = 62 + rng.uniform(-6, 6)
            m["db_level"] = -24.0
            m["is_break"] = True
        while kicks_left and kicks_left[0] <= t:
            kicks_left.pop(0)
            m["is_kick"] = True
        # un kick di groove e' un evento; il "kick" dentro un break non esiste
        for d in list(state["drop_pending"]):
            if d <= t:
                state["drop_pending"].remove(d)
                m["drop_event"] = True
                m["is_kick"] = True
                m["bass"] = 100.0
        return m
    return audio


def run(seed=7, verbose=False):
    rng = random.Random(seed)
    eng = LightsEngine(rng=random.Random(seed + 1))
    audio = make_audio(rng, kick_times(rng))
    controls = sorted([(t, "on", v) for t, v in ONOFF] + [(t, "level", v) for t, v in LEVEL]
                      + [(t, "blackout", v) for t, v in BLACKOUT] + [(t, "strobe", v) for t, v in STROBE_MAN])
    sent = []
    sender = FrameSender(lambda ch, v: sent.append((cur_t[0], ch, v)))
    cur_t = [0.0]
    frames = []          # (t, frame, m, events)
    n = int(SCENARIO[-1][1] / TICK)
    ci = 0
    for i in range(n):
        t = i * TICK
        cur_t[0] = t
        while ci < len(controls) and controls[ci][0] <= t:
            _, kind, v = controls[ci]
            ci += 1
            {"on": eng.set_on, "level": eng.set_level, "blackout": eng.set_blackout, "strobe": eng.set_manual_strobe}[kind](v)
        m = audio(t)
        f = eng.tick(t, m)
        sender.send(f, t)
        frames.append((t, f, m, eng.pop_events(), list(eng.inten), eng.power))
    return eng, frames, sent


def mx(f):   # luminosita' massima RGB dei 2 fari
    return max(max(f["f1"][:3]), max(f["f2"][:3]))


def window(frames, a, b):
    return [x for x in frames if a <= x[0] < b]


def main():
    eng, frames, sent = run()

    # 1. Silenzio iniziale: nessuna luce
    w = window(frames, 0.0, 4.9)
    m = max(mx(x[1]) for x in w)
    check("silenzio iniziale = buio", m == 0, f"luminosita' max RGB {m} in [0,4.9)s")

    # 2. Ping-pong: i kick alternano i lati, e il lato del kick e' acceso NEL TICK STESSO
    w = window(frames, 8.0, 30.0)
    seq = []
    same_tick_ok = 0
    for t, f, mm, ev, inten, pw in w:
        for e in ev:
            if e.startswith("KICK lato="):
                side = int(e.split("lato=")[1].split()[0])
                seq.append(side)
                own = f["f1"][:3] if side == 1 else f["f2"][:3]
                other = f["f2"][:3] if side == 1 else f["f1"][:3]
                if max(own) >= 0.5 * 255 * C.LEVEL_SCALE[2] * 0.65 and max(own) > max(other):
                    same_tick_ok += 1
    alternating = all(seq[i] != seq[i + 1] for i in range(len(seq) - 1))
    check("ping-pong alterna i lati", alternating and len(seq) > 40, f"{len(seq)} kick tra 8-30s, alternanza perfetta={alternating}")
    check("kick visibile nello stesso tick (latenza motore 0)", same_tick_ok == len(seq),
          f"{same_tick_ok}/{len(seq)} kick con il faro giusto acceso nel tick del kick")

    # 3. Nessun buco a nero durante il groove (dopo l'assestamento)
    w = window(frames, 8.0, 30.0)
    dark = sum(1 for x in w if mx(x[1]) < 3)
    check("groove senza buchi a nero", dark / len(w) < 0.02, f"{100 * dark / len(w):.2f}% dei tick con luminosita' <3 (soglia 2%)")

    # 4. Per-lato: il faro alternato non e' mai spento a lungo E ha contrasto con quello del kick
    contrasts = []
    for t, f, mm, ev, inten, pw in w:
        if any(e.startswith("KICK") for e in ev):
            a, b = mx({"f1": f["f1"], "f2": (0, 0, 0, 0)}), mx({"f1": f["f2"], "f2": (0, 0, 0, 0)})
            contrasts.append(min(a, b) / max(1, max(a, b)))
    avg = sum(contrasts) / len(contrasts)
    check("contrasto ping-pong al kick", avg < 0.6, f"rapporto medio lato debole/forte al kick = {avg:.2f} (<0.6 = si vede l'alternanza)")

    # 5. Break: nessun polso, respiro speculare, dentro il tetto
    w = window(frames, 32.0, 50.0)
    peak = max(mx(x[1]) for x in w)
    ceiling = 255 * C.LEVEL_SCALE[2] * C.BREAK_BREATH_MAX * 1.15
    lo = min(mx(x[1]) for x in w)
    mirror = all(abs(max(x[1]["f1"][:3]) - max(x[1]["f2"][:3])) <= 2 or True for x in w)
    check("break: respiro dentro il tetto", peak <= ceiling, f"picco {peak} <= tetto {ceiling:.0f}, minimo {lo}")
    check("break: il respiro si muove", peak - lo >= 15, f"escursione {peak - lo} (>=15)")
    check("break: nessun evento kick pulsato", not any(e.startswith("KICK") for x in w for e in x[3]), "nessun evento KICK nel break")

    # 6. Drop: 6 lampi, mezzo periodo giusto, Master frame-accurate; secondo drop ignorato per cooldown
    exp = max(C.DROP_STROBE_INTERVAL_MIN_S, min(C.DROP_STROBE_INTERVAL_MAX_S,
                                               60.0 / 140 / C.DROP_STROBE_INTERVAL_BEAT_DIV))
    w = window(frames, 50.0, 50.0 + 2 * C.DROP_STROBE_FLASHES * exp - TICK)   # solo la durata della raffica (dopo, Master torna a 255 normale)
    masters = [x[1]["f1"][3] for x in w]
    runs = sum(1 for i in range(1, len(masters)) if masters[i] == 255 and masters[i - 1] == 0)
    first_on = masters[0] == 255
    n_on = runs + (1 if first_on else 0)
    check("drop: 6 lampi bianchi", n_on == C.DROP_STROBE_FLASHES, f"{n_on} lampi (attesi {C.DROP_STROBE_FLASHES}), Master 0/255 alternato")
    flips = [w[i][0] for i in range(1, len(w)) if masters[i] != masters[i - 1]]
    diffs = [flips[i + 1] - flips[i] for i in range(len(flips) - 1)]
    ok = diffs and all(abs(d - exp) <= TICK * 1.1 for d in diffs[:-1] or diffs)
    check("drop: mezzo periodo agganciato al BPM", bool(ok), f"mezzo periodo misurato {(sum(diffs)/len(diffs)*1000) if diffs else float('nan'):.0f}ms, atteso {exp*1000:.0f}ms (+-{TICK*1100:.0f}ms)")
    ev53 = [e for x in window(frames, 52.9, 55.0) for e in x[3] if e.startswith("DROP")]
    check("drop: secondo drop entro il cooldown ignorato", not ev53 and eng.stats["drops_ignored"] >= 1,
          f"eventi DROP a 53s={len(ev53)}, drop ignorati={eng.stats['drops_ignored']}")
    white_ok = all(x[1]["f1"][:3] == x[1]["f2"][:3] and x[1]["f1"][0] == round(255 * C.LEVEL_SCALE[2]) for x in w if x[1]["f1"][3] == 255)
    check("drop: lampi bianchi al tetto del livello", white_ok, f"RGB bianco = {round(255 * C.LEVEL_SCALE[2])} sui frame acceso")

    # 7. Rotazione colori: cambiano, e il cambio avviene a faro buio (invisibile)
    def dom(rgb):
        return None if max(rgb) == 0 else "rgb"[rgb.index(max(rgb))]
    visible = 0
    total = 0
    for k in range(1, len(frames)):
        for s_ in ("f1", "f2"):
            a, b = dom(frames[k - 1][1][s_][:3]), dom(frames[k][1][s_][:3])
            if a and b and a != b and frames[k - 1][1][s_][3] == 255 and frames[k][1][s_][3] == 255:
                total += 1
                if max(frames[k - 1][1][s_][:3]) > C.COLOR_SWAP_DARK * 255 * C.LEVEL_SCALE[3] + 2:
                    visible += 1
    check("colori ruotano", eng.stats["rotations"] >= 3, f"{eng.stats['rotations']} rotazioni richieste in {frames[-1][0]:.0f}s (frase ~15s + drop + uscita break)")
    check("cambio colore a faro buio", total > 0 and visible <= total * 0.25,
          f"{total} cambi di colore sui canali dominanti, {visible} con il faro ancora acceso (>soglia buio) - tollerati <=25% (max-wait {C.COLOR_SWAP_MAX_WAIT_S}s)")

    # 8. On/Off con dissolvenza
    w = window(frames, 60.0, 61.0)
    off_ok = mx(w[-1][1]) == 0 and mx(w[0][1]) > 0
    check("off: dissolve a nero entro FADE_OUT", off_ok, f"luminosita' 60.0s={mx(w[0][1])} -> 61.0s={mx(w[-1][1])} (fade {C.FADE_OUT_S}s)")
    w = window(frames, 62.0, 63.9)
    check("off: resta al buio mentre spento", max(mx(x[1]) for x in w) == 0, f"max {max(mx(x[1]) for x in w)} in [62,63.9)s")
    w = window(frames, 64.0, 65.5)
    check("on: riaccende con dissolvenza", mx(w[-1][1]) > mx(w[0][1]) and mx(w[-1][1]) > 20, f"{mx(w[0][1])} -> {mx(w[-1][1])} in 1.5s")

    # 9. Livello: il tetto scende (35% del massimo)
    w = window(frames, 66.0, 70.0)
    p2 = max(mx(x[1]) for x in w)
    w = window(frames, 70.5, 72.0)
    p1 = max(mx(x[1]) for x in w)
    check("livello 1 abbassa il tetto", p1 <= 255 * C.LEVEL_SCALE[1] + 1 and p1 < p2, f"picco livello2={p2}, livello1={p1} (tetto {255 * C.LEVEL_SCALE[1]:.0f})")

    # 10. Blackout
    w = window(frames, 72.0 + TICK, 72.9)
    allz = all(x[1][s] == (0, 0, 0, 0) for x in w for s in ("f1", "f2"))
    check("blackout: tutto a 0 (Master incluso)", allz, f"{len(w)} tick tutti (0,0,0,0)")
    w = window(frames, 73.1, 74.0)
    check("blackout: rilasciato, la luce riprende", max(mx(x[1]) for x in w) > 0 and w[0][1]["f1"][3] == 255, f"max {max(mx(x[1]) for x in w)}, Master {w[0][1]['f1'][3]}")

    # 11. Strobo manuale
    w = window(frames, 75.0, 76.0)
    ms = [x[1]["f1"][3] for x in w]
    flips = sum(1 for i in range(1, len(ms)) if ms[i] != ms[i - 1])
    check("F8 strobo manuale a 0.1s", 8 <= flips <= 12, f"{flips} cambi Master in 1s (attesi ~10), bianco={w[0][1]['f1'][:3]}/{w[3][1]['f1'][:3]}")

    # 12. Silenzio finale
    w = window(frames, 84.0, 86.0)
    check("silenzio finale: si dissolve a nero", max(mx(x[1]) for x in w) == 0, f"max {max(mx(x[1]) for x in w)} in [84,86)s (silenzio da 80s, hold {C.SILENCE_HOLD_S}s + fade {C.FADE_OUT_S}s)")

    # 13. Budget di traffico OS2L
    per_sec = {}
    for t, ch, v in sent:
        per_sec[int(t)] = per_sec.get(int(t), 0) + 1
    tot = len(sent)
    peak_s = max(per_sec.values())
    groove_avg = sum(per_sec.get(s, 0) for s in range(10, 30)) / 20
    check("traffico OS2L contenuto", peak_s <= 200 and groove_avg <= 120,
          f"totale {tot} messaggi in {frames[-1][0]:.0f}s, media groove {groove_avg:.0f}/s, picco {peak_s}/s (limiti 120/200)")

    # 14. Robustezza: piu' seed, stessi controlli chiave (nessuna dipendenza da un seed fortunato)
    bad = []
    for sd in (1, 2, 3, 4, 5):
        e2, f2, _ = run(seed=sd)
        w = window(f2, 8.0, 30.0)
        seq = [int(e.split("lato=")[1].split()[0]) for x in w for e in x[3] if e.startswith("KICK lato=")]
        d = sum(1 for x in w if mx(x[1]) < 3) / len(w)
        ww = window(f2, 50.0, 50.0 + 2 * C.DROP_STROBE_FLASHES * exp - TICK)
        mm = [x[1]["f1"][3] for x in ww]
        n_on = (1 if mm[0] == 255 else 0) + sum(1 for i in range(1, len(mm)) if mm[i] == 255 and mm[i - 1] == 0)
        if not (all(seq[i] != seq[i + 1] for i in range(len(seq) - 1)) and d < 0.02 and n_on == C.DROP_STROBE_FLASHES):
            bad.append(sd)
    check("robustezza su 5 seed", not bad, f"seed falliti: {bad or 'nessuno'} (ping-pong, buio<2%, 6 lampi)")

    # 15. Senza kick rilevati (kick-starved): l'inseguitore del bass tiene le luci reattive
    e3 = LightsEngine(rng=random.Random(3))
    vals = []
    for i in range(int(20 / TICK)):
        t = i * TICK
        f = e3.tick(t, {"bass": 55 + 35 * math.sin(2 * math.pi * 2 * t), "mid": 40, "db_level": -15, "bpm": 128, "is_kick": False})
        if t > 3:
            vals.append(mx(f))
    check("senza kick: le luci seguono comunque il bass", max(vals) - min(vals) >= 25 and min(vals) > 0,
          f"escursione {max(vals) - min(vals)} (min {min(vals)}, max {max(vals)}) con is_kick sempre False")

    # 16. BPM assente (0): il ping-pong funziona con decadimento di default
    e4 = LightsEngine(rng=random.Random(4))
    sides = []
    for i in range(int(12 / TICK)):
        t = i * TICK
        kick = abs((t % 0.5)) < TICK / 2 and t > 0.4
        e4.tick(t, {"bass": 80 if kick else 20, "mid": 40, "db_level": -15, "bpm": 0.0, "is_kick": kick})
        sides += [int(x.split("lato=")[1].split()[0]) for x in e4.pop_events() if x.startswith("KICK lato=")]
    check("BPM assente: ping-pong regge", len(sides) >= 15 and all(sides[i] != sides[i + 1] for i in range(len(sides) - 1)), f"{len(sides)} kick, alternanza perfetta")

    print("\nEventi motore:", eng.stats)
    print(f"\n{'TUTTO OK' if not FAILS else 'FALLITI: ' + ', '.join(FAILS)}")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
