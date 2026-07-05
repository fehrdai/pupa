"""
PUPA Brain Module - v0.6 Unified Energy-Reactive Model
- 7-state machine: Intro, Build, Groove, Break, Drop, Peak, Relax
- A/B couples: 1:1 mapping (every _A paired with one _B)
- INTRO/BREAK: alternanza wave_kick <-> _A (prevalenza wave_kick decrescente con l'energia)
- BUILD/GROOVE/DROP/PEAK/RELAX: ciclo A<->B, STESSA pool random (Burn/Displace/Blur + Cut)
  per ENTRAMBE le direzioni, con velocita'/frequenza/probabilita' di Cut che scalano
  con lo stato E con il bass live (piu' energia = piu' veloce e piu' cut, mai sotto una
  soglia minima visibile)
"""

import random
import time
from collections import deque
from enum import Enum
from logger import log_decision
from debug_logger import debug as debug_log

class State(Enum):
    """7 stati della macchina musicale"""
    INTRO = "intro"        # Inizio basso, transizioni lente
    BUILD = "build"        # Energie crescenti, velocità aumenta
    GROOVE = "groove"      # Stabile, ritmo costante
    BREAK = "break"        # Calo energia, transizioni rare
    DROP = "drop"          # Picco bass, massima velocità
    PEAK = "peak"          # Apice, A+B overlay
    RELAX = "relax"        # Discesa post-peak

# COUPLES: ogni _A ha un POOL di possibili _B (non un abbinamento fisso 1:1).
# Ripristinato dal design originale v0.4 (COUPLES_CONFIG.yaml, "b_pool" per
# coppia, curato per tema/atmosfera: es. "Urban/Cyber", "Psicodelic"...).
# "projectx_B" rimosso su richiesta esplicita. Pool ridotti da 3 a 2 opzioni
# per coppia (la variet' con 3 risultava eccessiva secondo l'utente),
# scegliendo le rimozioni anche per ridurre la sovrapposizione tra coppie
# diverse (es. urbanfree_A e kusanagi_A condividevano 2 scene su 3).
COUPLES = {
    "urbanfree_A":   ["spectrumbar_B", "tunnelwave_B"],
    "psicodance_A":  ["stormlightning_B", "waveform1_B"],
    "montezuma_A":   ["roundedbar_B", "waveform2_B"],
    "kusanagi_A":    ["radialspike_B", "stormlightning_B"],
    "mri_A":         ["waveform1_B", "ring_B"],
    "futureflash_A": ["roundedbar_B", "waveform2_B"],
    "segnali_A":     ["spectrumbar_B", "ring_B"],
}

# Pool di transizioni "di coppia" per il ciclo energetico A<->B.
# STESSA pool usata per ENTRAMBE le direzioni (A->B e B->A), scelta randomica.
# Fade escluso di proposito: riservato alla fase INTRO/BREAK e al ritorno da wave_kick,
# per evitare che domini percettivamente il ciclo principale (problema gia' riscontrato).
COUPLE_TRANSITIONS = {
    "urbanfree_A": ["Blur", "Displace"],
    "psicodance_A": ["Displace", "Burn"],
    "montezuma_A": ["Burn", "Blur"],
    "kusanagi_A": ["Burn", "Blur"],
    "mri_A": ["Displace", "Blur"],
    "futureflash_A": ["Burn", "Displace"],
    "segnali_A": ["Burn", "Displace"],
}

# Durata (ms) del ciclo energetico A<->B per stato: scala col crescendo musicale,
# con un floor implicito (mai sotto ~150ms) per restare visibile.
CYCLE_TRANSITION_DURATION_MS = {
    State.BUILD:  900,
    State.GROOVE: 700,
    State.DROP:   250,
    State.PEAK:   180,
    State.RELAX:  1100,
}

# Probabilita' di usare un Cut ("Taglio") al posto di Burn/Displace/Blur, per stato.
# Rarefatta/quasi assente a bassa energia, "pompa" (aumenta) con l'energia.
CUT_PROBABILITY_BY_STATE = {
    State.BUILD:  0.10,
    State.GROOVE: 0.25,
    State.DROP:   0.55,
    State.PEAK:   0.75,
    State.RELAX:  0.05,
}

# Debounce (frequenza minima tra gli switch) per stato: piu' frequente con l'energia.
STATE_PARAMS = {
    State.INTRO:  {"debounce": 1.5},   # Lento
    State.BUILD:  {"debounce": 0.4},   # Accelera
    State.GROOVE: {"debounce": 0.3},   # Veloce
    State.BREAK:  {"debounce": 1.5},   # Pausa
    State.DROP:   {"debounce": 0.15},  # Massima velocita'
    State.PEAK:   {"debounce": 0.2},   # Strobo
    State.RELAX:  {"debounce": 1.2},   # Discesa lenta
}

# Probabilita' che un kick mentre siamo su _A ci faccia REALMENTE passare a
# _B, invece di essere "assorbito" (kick ignorato, si resta su _A). Il
# ritorno da _B a _A resta invece sempre immediato al kick successivo (non
# toccato) — questo crea l'asimmetria voluta: _A domina il tempo a schermo,
# _B resta un'apparizione breve e occasionale invece di alternarsi 50/50.
PROB_ENTER_B_ON_KICK = 0.4

# Raffica strobo/flash: alterna rapidamente strobo_B <-> scena di base, N volte,
# poi atterra sulla scena target normale. Implementata come piccola macchina a stati
# che avanza di UN frame per ogni chiamata a decide_next_scene() (NON bloccante:
# niente time.sleep, il loop a 20Hz continua a girare libero tra un frame e l'altro).
STROBE_SCENE = "strobo_B"
STROBE_BURST_COUNT = 4          # numero di flash (ON+OFF) per raffica
STROBE_BURST_INTERVAL = 0.10    # secondi tra un frame e l'altro della raffica
STROBE_BURST_PROBABILITY = {
    State.PEAK: 0.35,
    State.DROP: 0.15,
}
STROBE_TRANSITION_CHOICES = ["Taglio", "White Fade"]  # provate entrambe, scelta random ad ogni raffica

# SOVRAPPOSIZIONE: "peek" verso l'altra scena, spinto al 40-60% di blend, mantenuto
# li' per un tempo prolungato, poi tornato indietro (0%) alla scena di partenza.
# Non e' un "hold" che completa verso il target: e' un'anteprima che poi si annulla.
# Applicabile a TUTTE le transizioni della macchina a stati (ciclo A<->B, DROP,
# wave_kick<->A in intro/break). Tecnicamente realizzata interrompendo un fade
# in corso con uno nuovo in direzione opposta (OBS riparte dal blend attuale).
OVERLAP_PROBABILITY = 0.15         # "ogni tanto" (stati calmi fuori da intro/break: RELAX)
OVERLAP_PROBABILITY_INTRO_BREAK = 0.25  # piu' presenza in intro/break
# Stati in cui la musica "spinge" (energia alta/in salita): la sovrapposizione
# NON deve MAI scattare qui. Congelando tutti i kick per 0.5-7s ogni volta che
# scatta, durante un passaggio energico (kick ogni 0.15-0.3s in DROP/PEAK)
# toglieva reattivita' proprio quando serve di piu'. Riservata agli stati
# calmi (INTRO/BREAK/RELAX) dove e' un accento gradito, non un intoppo.
OVERLAP_PUSHING_STATES = (State.BUILD, State.GROOVE, State.DROP, State.PEAK)
OVERLAP_TRANSITION_CHOICES = ["Fade", "Dissolvenza"]
OVERLAP_HOLD_B_TO_A = (2.0, 4.0)   # secondi: sovrapposizione prolungata (arrivo verso _A)
OVERLAP_HOLD_A_TO_B = (0.5, 2.0)   # secondi: sovrapposizione piu' breve (arrivo verso _B)
OVERLAP_TARGET_BLEND = (0.4, 0.6)  # frazione di blend raggiunta durante l'hold

# wave_kick: permanenza minima prima di poter tornare a _A (deve avere spazio
# visibile). Alzato da 3.0 a 6.0: l'utente segnala che wave_kick resta troppo
# poco visibile nei passaggi rispetto alle scene _A.
MIN_WAVE_KICK_DWELL = 6.0  # secondi

# Sovrapposizione che coinvolge wave_kick specificamente (entrata o ritorno):
# hold piu' lungo del range generico OVERLAP_HOLD_B_TO_A/A_TO_B, per dargli
# ancora piu' presenza quando appare in una sovrapposizione.
OVERLAP_HOLD_WAVE_KICK = (4.0, 7.0)  # secondi

# Recupero post-BREAK: se l'energia risale in fretta dopo un break, wave_kick
# continua ad avere spazio anche in BUILD/GROOVE verso il PEAK
POST_BREAK_RECOVERY_WINDOW = 25.0       # secondi dopo l'uscita da BREAK
POST_BREAK_RISE_RATE_THRESHOLD = 3.0    # unita' di bass/secondo per "risalita veloce"

class HybridCouplesModel:
    def __init__(self):
        self.current_couple_a = "urbanfree_A"
        # Ultima scena _B REALMENTE mostrata (non un tentativo intermedio
        # scartato per assorbimento) — usata per l'anti-repeat vero, vedi
        # _roll_next_b_scene()
        self.last_shown_b_scene = None
        self.current_b_scene = self._roll_next_b_scene()
        self.in_scene_a = True

        self.couple_start_time = 0
        self.COUPLE_DURATION = 240  # 4 minuti

        self.couple_history = deque(maxlen=5)
        self.last_switch_time = 0

        # STATE MACHINE
        self.current_state = State.INTRO
        self.state_start_time = 0
        self.bass_history = deque(maxlen=30)  # Per calcolare trend energy
        self.last_bass = 0  # Ultimo valore bass live, per modulazione continua
        self.last_bass_avg = 0  # Ultima media bass, per calcolare la velocita' del break

        # Tracciamento uscita da BREAK: per estendere lo spazio di wave_kick
        # in BUILD/GROOVE durante una risalita rapida post-break
        self.break_exit_time = None
        self.bass_at_break_exit = 0

        self.temp_b_scene = None  # Temp override per wave_kick
        self.temp_b_scene_time = 0  # Timestamp quando è stato settato

        # Direzione dell'ultima transizione decisa (impostata ESPLICITAMENTE
        # in decide_next_scene PRIMA di flippare in_scene_a, cosi'
        # get_transition_info() non deve re-derivarla da uno stato gia' mutato)
        self.last_transition_is_return = False

        # Raffica strobo/flash (non bloccante, avanza un frame per tick)
        self.burst_active = False
        self.burst_step = -1
        self.burst_total_steps = 0
        self.burst_next_time = 0
        self.burst_back_scene = None
        self.burst_return_scene = None
        self.burst_return_is_a = True
        self.burst_transition_choice = "Taglio"
        self.last_decision_kind = "normal"  # "normal" | "burst_step" | "burst_end" | "overlap_forward" | "overlap_reverse"

        # Sovrapposizione (peek + ritorno, non bloccante)
        self.overlap_active = False
        self.overlap_base_scene = None       # scena di partenza, a cui si torna a fine overlap
        self.overlap_hold_until = 0
        self.overlap_forward_duration_ms = 0
        self.overlap_reverse_duration_ms = 0
        self.overlap_transition_choice = "Fade"

    def initialize(self, current_scene, current_time):
        """Inizializza stato e coppia"""
        if current_scene.endswith("_A"):
            self.current_couple_a = current_scene
            self.in_scene_a = True
        else:
            self.current_couple_a = "urbanfree_A"
            self.in_scene_a = False

        self.current_b_scene = self._roll_next_b_scene()
        self.couple_start_time = current_time
        self.state_start_time = current_time
        self.current_state = State.INTRO
        self.couple_history.append(self.current_couple_a)
        self.last_switch_time = current_time

    def _select_new_couple(self):
        """Seleziona nuova coppia (esclude ultime 5)"""
        available = [a for a in COUPLES.keys() if a not in self.couple_history]
        if not available:
            available = list(COUPLES.keys())
        new_couple = random.choice(available)
        self.couple_history.append(new_couple)
        return new_couple

    def _select_b_scene(self, couple_a, exclude=None):
        """Sceglie una scena _B dal pool della coppia data. Se il pool ha piu'
        di un'opzione, evita di ripetere `exclude` (l'ultima usata) — cosi' la
        rotazione e' realmente percepibile, non solo teoricamente possibile."""
        pool = COUPLES.get(couple_a, [])
        if not pool:
            return None
        if exclude and len(pool) > 1:
            choices = [b for b in pool if b != exclude]
            if choices:
                return random.choice(choices)
        return random.choice(pool)

    def _roll_next_b_scene(self):
        """Sceglie e COMMITTA la prossima scena _B da mostrare, evitando di
        ripetere l'ultima REALMENTE mostrata (self.last_shown_b_scene).

        NOTA: non usare _select_b_scene() direttamente per rerollare "nel
        dubbio" (es. ad ogni kick anche se poi assorbito) — con piu'
        tentativi scartati di fila, escludere solo l'ultimo TENTATIVO (non
        l'ultimo MOSTRATO) permette di tornare per caso sulla stessa scena
        gia' vista, vanificando l'anti-repeat. Chiamare questo metodo solo
        nel momento in cui si e' certi che la transizione a _B avverra'
        davvero (dentro burst/overlap/switch diretto)."""
        self.current_b_scene = self._select_b_scene(self.current_couple_a, exclude=self.last_shown_b_scene)
        self.last_shown_b_scene = self.current_b_scene
        return self.current_b_scene

    def _advance_burst(self, current_time, current_scene, logger):
        """Avanza di UN frame la raffica strobo attiva (assume self.burst_active == True).

        Alterna strobo_B <-> scena di base per burst_total_steps frame, poi atterra
        sulla scena target (burst_return_scene) decisa al momento del trigger.
        """
        self.burst_step += 1
        self.last_switch_time = current_time

        if self.burst_step >= self.burst_total_steps:
            self.burst_active = False
            self.in_scene_a = self.burst_return_is_a
            self.last_transition_is_return = self.burst_return_is_a
            self.last_decision_kind = "burst_end"

            log_decision(
                from_scene=current_scene,
                to_scene=self.burst_return_scene,
                reason="STROBE BURST fine",
                energy="STROBE",
                duration=0.1,
                logger=logger
            )
            return self.burst_return_scene

        self.burst_next_time = current_time + STROBE_BURST_INTERVAL
        self.last_decision_kind = "burst_step"
        target = STROBE_SCENE if (self.burst_step % 2 == 0) else self.burst_back_scene

        log_decision(
            from_scene=current_scene,
            to_scene=target,
            reason=f"STROBE BURST frame {self.burst_step + 1}/{self.burst_total_steps}",
            energy="STROBE",
            duration=STROBE_BURST_INTERVAL,
            logger=logger
        )
        return target

    def _maybe_trigger_overlap(self, peek_target_scene, current_time, current_scene, logger, probability=None):
        """Prova ad innescare una SOVRAPPOSIZIONE invece di uno switch normale.

        Se innescata: peek verso peek_target_scene (blend 40-60%), mantenuto per un
        tempo prolungato (2-4s se il peek arriva verso _A, 0.5-2s se verso _B), poi
        ritorno alla scena di partenza (nessun cambio scena netto). Non tocca
        self.in_scene_a: la sovrapposizione e' solo visiva, non un vero switch.

        Ritorna peek_target_scene se innescata, altrimenti None (il chiamante
        procede con lo switch normale).
        """
        prob = probability if probability is not None else OVERLAP_PROBABILITY
        if random.random() >= prob:
            return None

        peek_is_return = not self.in_scene_a  # il peek va verso _A se partiamo da _B

        # wave_kick coinvolto (in entrata o in ritorno): hold piu' lungo,
        # dedicato, per dargli piu' presenza (vedi MIN_WAVE_KICK_DWELL sopra)
        wave_kick_involved = peek_target_scene == "wave_kick" or current_scene == "wave_kick"

        if wave_kick_involved:
            hold_time = random.uniform(*OVERLAP_HOLD_WAVE_KICK)
        elif peek_is_return:
            hold_time = random.uniform(*OVERLAP_HOLD_B_TO_A)
        else:
            hold_time = random.uniform(*OVERLAP_HOLD_A_TO_B)

        target_pct = random.uniform(*OVERLAP_TARGET_BLEND)
        forward_ms = max(150, int((hold_time * 1000) / target_pct))
        reverse_ms = random.randint(300, 600)

        self.overlap_active = True
        self.overlap_base_scene = current_scene
        self.overlap_hold_until = current_time + hold_time
        self.overlap_forward_duration_ms = forward_ms
        self.overlap_reverse_duration_ms = reverse_ms
        self.overlap_transition_choice = random.choice(OVERLAP_TRANSITION_CHOICES)

        self.last_transition_is_return = peek_is_return
        self.last_decision_kind = "overlap_forward"
        self.last_switch_time = current_time

        log_decision(
            from_scene=current_scene,
            to_scene=peek_target_scene,
            reason=f"SOVRAPPOSIZIONE peek {target_pct*100:.0f}% (hold {hold_time:.1f}s, {self.overlap_transition_choice})",
            energy="OVERLAP",
            duration=forward_ms / 1000,
            logger=logger
        )
        return peek_target_scene

    def _post_break_rise_rate(self, current_time, bass):
        """Velocita' di risalita del bass dall'ultima uscita da BREAK (unita'/secondo).
        0 se non siamo (o non siamo piu', oltre la finestra) in recupero da un break."""
        if self.break_exit_time is None:
            return 0.0
        elapsed = current_time - self.break_exit_time
        if elapsed <= 0 or elapsed > POST_BREAK_RECOVERY_WINDOW:
            return 0.0
        return max(0.0, (bass - self.bass_at_break_exit) / elapsed)

    def _wave_kick_eligible(self, current_time):
        """True se siamo in una fase dove wave_kick puo' comparire: sempre in
        INTRO/BREAK, oppure in BUILD/GROOVE se siamo dentro la finestra di
        recupero veloce da un break recente."""
        if self.current_state in (State.INTRO, State.BREAK):
            return True
        if self.current_state in (State.BUILD, State.GROOVE) and self.break_exit_time is not None:
            elapsed = current_time - self.break_exit_time
            return 0 <= elapsed <= POST_BREAK_RECOVERY_WINDOW
        return False

    def _update_state(self, bass, bass_avg, couple_elapsed, current_time):
        """Aggiorna stato musicale basato su energia audio

        Classifica sulla MEDIA (bass_avg, ~1.4s), non sul valore istantaneo:
        su house/techno con basso quasi continuo, il singolo blocco FFT
        (~46ms) cade quasi sempre vicino a un kick o alla sua coda, restando
        strutturalmente alto anche nei passaggi piu' calmi - misurato dal
        vivo su un liveset reale (Ling Ling, 7min, gain pulito/no clipping):
        bass istantaneo 62.8-100 (media 92.6), MAI sotto 63, risultando in
        PEAK/DROP quasi assoluti e zero GROOVE/RELAX/BREAK. energy_trend
        resta sul confronto istante-vs-media (serve a intercettare un picco
        sopra la media in corso, es. un vero drop).
        """
        energy = bass_avg
        energy_trend = bass - bass_avg if bass_avg > 0 else 0

        # Logica semplice: associa energia a stato
        if couple_elapsed < 30:
            new_state = State.INTRO
        elif energy > 80 and energy_trend > 10:
            new_state = State.DROP
        elif energy > 85:
            new_state = State.PEAK
        elif energy > 70:
            new_state = State.BUILD
        elif energy > 50:
            new_state = State.GROOVE
        elif energy < 20:
            new_state = State.BREAK
        else:
            new_state = State.RELAX

        # Transizione stato
        if new_state != self.current_state:
            self.current_state = new_state
            self.state_start_time = current_time

    def _get_overlap_probability(self):
        """Probabilita' di sovrapposizione basata sullo stato corrente: ZERO
        durante gli stati in cui la musica spinge (BUILD/GROOVE/DROP/PEAK),
        presente negli stati calmi (INTRO/BREAK con presenza maggiore, RELAX
        con quella base) dove resta un accento gradito senza congelare la
        reattivita' quando serve di piu'."""
        if self.current_state in OVERLAP_PUSHING_STATES:
            return 0.0
        if self.current_state in (State.INTRO, State.BREAK):
            return OVERLAP_PROBABILITY_INTRO_BREAK
        return OVERLAP_PROBABILITY  # RELAX

    def _get_debounce(self):
        """Ritorna debounce basato su stato corrente

        BREAK e' reattivo alla velocita' del crollo bass: un break brusco
        (bass scende rapidamente) riduce il debounce fino al 70%, un break
        lento (calo graduale) resta sul debounce base.
        """
        base = STATE_PARAMS[self.current_state]["debounce"]
        if self.current_state == State.BREAK:
            drop_rate = max(0.0, self.last_bass_avg - self.last_bass)
            drop_factor = min(1.0, drop_rate / 30.0)
            return max(0.3, base * (1.0 - 0.7 * drop_factor))
        return base

    def _get_fade_ms(self):
        """Ritorna la durata di transizione usata per il display nei log"""
        if self.current_state in (State.INTRO, State.BREAK):
            return 2000
        return CYCLE_TRANSITION_DURATION_MS.get(self.current_state, 800)

    def _is_return_transition(self):
        """Ritorna True se la transizione appena decisa è B→A (return), False per A→B

        NOTA: non deriva da self.in_scene_a perche' decide_next_scene() lo aggiorna
        (flip) PRIMA di ritornare la scena, quindi al momento in cui questa funzione
        viene chiamata (da pupa.py, dopo decide_next_scene) in_scene_a rappresenta
        gia' lo stato di ARRIVO, non quello di partenza. Usiamo invece il flag
        esplicito settato da decide_next_scene() al momento della decisione.
        """
        return self.last_transition_is_return

    def _get_transition_info(self):
        """Ritorna tipo e durata della transizione

        - Raffica strobo attiva (frame intermedio): Taglio o White Fade, veloce
        - wave_kick (entrata): SEMPRE Stinger, esclusivo
        - wave_kick (ritorno a _A): Fade
        - INTRO/BREAK (ciclo wave_kick<->_A): Fade
        - Ciclo energetico principale (BUILD/GROOVE/DROP/PEAK/RELAX): STESSA pool
          random (Burn/Displace/Blur + Cut) per ENTRAMBE le direzioni A->B e B->A;
          velocita' e probabilita' di Cut scalano con lo stato e col bass live.
        """
        is_return = self._is_return_transition()

        # Sovrapposizione: leg di andata (peek) o di ritorno (base)
        if self.last_decision_kind == "overlap_forward":
            debug_log(f"[TRANS] SOVRAPPOSIZIONE peek: {self.overlap_transition_choice} {self.overlap_forward_duration_ms}ms")
            return {
                "type": self.overlap_transition_choice,
                "duration_ms": self.overlap_forward_duration_ms,
                "is_return": is_return,
                "kick_mode": "overlap"
            }

        if self.last_decision_kind == "overlap_reverse":
            debug_log(f"[TRANS] SOVRAPPOSIZIONE ritorno: {self.overlap_transition_choice} {self.overlap_reverse_duration_ms}ms")
            return {
                "type": self.overlap_transition_choice,
                "duration_ms": self.overlap_reverse_duration_ms,
                "is_return": is_return,
                "kick_mode": "overlap"
            }

        # Frame intermedio della raffica strobo: transizione scelta al trigger,
        # durata legata all'intervallo del burst (niente pool/energia qui)
        if self.last_decision_kind == "burst_step":
            burst_duration_ms = int(STROBE_BURST_INTERVAL * 1000 * 0.7)
            debug_log(f"[TRANS] STROBE frame: {self.burst_transition_choice} {burst_duration_ms}ms")
            return {
                "type": self.burst_transition_choice,
                "duration_ms": burst_duration_ms,
                "is_return": False,
                "kick_mode": "strobe"
            }

        # Ritorno da wave_kick a _A: Fade (controllato PRIMA del check generico
        # sotto, perche' temp_b_scene resta "wave_kick" fino a qui: se il check
        # generico venisse prima, intercetterebbe anche il ritorno, non solo l'entrata)
        if is_return and self.temp_b_scene == "wave_kick":
            self.temp_b_scene = None  # Reset temp override
            debug_log(f"[TRANS] wave_kick -> A: Fade 2000ms")
            return {
                "type": "Fade",
                "duration_ms": 2000,
                "is_return": True,
                "kick_mode": "crescendo"
            }

        # FORCE: wave_kick SEMPRE Stinger in ENTRATA, bypassa tutto il resto
        if self.temp_b_scene == "wave_kick":
            debug_log(f"[TRANS] wave_kick -> Stinger 20s")
            return {"type": "Stinger", "duration_ms": 20000, "is_return": False, "kick_mode": "wave"}

        # BREAK: cut/fade alternati, reattivi alla velocita' del crollo bass
        # (break brusco -> piu' probabile Taglio veloce; break lento -> Fade)
        if self.current_state == State.BREAK:
            drop_rate = max(0.0, self.last_bass_avg - self.last_bass)
            cut_prob = min(0.8, drop_rate / 30.0)
            if random.random() < cut_prob:
                trans_type, duration_ms = "Taglio", 300
            else:
                trans_type, duration_ms = "Fade", 2000
            debug_log(f"[TRANS] BREAK reattivo: {trans_type} {duration_ms}ms (drop_rate={drop_rate:.1f}, cut_prob={cut_prob:.2f})")
            return {"type": trans_type, "duration_ms": duration_ms, "is_return": is_return}

        # INTRO: ciclo wave_kick<->_A, sempre Fade (Stinger gia' gestito sopra)
        if self.current_state == State.INTRO:
            debug_log(f"[TRANS] INTRO: Fade 2000ms")
            return {"type": "Fade", "duration_ms": 2000, "is_return": is_return}

        # Ciclo energetico principale: stessa pool random per A->B e B->A,
        # con Cut integrato a probabilita' crescente con l'energia (stato + bass live)
        couple_pool = COUPLE_TRANSITIONS.get(self.current_couple_a, ["Burn", "Displace"])
        base_duration = CYCLE_TRANSITION_DURATION_MS.get(self.current_state, 800)
        base_cut_prob = CUT_PROBABILITY_BY_STATE.get(self.current_state, 0.2)

        # Modulazione continua sul bass live: piu' energia = piu' veloce e piu' cut,
        # ma mai sotto una soglia minima visibile (150ms)
        bass_factor = min(1.0, max(0.0, self.last_bass / 100.0))
        duration = max(150, int(base_duration * (1.0 - 0.3 * bass_factor)))
        cut_prob = min(0.9, base_cut_prob + 0.2 * bass_factor)

        if random.random() < cut_prob:
            trans_type = "Taglio"
        else:
            trans_type = random.choice(couple_pool)

        direction = "B->A" if is_return else "A->B"
        debug_log(f"[TRANS] {direction} ciclo {self.current_couple_a}: {trans_type} {duration}ms "
                   f"(state={self.current_state.value}, cut_prob={cut_prob:.2f}, bass={self.last_bass:.0f})")

        return {
            "type": trans_type,
            "duration_ms": duration,
            "is_return": is_return
        }

    def decide_next_scene(self, audio_data, current_time, current_scene, logger):
        """
        Decisione scene con state machine + A/B logic:
        1. Aggiorna stato musicale da audio
        2. Timer coppia (4 min) → cambio coppia
        3. INTRO/BREAK: alternanza wave_kick <-> _A (prevalenza wave_kick decrescente)
        4. Altrimenti: ciclo A<->B reattivo a kick/drop, con debounce per stato
        """

        if not audio_data:
            return None

        bass = audio_data.get("bass", 0)
        bass_avg = audio_data.get("bass_avg", 0)
        is_kick = audio_data.get("is_kick", False)
        is_drop = audio_data.get("is_drop", False)

        self.last_bass = bass
        self.last_bass_avg = bass_avg
        couple_elapsed = current_time - self.couple_start_time

        # Default: nessun "kind" speciale finche' non impostato da un branch specifico
        self.last_decision_kind = "normal"

        # RAFFICA STROBO IN CORSO: priorita' assoluta, avanza un frame per tick
        # col proprio intervallo (non il debounce generico dello stato corrente)
        if self.burst_active:
            if current_time < self.burst_next_time:
                return None
            return self._advance_burst(current_time, current_scene, logger)

        # SOVRAPPOSIZIONE IN CORSO: priorita' assoluta, aspetta la fine dell'hold
        # poi torna alla scena di partenza (nessun cambio scena netto)
        if self.overlap_active:
            if current_time < self.overlap_hold_until:
                return None
            self.overlap_active = False
            self.last_transition_is_return = self.in_scene_a  # torniamo alla base, invariata
            self.last_decision_kind = "overlap_reverse"
            self.last_switch_time = current_time

            log_decision(
                from_scene=current_scene,
                to_scene=self.overlap_base_scene,
                reason="SOVRAPPOSIZIONE fine, ritorno a base",
                energy="OVERLAP",
                duration=self.overlap_reverse_duration_ms / 1000,
                logger=logger
            )
            return self.overlap_base_scene

        # TIMEOUT temp_b_scene (wave_kick max 20 secondi)
        if self.temp_b_scene == "wave_kick" and self.temp_b_scene_time > 0 and (current_time - self.temp_b_scene_time) > 20:
            debug_log(f"[TIMEOUT] wave_kick scaduto, reset")
            self.temp_b_scene = None
            self.temp_b_scene_time = 0

        # AGGIORNA STATO MUSICALE
        prev_state = self.current_state
        self.bass_history.append(bass)
        self._update_state(bass, bass_avg, couple_elapsed, current_time)

        # Rileva uscita da BREAK: serve per estendere lo spazio di wave_kick
        # in BUILD/GROOVE durante una risalita rapida (vedi _wave_kick_eligible)
        if prev_state == State.BREAK and self.current_state != State.BREAK:
            self.break_exit_time = current_time
            self.bass_at_break_exit = bass

        # ====================================================================
        # 1. TIMER COPPIA SCADUTO? (4 minuti)
        # ====================================================================
        if couple_elapsed > self.COUPLE_DURATION:
            self.current_couple_a = self._select_new_couple()
            self.current_b_scene = self._roll_next_b_scene()
            self.couple_start_time = current_time
            self.state_start_time = current_time
            self.current_state = State.INTRO
            self.in_scene_a = True
            self.last_switch_time = current_time
            self.last_transition_is_return = False  # Nuova coppia, ingresso "forward" su _A

            log_decision(
                from_scene=current_scene,
                to_scene=self.current_couple_a,
                reason=f"TIMER TIMER 4min ({couple_elapsed:.0f}s) | State: {self.current_state.value}",
                energy="CAMBIO COPPIA",
                duration=self._get_fade_ms() / 1000,
                logger=logger
            )
            return self.current_couple_a

        # ====================================================================
        # 2. DEBOUNCE dinamico (dipende da stato)
        # ====================================================================
        debounce = self._get_debounce()
        if (current_time - self.last_switch_time) < debounce:
            return None

        couple_pct = (couple_elapsed / self.COUPLE_DURATION) * 100

        # ====================================================================
        # 3. wave_kick <-> _A: sempre in INTRO/BREAK, oppure in BUILD/GROOVE
        #    durante una risalita rapida dopo un break recente ("piu' spazio
        #    a wave_kick dal break al crescendo, se e' veloce")
        # ====================================================================
        wave_eligible = self._wave_kick_eligible(current_time)
        intro_or_break = self.current_state in (State.INTRO, State.BREAK)
        overlap_prob = self._get_overlap_probability()

        # RITORNO da wave_kick: controllato SEMPRE se siamo attualmente su
        # wave_kick, INDIPENDENTEMENTE da wave_eligible/current_state (che nel
        # frattempo potrebbero essere cambiati, es. un picco improvviso verso
        # DROP/PEAK/RELAX mentre siamo ancora su wave_kick). Se questo controllo
        # dipendesse da wave_eligible, la permanenza minima verrebbe bypassata
        # in quel caso, facendo sparire wave_kick troppo in fretta.
        if self.temp_b_scene == "wave_kick" and not self.in_scene_a and is_kick:
            time_on_wave = current_time - self.temp_b_scene_time if self.temp_b_scene_time > 0 else 999
            if time_on_wave < MIN_WAVE_KICK_DWELL:
                self.last_switch_time = current_time
                return None

            self.last_switch_time = current_time

            # SOVRAPPOSIZIONE: possibilita' di un peek invece del ritorno diretto
            peek = self._maybe_trigger_overlap(self.current_couple_a, current_time, current_scene, logger, probability=overlap_prob)
            if peek:
                return peek

            self.in_scene_a = True
            self.last_transition_is_return = True
            # Reset completo (non solo temp_b_scene, gia' fatto in _get_transition_info):
            # senza azzerare anche temp_b_scene_time, un valore "vecchio" da un ciclo
            # precedente fa scattare il timeout dei 20s quasi subito al PROSSIMO
            # ingresso in wave_kick, bypassando la permanenza minima appena imposta.
            self.temp_b_scene_time = 0

            log_decision(
                from_scene=current_scene,
                to_scene=self.current_couple_a,
                reason=f"{self.current_state.value.upper()} wave_kick -> A ({couple_pct:.0f}%)",
                energy=f"{self.current_state.value.upper()} WAVE",
                duration=self._get_fade_ms() / 1000,
                logger=logger
            )
            return self.current_couple_a

        # ENTRATA in wave_kick: solo se siamo eligible (INTRO/BREAK sempre,
        # BUILD/GROOVE in recupero da break) e attualmente su _A.
        # In INTRO/BREAK valutiamo ad OGNI tick (oltre il debounce), non solo sui
        # kick veri: durante il silenzio i kick (bass>60 richiesto) sono rari o
        # assenti, e questo e' esattamente il motivo per cui "wave_kick non
        # sempre parte". In BUILD/GROOVE (recupero da break) restiamo legati
        # a kick reali, visto che in quella fase l'energia e' gia' attiva.
        if wave_eligible and self.in_scene_a and (intro_or_break or is_kick):
            if self.current_state == State.INTRO:
                prob_wave = max(0.2, 1.0 - couple_elapsed / 30.0)
            elif self.current_state == State.BREAK:
                time_in_break = current_time - self.state_start_time
                prob_wave = max(0.3, 1.0 - time_in_break / 20.0)
            else:
                # BUILD/GROOVE in recupero da break: piu' spazio a wave_kick
                # quanto piu' veloce e' la risalita, decade con la finestra
                elapsed_since_break = current_time - self.break_exit_time
                rise_rate = self._post_break_rise_rate(current_time, bass)
                recovery_progress = min(1.0, elapsed_since_break / POST_BREAK_RECOVERY_WINDOW)
                rise_factor = min(1.0, rise_rate / POST_BREAK_RISE_RATE_THRESHOLD)
                prob_wave = max(0.0, rise_factor * (1.0 - recovery_progress))

            if random.random() < prob_wave:
                self.last_switch_time = current_time

                # SOVRAPPOSIZIONE: possibilita' di un peek invece dello switch diretto
                peek = self._maybe_trigger_overlap("wave_kick", current_time, current_scene, logger, probability=overlap_prob)
                if peek:
                    return peek

                self.in_scene_a = False
                self.last_transition_is_return = False
                target_scene = "wave_kick"
                self.temp_b_scene = target_scene
                if self.temp_b_scene_time == 0:
                    self.temp_b_scene_time = current_time

                log_decision(
                    from_scene=current_scene,
                    to_scene=target_scene,
                    reason=f"{self.current_state.value.upper()} -> wave_kick (prob={prob_wave:.2f}, {couple_pct:.0f}%)",
                    energy=f"{self.current_state.value.upper()} WAVE",
                    duration=self._get_fade_ms() / 1000,
                    logger=logger
                )
                return target_scene

            if intro_or_break:
                # Fase esclusiva: il kick non ha innescato wave_kick, resta su _A
                self.last_switch_time = current_time
                return None
            # BUILD/GROOVE in recupero: se wave_kick non scatta, il kick
            # prosegue al ciclo energetico normale sotto (non e' sprecato)

        # ====================================================================
        # 4. CICLO ENERGETICO PRINCIPALE (BUILD/GROOVE/DROP/PEAK/RELAX)
        # ====================================================================

        # DROP: torna sempre a A (priorità massima)
        if is_drop:
            self.last_switch_time = current_time

            if not self.in_scene_a:
                self.in_scene_a = True
                self.last_transition_is_return = True  # B -> A

                log_decision(
                    from_scene=current_scene,
                    to_scene=self.current_couple_a,
                    reason=f"DROP DROP ({couple_pct:.0f}%) | State: {self.current_state.value}",
                    energy="DROP",
                    duration=self._get_fade_ms() / 1000,
                    logger=logger
                )
                return self.current_couple_a
            return None

        # KICK: A→B→A (ciclo energetico normale)
        if is_kick:
            self.last_switch_time = current_time

            # RAFFICA STROBO: possibilita' di innescare un burst strobo_B invece
            # del normale singolo switch, con probabilita' crescente per stato
            burst_prob = STROBE_BURST_PROBABILITY.get(self.current_state, 0.0)
            if burst_prob > 0 and random.random() < burst_prob:
                self.burst_total_steps = STROBE_BURST_COUNT * 2
                self.burst_step = -1  # verra' incrementato a 0 dentro _advance_burst
                self.burst_back_scene = current_scene
                self.burst_transition_choice = random.choice(STROBE_TRANSITION_CHOICES)
                self.burst_active = True

                # Dove atterrare DOPO il burst: stessa logica del ciclo normale.
                # Rotazione reale del pool _B fatta QUI (commit certo), non
                # speculativamente prima di sapere se saremmo finiti su burst/
                # overlap/switch diretto — altrimenti tentativi assorbiti
                # intermedi possono far ripetere per caso l'ultima _B mostrata.
                if self.in_scene_a:
                    self.burst_return_scene = self._roll_next_b_scene()
                    self.burst_return_is_a = False
                else:
                    self.burst_return_scene = self.current_couple_a
                    self.burst_return_is_a = True

                return self._advance_burst(current_time, current_scene, logger)

            # SOVRAPPOSIZIONE: possibilita' di un peek invece dello switch diretto
            # (probabilita' ZERO durante BUILD/GROOVE/DROP/PEAK — vedi _get_overlap_probability)
            # NOTA: candidato calcolato ma NON committato (self._select_b_scene,
            # non _roll_next_b_scene) finche' non sappiamo che l'overlap scatta
            # davvero — altrimenti un tentativo di overlap fallito "brucerebbe"
            # comunque un roll, con lo stesso bug gia' visto per i tentativi
            # assorbiti (rischio di ripetere per caso l'ultima _B mostrata).
            if self.in_scene_a:
                peek_target = self._select_b_scene(self.current_couple_a, exclude=self.last_shown_b_scene)
            else:
                peek_target = self.current_couple_a
            peek = self._maybe_trigger_overlap(peek_target, current_time, current_scene, logger,
                                                probability=self._get_overlap_probability())
            if peek:
                if self.in_scene_a:
                    # L'overlap scatta davvero: il peek verso peek_target si vede
                    # a schermo (anche se e' solo un'anteprima), quindi committa.
                    self.current_b_scene = peek_target
                    self.last_shown_b_scene = peek_target
                return peek

            if self.in_scene_a:
                # Assorbi il kick con probabilita' (1 - PROB_ENTER_B_ON_KICK):
                # restiamo su _A invece di passare sempre a _B. Il ritorno da
                # _B (branch sotto) resta invece sempre immediato — asimmetria
                # voluta per dare piu' presenza a schermo a _A rispetto a _B.
                # Se assorbito, nessun roll viene committato (si esce prima).
                if random.random() >= PROB_ENTER_B_ON_KICK:
                    return None

                self.in_scene_a = False
                self.last_transition_is_return = False  # A -> B
                # Rotazione reale del pool _B: committata qui, ora che siamo
                # certi che questa _B verra' davvero mostrata.
                self._roll_next_b_scene()

                log_decision(
                    from_scene=current_scene,
                    to_scene=self.current_b_scene,
                    reason=f"KICK B ({couple_pct:.0f}%) | {self.current_state.value}",
                    energy=self.current_state.value.upper(),
                    duration=self._get_fade_ms() / 1000,
                    logger=logger
                )
                return self.current_b_scene

            # IN B: kick → A (sempre ritorna per timing _A > _B)
            else:
                self.in_scene_a = True
                self.last_transition_is_return = True  # B -> A

                log_decision(
                    from_scene=current_scene,
                    to_scene=self.current_couple_a,
                    reason=f"KICK A ({couple_pct:.0f}%) | {self.current_state.value} [timing _A>_B]",
                    energy=self.current_state.value.upper(),
                    duration=self._get_fade_ms() / 1000,
                    logger=logger
                )
                return self.current_couple_a

        return None


model = HybridCouplesModel()

def decide_next_scene(audio_data, current_time, current_scene, logger):
    return model.decide_next_scene(audio_data, current_time, current_scene, logger)

def get_transition_info():
    """Ritorna tipo e durata di transizione corrente"""
    return model._get_transition_info()

def initialize_model(current_scene, current_time):
    model.initialize(current_scene, current_time)
