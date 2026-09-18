"""
lights_engine.py - motore luci PUPA, INDIPENDENTE dal video (PU.luci, 2026-09-19).

Modulo PURO: nessun OBS, nessun QLC+, nessun audio device, nessun orologio
interno (il tempo e' sempre passato da fuori) - cosi' si testa offline con
audio sintetico (vedi test_lights_engine.py). Il runner (pupa_luci.py) lo
alimenta con AudioAnalyzer.get_metrics() e manda il frame a QLC+.

Modello (design concordato 2026-09-19):
  - il BASS e' l'intensita': ogni kick accende un faro alla sua ampiezza
    (kick forti = piu' luminosi) con un decadimento agganciato al BPM; in piu'
    un inseguitore continuo del bass tiene le luci reattive anche se i kick
    non vengono rilevati, e un pavimento proporzionale ai medi impedisce i
    "buchi a nero" tra un kick e l'altro (43% di (0,0,0) osservato nei log
    del 2026-08-01);
  - PING-PONG sui kick: ogni kick va all'altro faro (guidato dal suono, non
    dalle battute - il vecchio conteggio a battute e' la causa del bug
    "stallo" dei monitor e non viene riusato);
  - PALETTE PROPRIA: coppia di primari RGB, un colore per faro, che ruota a
    ogni frase musicale (PHRASE_BEATS beat) e sui drop; il nuovo colore entra
    quando il faro e' al buio (cambio invisibile) - mai legato all'identita'
    colore del video;
  - BREAK (is_break): i kick vengono ignorati, le luci respirano lente;
  - DROP: strobo bianco proprio (Master frame-accurate) con cooldown;
  - overrides: on/off con dissolvenza, livello 1-3 (tetto di intensita'),
    strobo bianco manuale (F8), blackout (F11).

Uscita di tick(): {"f1": (r,g,b,master), "f2": (r,g,b,master)}, interi 0-255.
"""
import math
import random

import lights_config as C


def _clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def _ease(dt, tau):
    """Coefficiente di un filtro esponenziale a 1 polo per un passo dt."""
    return 1.0 - math.exp(-dt / tau) if tau > 0 else 1.0


COLOR_PAIRS = [(a, b) for a in C.COLORS for b in C.COLORS if a != b]  # coppie ordinate, un colore per faro


class LightsEngine:
    def __init__(self, rng=None, on=C.DEFAULT_ON, level=C.DEFAULT_LEVEL):
        self.rng = rng or random.Random()
        self.on = bool(on)
        self.level = level if level in C.LEVEL_SCALE else C.DEFAULT_LEVEL
        self.blackout = False
        self.strobe_manual = False

        self.t_last = None
        self.power = 0.0                  # gain globale 0..1 (dissolvenza on/off/silenzio)
        self.silent_since = None
        self.silent = False               # silenzio SOSTENUTO (fa dissolvere a nero)
        self.quiet = True                 # sotto SILENCE_DB adesso (spegne pavimento/kick subito)

        self.bpm = 0.0
        self.env = [0.0, 0.0]             # polso a kick per faro
        self.follow = 0.0                 # inseguitore del bass continuo
        self.mid = 0.0
        self.kick_side = 1                # lato dell'ULTIMO kick; il prossimo va all'altro (parte da 0)
        self.last_kick_t = -1e9

        self.break_since = None
        self.notbreak_since = None
        self.in_break = False
        self.break_mix = 0.0

        self.pair = self.rng.choice(COLOR_PAIRS)
        self.side_color = list(self.pair)
        self.pending_pair = None
        self.pending_since = 0.0
        self.pending_applied = [True, True]
        self.last_rotate_t = None

        self.burst_active = False
        self.burst_k = 0
        self.burst_next_t = 0.0
        self.burst_interval = 0.1
        self.last_drop_t = -1e9

        self.inten = [0.0, 0.0]           # intensita' finale 0..1 per faro (post break, pre gain) - per log/test
        self._events = []
        self.stats = {"ticks": 0, "kicks": 0, "kicks_ignored_break": 0, "drops": 0, "drops_ignored": 0,
                      "rotations": 0, "breaks": 0}

    # ------------------------------------------------------------------ API
    def set_on(self, on):
        on = bool(on)
        if on != self.on:
            self.on = on
            self._ev(f"ON/OFF -> {'on' if on else 'off'}")

    def set_level(self, level):
        if level in C.LEVEL_SCALE and level != self.level:
            self.level = level
            self._ev(f"LIVELLO -> {level} (x{C.LEVEL_SCALE[level]})")

    def set_blackout(self, active):
        active = bool(active)
        if active != self.blackout:
            self.blackout = active
            self._ev(f"BLACKOUT -> {active}")

    def set_manual_strobe(self, active):
        active = bool(active)
        if active != self.strobe_manual:
            self.strobe_manual = active
            self._ev(f"STROBO MANUALE -> {active}")

    def pop_events(self):
        ev, self._events = self._events, []
        return ev

    def _ev(self, msg):
        self._events.append(msg)

    # ----------------------------------------------------------------- tick
    def tick(self, t, m):
        """t: secondi (qualunque scala monotona, in produzione time.time()).
        m: dict di AudioAnalyzer.get_metrics() (chiavi mancanti = default)."""
        dt = 1.0 / C.TICK_HZ if self.t_last is None else _clamp(t - self.t_last, 0.0, 0.25)
        self.t_last = t
        self.stats["ticks"] += 1

        bass = _clamp(m.get("bass", 0.0) / 100.0, 0.0, 1.0)
        mid = _clamp(m.get("mid", 0.0) / 100.0, 0.0, 1.0)
        db = m.get("db_level", -60.0)
        bpm = m.get("bpm", 0.0) or 0.0
        kick = bool(m.get("is_kick", False))
        drop = bool(m.get("drop_event", False))
        is_break = bool(m.get("is_break", False))
        beat = 60.0 / bpm if bpm > 0 else None
        self.bpm = bpm

        # --- segnale presente / silenzio sostenuto
        self.quiet = db < C.SILENCE_DB
        if self.quiet:
            if self.silent_since is None:
                self.silent_since = t
            self.silent = (t - self.silent_since) >= C.SILENCE_HOLD_S
        else:
            self.silent_since = None
            self.silent = False

        # --- gain globale (on/off + silenzio) con dissolvenza
        target = 1.0 if (self.on and not self.silent) else 0.0
        step = dt / (C.FADE_IN_S if target > self.power else C.FADE_OUT_S)
        self.power = _clamp(self.power + _clamp(target - self.power, -step, step), 0.0, 1.0)

        # --- break con isteresi
        if is_break:
            self.notbreak_since = None
            if self.break_since is None:
                self.break_since = t
            if not self.in_break and (t - self.break_since) >= C.BREAK_ENTER_S:
                self.in_break = True
                self.stats["breaks"] += 1
                self._ev("BREAK entrato")
        else:
            self.break_since = None
            if self.notbreak_since is None:
                self.notbreak_since = t
            # Un kick vero (o un drop) e' la prova che il break e' finito: non
            # si aspetta BREAK_EXIT_S, altrimenti i primi kick dopo il break
            # (spesso proprio il drop) verrebbero ignorati.
            if self.in_break and ((t - self.notbreak_since) >= C.BREAK_EXIT_S or kick or drop):
                self.in_break = False
                self._ev("BREAK uscito")
                self._request_rotate(t, "uscita break")

        # --- kick: ping-pong, ampiezza dal bass
        if kick and not self.quiet:
            if self.in_break:
                self.stats["kicks_ignored_break"] += 1
            else:
                side = 1 - self.kick_side
                self.kick_side = side
                strength = C.KICK_STRENGTH_MIN + (1.0 - C.KICK_STRENGTH_MIN) * bass
                self.env[side] = max(self.env[side], strength)
                self.last_kick_t = t
                self.stats["kicks"] += 1
                self._ev(f"KICK lato={side + 1} forza={strength:.2f}")

        # --- decadimento del polso agganciato al BPM
        tau = _clamp(C.KICK_DECAY_BEAT_FRAC * beat, C.KICK_DECAY_MIN_S, C.KICK_DECAY_MAX_S) if beat else C.KICK_DECAY_NO_BPM_S
        k = math.exp(-dt / tau)
        self.env[0] *= k
        self.env[1] *= k

        # --- inseguitore continuo del bass + pavimento dai medi
        f_target = 0.0 if self.quiet else bass
        self.follow += (f_target - self.follow) * _ease(dt, C.FOLLOW_ATTACK_S if f_target > self.follow else C.FOLLOW_RELEASE_S)
        self.mid += ((0.0 if self.quiet else mid) - self.mid) * _ease(dt, 0.3)
        floor = 0.0 if self.quiet else C.MID_FLOOR_MIN + C.MID_FLOOR_RANGE * self.mid
        base = max(floor, C.FOLLOW_GAIN * (self.follow ** C.FOLLOW_EXPONENT))
        inten = [_clamp(max(self.env[0], base), 0.0, 1.0), _clamp(max(self.env[1], base), 0.0, 1.0)]

        # --- break: respiro lento al posto del normale (dissolvenza incrociata)
        self.break_mix += ((1.0 if self.in_break else 0.0) - self.break_mix) * _ease(dt, C.BREAK_MIX_TAU_S)
        if self.break_mix > 0.001:
            breath = C.BREAK_BREATH_MIN + (C.BREAK_BREATH_MAX - C.BREAK_BREATH_MIN) * \
                (0.5 - 0.5 * math.cos(2 * math.pi * t / C.BREAK_BREATH_PERIOD_S))
            inten = [i * (1 - self.break_mix) + breath * self.break_mix for i in inten]
        self.inten = inten

        # --- rotazione colori: a frase, e la applica quando il faro e' buio
        phrase_s = _clamp(C.PHRASE_BEATS * beat, C.PHRASE_S_MIN, C.PHRASE_S_MAX) if beat else C.PHRASE_S_NO_BPM
        if self.last_rotate_t is None:
            self.last_rotate_t = t
        elif t - self.last_rotate_t >= phrase_s:
            self._request_rotate(t, "frase")
        if self.pending_pair is not None:
            for i in (0, 1):
                if not self.pending_applied[i] and (inten[i] * self.power < C.COLOR_SWAP_DARK
                                                     or t - self.pending_since >= C.COLOR_SWAP_MAX_WAIT_S):
                    self.side_color[i] = self.pending_pair[i]
                    self.pending_applied[i] = True
            if all(self.pending_applied):
                self.pair = self.pending_pair
                self.pending_pair = None
                self._ev(f"COLORI -> {self.side_color[0]}/{self.side_color[1]}")

        # --- drop: strobo bianco proprio
        if drop:
            if (self.on and not self.quiet and not self.in_break and not self.burst_active
                    and t - self.last_drop_t >= C.DROP_STROBE_COOLDOWN_S):
                self.burst_active = True
                self.burst_k = 0
                self.burst_interval = _clamp(beat / C.DROP_STROBE_INTERVAL_BEAT_DIV, C.DROP_STROBE_INTERVAL_MIN_S,
                                             C.DROP_STROBE_INTERVAL_MAX_S) if beat else 0.1
                self.burst_next_t = t + self.burst_interval
                self.last_drop_t = t
                self.stats["drops"] += 1
                self._ev(f"DROP strobo {C.DROP_STROBE_FLASHES} lampi, mezzo periodo {self.burst_interval * 1000:.0f}ms")
                self._request_rotate(t, "drop")
            else:
                self.stats["drops_ignored"] += 1
        if self.burst_active:
            while self.burst_active and t >= self.burst_next_t:
                self.burst_k += 1
                self.burst_next_t += self.burst_interval
                if self.burst_k >= 2 * C.DROP_STROBE_FLASHES:
                    self.burst_active = False
        burst_on = self.burst_active and self.burst_k % 2 == 0

        # --- uscita
        if self.blackout:
            return {"f1": (0, 0, 0, 0), "f2": (0, 0, 0, 0)}
        if self.strobe_manual:
            on = int(t / C.STROBE_MANUAL_HALF_PERIOD_S) % 2 == 0
            v = 255 if on else 0
            return {"f1": (v, v, v, v), "f2": (v, v, v, v)}
        gain = self.power * C.LEVEL_SCALE[self.level]
        if self.burst_active:
            if burst_on:
                w = int(round(255 * C.LEVEL_SCALE[self.level]))
                return {"f1": (w, w, w, 255), "f2": (w, w, w, 255)}
            return {"f1": (0, 0, 0, 0), "f2": (0, 0, 0, 0)}
        out = {}
        for i, name in enumerate(("f1", "f2")):
            r, g, b = C.COLORS[self.side_color[i]]
            s = inten[i] * gain
            out[name] = (int(round(r * s)), int(round(g * s)), int(round(b * s)), 255)
        return out

    def _request_rotate(self, t, why):
        options = [p for p in COLOR_PAIRS if p != self.pair]
        self.pending_pair = self.rng.choice(options)
        self.pending_since = t
        self.pending_applied = [False, False]
        self.last_rotate_t = t
        self.stats["rotations"] += 1
        self._ev(f"ROTAZIONE colori richiesta ({why}) -> {self.pending_pair[0]}/{self.pending_pair[1]}")


class FrameSender:
    """Trasforma i frame in set_channel() inviando SOLO i canali cambiati
    (RGB con soglia MIN_SEND_STEP, 0/255 sempre) e rimandando tutto ogni
    RESYNC_S secondi - cosi' un QLC+ riavviato o un pacchetto perso si
    riallineano da soli invece di restare sbagliati fino al prossimo cambio."""
    RESYNC_S = 2.0

    def __init__(self, set_channel):
        self.set_channel = set_channel
        self.last = {}
        self.last_full = -1e9
        self.sent = 0

    def send(self, frame, t):
        force = (t - self.last_full) >= self.RESYNC_S
        if force:
            self.last_full = t
        n = 0
        for side, values in frame.items():
            for name, val in zip(("r", "g", "b", "master"), values):
                ch = C.CH[side][name]
                prev = self.last.get(ch)
                if not (force or prev is None or (val != prev and (abs(val - prev) >= C.MIN_SEND_STEP or val in (0, 255)))):
                    continue
                self.set_channel(ch, val)
                self.last[ch] = val
                n += 1
        self.sent += n
        return n
