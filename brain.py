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
import os
from collections import deque
from enum import Enum
from logger import log_decision
from debug_logger import debug as debug_log

try:
    import yaml
except ImportError:
    yaml = None

class State(Enum):
    """7 stati della macchina musicale"""
    INTRO = "intro"        # Inizio basso, transizioni lente
    BUILD = "build"        # Energie crescenti, velocità aumenta
    GROOVE = "groove"      # Stabile, ritmo costante
    BREAK = "break"        # Calo energia, transizioni rare
    DROP = "drop"          # Picco bass, massima velocità
    PEAK = "peak"          # Apice, A+B overlay
    RELAX = "relax"        # Discesa post-peak

# Fallback hardcoded, usato se scenes_config.yaml manca/e' invalido - vedi
# _load_scenes_config() sotto. Storicamente questi erano gli UNICI valori
# possibili (COUPLES_CONFIG.yaml esisteva ma non era mai stato caricato,
# vedi CLAUDE.md); ora sono anche il fallback di sicurezza del file YAML.
_DEFAULT_COUPLES = {
    "urbanfree_A":   ["spectrumbar_B", "tunnelwave_B"],
    "psicodance_A":  ["stormlightning_B", "waveform1_B"],
    "montezuma_A":   ["roundedbar_B", "waveform2_B"],
    "kusanagi_A":    ["radialspike_B", "stormlightning_B"],
    "mri_A":         ["waveform1_B", "ring_B"],
    "futureflash_A": ["roundedbar_B", "waveform2_B"],
    "segnali_A":     ["spectrumbar_B", "ring_B"],
    "strobo_A":      ["stormlightning_B", "ring_B"],
}
_DEFAULT_COUPLE_TRANSITIONS = {
    "urbanfree_A": ["Blur", "Displace"],
    "psicodance_A": ["Displace", "Burn"],
    "montezuma_A": ["Burn", "Blur"],
    "kusanagi_A": ["Burn", "Blur"],
    "mri_A": ["Displace", "Blur"],
    "futureflash_A": ["Burn", "Displace"],
    "segnali_A": ["Burn", "Displace"],
    "strobo_A": ["Displace", "Burn"],
}
_DEFAULT_SPECIAL_SCENES = {"wave_kick": "wave_kick", "strobo": "white_master", "black": "black_master"}
_DEFAULT_STROBE_COLOR_POOL = ["white_master", "red_master", "blue_master", "yellow_master", "green_master"]
# IDENTITA' per scena_A (vedi scenes_config.yaml per la spiegazione): transizione
# "firma" di ingresso coppia, colore identitario del lampo 40/30/30, variante
# wave_kick dedicata. Le 8 scene_A si accoppiano 2 a 2 per pool _B condiviso.
_DEFAULT_IDENTITY = {
    "urbanfree_A":   {"transition": "Spiral",       "color": "red_master",    "wave_kick": "wave_kick1"},
    "segnali_A":     {"transition": "Spiral",       "color": "red_master",    "wave_kick": "wave_kick1"},
    "psicodance_A":  {"transition": "Diaframmatic", "color": "blue_master",   "wave_kick": "wave_kick3"},
    "strobo_A":      {"transition": "Diaframmatic", "color": "blue_master",   "wave_kick": "wave_kick3"},
    "montezuma_A":   {"transition": "Circles",      "color": "yellow_master", "wave_kick": "wave_kick2"},
    "futureflash_A": {"transition": "Circles",      "color": "yellow_master", "wave_kick": "wave_kick2"},
    "kusanagi_A":    {"transition": "Fractal",      "color": "green_master",  "wave_kick": "wave_kick4"},
    "mri_A":         {"transition": "Fractal",      "color": "green_master",  "wave_kick": "wave_kick4"},
}

SCENES_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenes_config.yaml")


def _load_scenes_config(path=SCENES_CONFIG_PATH):
    """Carica coppie/transizioni/scene speciali/pool colori/identita' da
    scenes_config.yaml. Fallback ai valori hardcoded sopra se il file manca,
    e' invalido, o pyyaml non e' installato - nessuna rottura per chi non lo tocca."""
    defaults = (dict(_DEFAULT_COUPLES), dict(_DEFAULT_COUPLE_TRANSITIONS),
                dict(_DEFAULT_SPECIAL_SCENES), list(_DEFAULT_STROBE_COLOR_POOL),
                dict(_DEFAULT_IDENTITY))
    if yaml is None:
        debug_log("[CONFIG] pyyaml non disponibile, uso valori hardcoded")
        return defaults
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        couples = data.get("couples") or _DEFAULT_COUPLES
        transitions = data.get("couple_transitions") or _DEFAULT_COUPLE_TRANSITIONS
        special = data.get("special_scenes") or _DEFAULT_SPECIAL_SCENES
        color_pool = data.get("strobe_color_pool") or _DEFAULT_STROBE_COLOR_POOL
        identity = data.get("identity") or _DEFAULT_IDENTITY
        debug_log(f"[CONFIG] scenes_config.yaml caricato: {len(couples)} coppie")
        return couples, transitions, special, color_pool, identity
    except FileNotFoundError:
        debug_log(f"[CONFIG] {path} non trovato, uso valori hardcoded")
        return defaults
    except Exception as e:
        debug_log(f"[CONFIG] {path} invalido ({e}), uso valori hardcoded")
        return defaults


# COUPLES: ogni _A ha un POOL di possibili _B (non un abbinamento fisso 1:1).
# Caricato da scenes_config.yaml (vedi _load_scenes_config), con fallback
# hardcoded. Filtrato poi da validate_scenes() contro le scene REALMENTE
# presenti in OBS - vedi sotto.
COUPLES, COUPLE_TRANSITIONS, SPECIAL_SCENES, STROBE_COLOR_POOL, IDENTITY = _load_scenes_config()

# True se dopo validate_scenes() resta una sola scena in tutto (nessun vero
# A/B possibile) - decide_next_scene() allora lampeggia sulla stessa scena
# invece di alternare, vedi in fondo al file.
DEGENERATE_MODE = False
DEGENERATE_SCENE = None

# META-COPPIE tra scene_A: la rotazione libera tra tutte e 7 le scene_A ad
# ogni cambio COUPLE_DURATION (4min) risultava troppo veloce ("la rotazione
# tra scene_A deve avvenire piu' lentamente"). Ora ogni META_COUPLE_DURATION
# (~20min) si sceglie una coppia RANDOMICA di 2 scene_A: per quella finestra,
# _select_new_couple() attinge SOLO da quelle 2 (alternandosi ogni
# COUPLE_DURATION come prima), poi si passa a una nuova coppia di scene_A.
META_COUPLE_DURATION = 1200  # ~20 minuti

# Pool di transizioni "di coppia" per il ciclo energetico A<->B (COUPLE_TRANSITIONS,
# caricato sopra da scenes_config.yaml). STESSA pool usata per ENTRAMBE le
# direzioni (A->B e B->A), scelta randomica. Fade escluso di proposito:
# riservato alla fase INTRO/BREAK e al ritorno da wave_kick, per evitare che
# domini percettivamente il ciclo principale (problema gia' riscontrato).

# Transizioni PREVALENTI per stato (non univoche): invece di scegliere 50/50
# tra le 2 transizioni del pool di ogni coppia, si pesa verso quella piu'
# "intensa" (Displace > Burn > Blur) o quella piu' calma a seconda dello
# stato corrente. Mantiene la personalizzazione per coppia (i pool sopra
# restano quelli), aggiunge solo un carattere riconoscibile per stato senza
# diventare meccanico/prevedibile (mai il 100%, sempre un po' di varieta').
TRANSITION_INTENSITY_RANK = {"Displace": 3, "Burn": 2, "Blur": 1}
TRANSITION_INTENSITY_PROBABILITY = {
    State.BUILD:  0.65,  # tensione in crescita -> pende verso il piu' dinamico
    State.GROOVE: 0.55,  # ritmo stabile -> leggero sbilanciamento dinamico
    State.DROP:   0.80,  # massima energia -> quasi sempre il piu' dinamico
    State.PEAK:   0.75,  # apice -> molto dinamico (il resto lo fa gia' il Cut alto)
    State.RELAX:  0.25,  # discesa -> pende verso il piu' calmo
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
# GROOVE alzato da 0.25 a 0.40 su richiesta esplicita ("aumentiamo il numero
# dei cut... anche in groove e intro").
CUT_PROBABILITY_BY_STATE = {
    State.BUILD:  0.10,
    State.GROOVE: 0.40,
    State.DROP:   0.55,
    State.PEAK:   0.75,
    State.RELAX:  0.05,
}

# INTRO non usa CUT_PROBABILITY_BY_STATE (ha una sua transizione dedicata in
# _get_transition_info, wave_kick<->A) - probabilita' di Taglio al posto del
# Fade di default li', stessa richiesta di sopra estesa a INTRO.
CUT_PROBABILITY_INTRO = 0.15

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

# CICLO PRINCIPALE 40/30/30: quando un kick NON viene assorbito (vedi
# PROB_ENTER_B_ON_KICK sopra), invece di andare SEMPRE a scena_B, si sceglie
# tra 3 destinazioni pesate - stessa idea di scena_B ma con piu' varieta' ed
# "esaltando" la scena_A col suo colore/wave_kick identitari (vedi IDENTITY).
# Le raffiche vere (STROBE_BURST) restano riservate a DROP/PEAK come prima -
# non toccate da questo schema, che si applica solo quando il kick arriva
# fin qui SENZA gia' aver innescato una raffica sopra. DROP resta intatto
# (torna sempre e comunque ad A, gestito in un branch separato PRIMA di
# questo). INTRO/BREAK NON passano di qui: mantengono la loro logica
# dedicata pre-esistente (vedi sezione 3, invariata) - il nuovo schema e' in
# prova SOLO nel ciclo energetico principale (BUILD/GROOVE/DROP/PEAK/RELAX).
MAIN_CYCLE_B_PROB = 0.40
MAIN_CYCLE_WAVE_KICK_PROB = 0.30
MAIN_CYCLE_COLOR_PROB = 0.30

# Raffica strobo/flash: alterna rapidamente un colore (vedi STROBE_COLOR_POOL,
# scelto random per OGNI raffica, stesso colore per tutti i suoi frame) <->
# scena di base, N volte, poi atterra sulla scena target normale.
# Implementata come piccola macchina a stati che avanza di UN frame per ogni
# chiamata a decide_next_scene() (NON bloccante: niente time.sleep, il loop
# a 20Hz continua a girare libero tra un frame e l'altro).
STROBE_SCENE = SPECIAL_SCENES.get("strobo", "white_master")  # rinominata da strobo_B
BLACK_PAUSE_SCENE = SPECIAL_SCENES.get("black", "black_master")
# Frazione delle sovrapposizioni in stato calmo che diventano una PAUSA NERA
# (schermo a black_master, hold, ritorno) invece del solito peek verso B/A -
# "manca il nero... troppo illuminata" - una pausa senza immagini ogni tanto,
# mai durante gli stati energici (l'overlap e' gia' disattivato li').
BLACK_PAUSE_PROBABILITY = 0.20
BLACK_PAUSE_HOLD = (1.5, 3.5)  # secondi di nero

# CALM MODE: 4 livelli (0=spento, 1-3 crescenti), attivato via hotkey OBS
# (4 source dedicate, vedi pupa.py) per generi a bassa energia (dub techno,
# minimal, intro lunghe) dove PUPA non puo' riconoscere il genere da solo -
# l'unico modo affidabile e' lasciare che sia il VJ a dirlo. Ogni livello e'
# un moltiplicatore su 5 assi: meno Tagli, Fade/Dissolvenze piu' lente, piu'
# pausa nera (probabilita' e durata), e raffiche strobo DROP/PEAK piu' rare/
# lente/corte - "piu' salgo di livello, piu' le transizioni sono lente e
# morbide" + "aumenterei anche il nero_master" + "anche le raffiche di
# strobo dovrebbero calare (frequenza, velocita' e durata)". "fade" riusato
# anche per rallentare l'intervallo tra i frame della raffica (stesso
# concetto: piu' lento/morbido), "burst_len" scala il NUMERO di frame
# (STROBE_BURST_COUNT), non un tempo - min 1 frame, mai una raffica da zero.
# Numeri di partenza, da tarare dal vivo come tutto il resto in questo file.
CALM_MULTIPLIERS = {
    0: {"cut": 1.0,  "fade": 1.0, "black_prob": 1.0, "black_hold": 1.0, "burst_len": 1.0},
    1: {"cut": 0.6,  "fade": 1.3, "black_prob": 1.5, "black_hold": 1.0, "burst_len": 0.75},
    2: {"cut": 0.35, "fade": 1.6, "black_prob": 2.0, "black_hold": 1.5, "burst_len": 0.5},
    3: {"cut": 0.15, "fade": 2.0, "black_prob": 2.5, "black_hold": 2.0, "burst_len": 0.25},
}
CALM_BLACK_PAUSE_PROB_CAP = 0.9  # non deve mai diventare "quasi sempre nero"

# ALTERNANZA 2 USCITE MONITOR (setup hardware Linux: 2 uscite show separate,
# vedi pupa.py/secrets_local.py - assente su Windows). A bassa energia
# un'uscita accesa e una spenta che si alternano, sempre piu' veloce man
# mano che la musica cresce - "man mano che la musica cresce, fino ad
# essere entrambi accesi nel peak": non un'alternanza sempre piu' rapida
# all'infinito (rischia di leggersi come uno sfarfallio strobo indesiderato),
# ma una vera CONVERGENZA netta a "entrambe accese" in DROP/PEAK.
# (min, max) secondi tra un flip e l'altro per stato - bass live interpola
# dentro il range (piu' energia = piu' vicino al minimo, quindi piu' veloce).
MONITOR_ALTERNATION_INTERVAL_RANGE = {
    State.INTRO:  (2.0, 4.0),
    State.BREAK:  (2.0, 4.0),
    State.RELAX:  (1.5, 3.0),
    State.GROOVE: (0.6, 1.5),
    State.BUILD:  (0.25, 0.8),
}
MONITOR_BOTH_ON_STATES = (State.DROP, State.PEAK)

# NOTA (2026-07-14): una pausa-respiro periodica era stata aggiunta qui per
# ridurre la frequenza delle chiamate wmctrl, quando l'alternanza apriva e
# chiudeva un proiettore nuovo ad ogni flip (fragile sotto carico, causa di
# piu' crash di OBS lo stesso giorno). Con la riscrittura a "stacking" di
# pupa.py (finestre aperte una volta sola, alternanza = portare in primo
# piano quella giusta - vedi _wmctrl_activate) il costo per flip e' sceso a
# ~30ms medi anche sotto carico reale (misurato con test_stacking.py: 2249
# flip/900s, 0 falliti), quindi il problema che la pausa doveva mitigare non
# c'e' piu' - rimossa per tornare all'alternanza continua originale.

# Pesi per stato nella scelta del colore di raffica/lampo: nero e bianco
# SEMPRE dominanti (70% insieme, uguali tra loro), il terzo colore si
# aggiunge come accento (30%) legato all'energia/genere del momento -
# yellow=minimal/ambient (calmo), red=middle, blue=hard/fast (energico).
# Filtrato automaticamente sui colori REALMENTE disponibili (vedi
# validate_scenes) - se l'accento di stato non esiste ancora, resta solo
# nero/bianco pesati.
STROBE_COLOR_WEIGHTS = {
    State.INTRO:  {"white_master": 0.35, "black_master": 0.35, "yellow_master": 0.30},
    State.BREAK:  {"white_master": 0.35, "black_master": 0.35, "yellow_master": 0.30},
    State.RELAX:  {"white_master": 0.35, "black_master": 0.35, "yellow_master": 0.30},
    State.GROOVE: {"white_master": 0.35, "black_master": 0.35, "red_master": 0.30},
    State.BUILD:  {"white_master": 0.35, "black_master": 0.35, "red_master": 0.30},
    State.DROP:   {"white_master": 0.35, "black_master": 0.35, "blue_master": 0.30},
    State.PEAK:   {"white_master": 0.35, "black_master": 0.35, "blue_master": 0.30},
}

STROBE_BURST_COUNT = 4          # numero di flash (ON+OFF) per raffica
STROBE_BURST_INTERVAL = 0.10    # secondi tra un frame e l'altro, fallback se il BPM non e' ancora stimato
STROBE_BEAT_DIVISOR = 4         # 1/4 di battito (sedicesimo) - a 150 BPM coincide col vecchio 0.10 fisso
STROBE_BURST_PROBABILITY = {
    State.PEAK: 0.35,
    State.DROP: 0.15,
}
STROBE_TRANSITION_CHOICES = ["Taglio", "White Fade"]  # provate entrambe, scelta random ad ogni raffica

# LAMPO SINGOLO in INTRO: un solo ON+OFF (non una raffica completa),
# innescato solo sul kick PIU' ALTO letto finora nello stato corrente (nuovo
# "massimo personale"), e comunque solo con probabilita'. Riusa la stessa
# macchina a stati della raffica strobo, con STROBE_FLASH_STEPS al posto di
# STROBE_BURST_COUNT*2. Gestito con una chiamata dedicata dentro il ramo
# wave_kick (il ciclo A/B normale, dove viveva anche il lampo GROOVE/BUILD,
# non e' raggiungibile durante INTRO/BREAK - lasciati intatti, vedi sezione 3).
#
# GROOVE/BUILD tolti da qui (erano gia' presenti prima del ciclo 40/30/30 in
# prova nel ciclo energetico principale, vedi MAIN_CYCLE_*): l'esito "color"
# di quel ciclo copre lo stesso ruolo (lampo colore su kick non assorbito)
# con un colore identitario per scena_A invece che per stato - tenerli
# entrambi avrebbe fatto competere due colori diversi sullo stesso lampo.
STROBE_FLASH_STEPS = 2  # 1 ON + 1 OFF = un singolo lampo
STROBE_FLASH_PROBABILITY = {
    State.INTRO: 0.15,
}

# FADE reattivo all'energia: corto/veloce se la musica spinge (bass live
# alto), lungo se e' calma - invece della durata fissa 2000ms usata finora
# in INTRO e nel ritorno da wave_kick. Stessa logica di modulazione gia'
# usata per il ciclo A/B principale (bass_factor), ma qui INVERSA: piu'
# energia = fade PIU' corto (non piu' lungo).
FADE_DURATION_RANGE = (500, 2800)  # ms: (bass alto -> corto, bass basso -> lungo)

# RAFFICA DI CUT (Taglio): alterna rapidamente scena corrente <-> l'altra
# della coppia (non strobo_B), tutta in Taglio puro - pensata per i momenti
# di "riavvolgimento"/pausa breve della musica (bass che scende bruscamente
# sotto la sua media recente), non per l'energia sostenuta in generale
# (quella e' gia' coperta da STROBE_BURST/lampo singolo). Riusa la stessa
# macchina a stati della raffica strobo (_trigger_strobe/_advance_burst),
# con alt_scene = l'altra scena della coppia invece di STROBE_SCENE.
CUT_BURST_STEPS = 6  # 3 tagli (ON+OFF) per raffica
CUT_BURST_INTERVAL = 0.12  # secondi tra un taglio e l'altro
# Soglia abbassata da -15 a -10 (piu' permissiva, cattura anche pull-back
# piu' leggeri) e probabilita' raddoppiate: nel primo test live nessuna
# raffica e' scattata in 2 minuti nonostante pull-back osservati - non
# sappiamo se per soglia mai raggiunta o dado sfavorevole (non logghiamo
# il trend), quindi si agisce su entrambe per sicurezza.
CUT_BURST_TREND_THRESHOLD = -10.0  # quanto deve scendere bass sotto bass_avg per contare come "pull-back"
CUT_BURST_COOLDOWN = 2.0  # secondi minimi tra una raffica di cut e la successiva
CUT_BURST_PROBABILITY = {
    State.BUILD:  0.50,
    State.GROOVE: 0.40,
    State.DROP:   0.30,
    State.PEAK:   0.30,
    State.RELAX:  0.20,
}

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
# visibile). Era stato alzato 3.0 -> 6.0 per dargli piu' presenza; con la
# nuova calibrazione audio (AGC a inseguitore di picco) wave_kick ricompare
# molto piu' spesso, risultando ora troppo lungo - riportato a 3.0.
MIN_WAVE_KICK_DWELL = 3.0  # secondi

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

        # META-COPPIA tra scene_A (vedi META_COUPLE_DURATION sopra): quali 2
        # scene_A sono "in gioco" per i prossimi ~20 minuti
        self.meta_couple_start_time = 0
        self.current_meta_pair = []
        self.meta_pair_shuffle_bag = []  # "mazzo mescolato", vedi _select_new_meta_pair

        # STATE MACHINE
        self.current_state = State.INTRO
        self.state_start_time = 0
        self.energy_history = deque(maxlen=900)  # ~45s a 20Hz, per le soglie adattive (vedi _adaptive_thresholds)
        self.last_bass = 0  # Ultimo valore bass live, per modulazione continua
        self.last_bass_avg = 0  # Ultima media bass, per calcolare la velocita' del break
        self.last_bpm = 0.0  # Ultimo BPM stimato da audio_analyzer, per l'intervallo strobo agganciato al beat
        self.calm_level = 0  # 0-3, impostato dall'hotkey OBS (vedi CALM_MULTIPLIERS e set_calm_level)
        self.loop_scene = False  # hotkey OBS: congela il timer 4min sulla scena_A corrente (vedi set_loop_scene)

        self.monitor_show1_on = True  # quale uscita e' "accesa" nell'alternanza (vedi get_monitor_outputs)
        self.monitor_last_flip_time = 0
        self.last_energy_trend = 0  # bass - bass_avg dell'ultimo _update_state, per la raffica di cut
        self.recent_kick_peak_bass = 0  # Massimo kick visto nello stato corrente (per il lampo singolo GROOVE/BUILD)
        self.last_cut_burst_time = 0  # Cooldown tra una raffica di cut e la successiva
        self.in_pullback = False  # Per l'edge-trigger della raffica di cut (vedi sopra)

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
        self.burst_alt_scene = STROBE_SCENE
        self.burst_interval = STROBE_BURST_INTERVAL
        self.last_decision_kind = "normal"  # "normal" | "burst_step" | "burst_end" | "overlap_forward" | "overlap_reverse"

        # Sovrapposizione (peek + ritorno, non bloccante)
        self.overlap_active = False
        self.overlap_base_scene = None       # scena di partenza, a cui si torna a fine overlap
        self.overlap_hold_until = 0
        self.overlap_forward_duration_ms = 0
        self.overlap_reverse_duration_ms = 0
        self.overlap_transition_choice = "Fade"

    def initialize(self, current_scene, current_time):
        """Inizializza stato e coppia.

        Scena_A di partenza SEMPRE randomizzata tra tutte quelle valide,
        ignorando quale scena_A OBS sta gia' mostrando (che tra un test e
        l'altro resta quasi sempre la stessa, es. urbanfree_A - "vedo
        sempre urbanfree_A"). pupa.py forza poi lo switch reale in OBS
        subito dopo initialize_model(), cosi' quello che si vede combacia
        da subito con quello che il modello crede.

        Prima meta-coppia (vedi META_COUPLE_DURATION) = TUTTE le scene_A
        invece delle solite 2 - "almeno al primo avvio deve far vedere
        tutte le 8 scene_A", che con la normale finestra di 2 alla volta
        richiederebbe fino a ~80min per una copertura completa. Dalla
        SECONDA meta-coppia in poi (al primo scadere dei 20 min) si torna
        al normale mazzo mescolato da 2."""
        all_a = list(COUPLES.keys())
        self.current_couple_a = random.choice(all_a) if all_a else "urbanfree_A"
        self.in_scene_a = True

        self.current_meta_pair = list(all_a)
        self.meta_pair_shuffle_bag = []  # forza un mescolamento fresco al primo vero cambio meta-coppia
        self.meta_couple_start_time = current_time

        self.current_b_scene = self._roll_next_b_scene()
        self.couple_start_time = current_time
        self.state_start_time = current_time
        self.current_state = State.INTRO
        self.couple_history.append(self.current_couple_a)
        self.last_switch_time = current_time

    def force_couple(self, scene_name, current_time):
        """Risincronizza il modello su una scena_A scelta MANUALMENTE
        dall'esterno (hotkey OBS nativo "Passa a [scena_A]", non gestito da
        PUPA - lo switch e' gia' avvenuto in OBS quando questa funzione viene
        chiamata, qui aggiorniamo solo la CREDENZA interna per non "tornare
        indietro" da soli al prossimo kick).

        "quando forzi una scena_A a mano, il timer dei 4 minuti per quella
        coppia riparte da zero" - couple_start_time fresco, nuova scena_B
        pescata per questa visita. "la finestra colore da 20 minuti invece
        la lascerei intoccata" - meta_pair/meta_couple_start_time NON toccati.
        current_state (energia musicale) lasciato invariato: riflette la
        musica, non la scena, non ha senso resettarlo a INTRO qui.

        Azzera anche eventuali raffiche/sovrapposizioni in corso: dopo uno
        switch manuale non hanno piu' senso (punterebbero a scene ormai
        superate)."""
        self.current_couple_a = scene_name
        self.in_scene_a = True
        self.couple_start_time = current_time
        self.current_b_scene = self._roll_next_b_scene()
        self.last_switch_time = current_time
        self.last_transition_is_return = False
        self.couple_history.append(scene_name)

        self.burst_active = False
        self.overlap_active = False
        self.temp_b_scene = None
        self.temp_b_scene_time = 0
        self.in_pullback = False
        self.recent_kick_peak_bass = 0

    def _get_identity_duos(self):
        """Raggruppa le scene_A per pool_B condiviso - le 4 coppie-colore
        (vedi IDENTITY/scenes_config.yaml: urbanfree_A+segnali_A=rosso,
        psicodance_A+strobo_A=blu, montezuma_A+futureflash_A=giallo,
        kusanagi_A+mri_A=verde). Calcolato dal vivo su COUPLES (gia'
        filtrato da validate_scenes()), non hardcoded - resta corretto
        anche se una scena_A sparisce da OBS (il suo duo diventa da 1 sola
        scena invece di due, gestito senza casi speciali da chi lo consuma)."""
        groups = {}
        for a_scene, b_pool in COUPLES.items():
            key = frozenset(b_pool)
            groups.setdefault(key, []).append(a_scene)
        return list(groups.values())

    def _select_new_meta_pair(self):
        """Sceglie una nuova meta-coppia (vedi META_COUPLE_DURATION): per i
        prossimi ~20 minuti _select_new_couple() attinge solo da queste
        scene_A, invece che liberamente da tutte.

        L'unita' del mazzo e' un DUO-COLORE intero (_get_identity_duos), non
        una singola scena_A: cosi' ogni finestra di 20 min resta su
        un'unica identita' colore, mai una combinazione mista tra due duo
        diversi ("le coppie dei 20min combaciano con lo schema dei
        colori?" - verificato che PRIMA no, solo 16% per puro caso).

        "Mazzo mescolato" (self.meta_pair_shuffle_bag): tutti i duo vengono
        mescolati una volta e consumati uno alla volta finche' il mazzo non
        si svuota, poi si rimescola - GARANTISCE che ognuno compaia
        esattamente una volta ogni giro completo (4 duo, ~80min), invece di
        affidarsi al caso con una finestra anti-repeat limitata (le
        precedenti 3 coppie) che non garantiva copertura.

        Se dopo validate_scenes() sopravvive 1 sola coppia (non 0 - quel
        caso e' DEGENERATE_MODE, gestito altrove), il mazzo non serve:
        resta l'unica disponibile."""
        all_a = list(COUPLES.keys())
        if len(all_a) <= 2:
            self.current_meta_pair = list(all_a)
            return self.current_meta_pair

        if not self.meta_pair_shuffle_bag:
            self.meta_pair_shuffle_bag = self._get_identity_duos()
            random.shuffle(self.meta_pair_shuffle_bag)

        duo = self.meta_pair_shuffle_bag.pop()
        self.current_meta_pair = list(duo)
        return self.current_meta_pair

    def _select_new_couple(self):
        """Seleziona nuova coppia_A dalla meta-coppia corrente (esclude ultime 5)"""
        pool = self.current_meta_pair if self.current_meta_pair else list(COUPLES.keys())
        available = [a for a in pool if a not in self.couple_history]
        if not available:
            available = pool
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

    def _get_identity(self):
        """Ritorna il dict identita' (transition/color/wave_kick) della
        scena_A corrente, gia' filtrato da validate_scenes() sui campi
        REALMENTE disponibili in OBS. Dict vuoto se la scena_A non e' in
        IDENTITY - i chiamanti gestiscono il fallback campo per campo."""
        return IDENTITY.get(self.current_couple_a, {})

    def _pick_strobe_color(self):
        """Sceglie il colore per la prossima raffica/lampo, pesato per stato
        (vedi STROBE_COLOR_WEIGHTS): nero/bianco sempre dominanti, il terzo
        colore legato a energia/genere si aggiunge come accento. Filtra sui
        colori REALMENTE disponibili (STROBE_COLOR_POOL, gia' validato)."""
        weights = STROBE_COLOR_WEIGHTS.get(self.current_state, {})
        available_weighted = {c: w for c, w in weights.items() if c in STROBE_COLOR_POOL}
        if not available_weighted:
            return random.choice(STROBE_COLOR_POOL)
        colors = list(available_weighted.keys())
        weight_values = list(available_weighted.values())
        return random.choices(colors, weights=weight_values, k=1)[0]

    def _calm(self, key):
        """Moltiplicatore CALM MODE per l'asse richiesto ("cut"/"fade"/
        "black_prob"/"black_hold"), in base a self.calm_level (0-3). Vedi
        CALM_MULTIPLIERS - livello 0 ritorna sempre 1.0 (nessun effetto)."""
        return CALM_MULTIPLIERS.get(self.calm_level, CALM_MULTIPLIERS[0])[key]

    def get_monitor_outputs(self, current_time):
        """Decide quale/i delle 2 uscite show mostrare 'accesa' in questo
        istante - chiamata ad ogni tick da pupa.py (solo Linux, vedi
        secrets_local.py). A bassa energia alterna una sola uscita per
        volta (intervallo che si accorcia con l'energia, vedi
        MONITOR_ALTERNATION_INTERVAL_RANGE), in DROP/PEAK converge su
        ENTRAMBE accese fisse (non un'alternanza sempre piu' rapida).

        Ritorna {"show1": bool, "show2": bool}."""
        if self.current_state in MONITOR_BOTH_ON_STATES:
            return {"show1": True, "show2": True}

        lo, hi = MONITOR_ALTERNATION_INTERVAL_RANGE.get(self.current_state, (2.0, 4.0))
        bass_factor = min(1.0, max(0.0, self.last_bass / 100.0))
        interval = hi - bass_factor * (hi - lo)  # piu' energia -> piu' vicino al minimo (piu' veloce)

        if current_time - self.monitor_last_flip_time >= interval:
            self.monitor_last_flip_time = current_time
            self.monitor_show1_on = not self.monitor_show1_on

        return {"show1": self.monitor_show1_on, "show2": not self.monitor_show1_on}

    def _get_strobe_interval(self):
        """Intervallo tra un frame e l'altro di flash/raffica, agganciato al
        beat reale (un sedicesimo, STROBE_BEAT_DIVISOR) quando il BPM e' gia'
        stimato (audio_analyzer lo azzera durante i primi kick o se implausibile).
        A 150 BPM coincide col vecchio valore fisso STROBE_BURST_INTERVAL, che
        resta come fallback finche' il BPM non converge."""
        if self.last_bpm <= 0:
            return STROBE_BURST_INTERVAL
        return 60.0 / self.last_bpm / STROBE_BEAT_DIVISOR

    def _trigger_strobe(self, current_scene, total_steps, return_scene=None, return_is_a=None,
                         alt_scene=None, transition_choice=None, interval=None):
        """Predispone una raffica strobo/lampo/cut (self.burst_active=True), pronta
        per essere avanzata da _advance_burst(). total_steps=2 (1 ON+1 OFF) per
        un lampo singolo, STROBE_BURST_COUNT*2 per una raffica completa, CUT_BURST_STEPS
        per una raffica di Tagli - stessa macchina a stati in tutti i casi, cambia
        solo lunghezza/scena alternata/transizione.

        return_scene/return_is_a: override esplicito di dove atterrare dopo il
        burst/lampo (usato dal lampo in INTRO, che deve restare sulla stessa
        scena_A invece di saltare su una _B - li' il kick e' "assorbito", non
        genera un vero switch). Se assenti, usa la logica di default del
        ciclo A/B normale.

        alt_scene: scena alternata al posto del pool colori (usato dalla
        raffica di cut, che alterna verso l'altra scena della coppia invece
        che verso un colore). Se assente, sceglie a caso da STROBE_COLOR_POOL
        (stesso colore per tutti i frame di QUESTA raffica, non cambia a meta').
        transition_choice: forza la transizione invece di sceglierla random da
        STROBE_TRANSITION_CHOICES (la raffica di cut vuole SEMPRE Taglio puro).
        interval: secondi tra un frame e l'altro, default STROBE_BURST_INTERVAL."""
        self.burst_total_steps = total_steps
        self.burst_step = -1  # verra' incrementato a 0 dentro _advance_burst
        self.burst_back_scene = current_scene
        self.burst_transition_choice = transition_choice or random.choice(STROBE_TRANSITION_CHOICES)
        self.burst_alt_scene = alt_scene if alt_scene is not None else self._pick_strobe_color()
        self.burst_interval = interval or STROBE_BURST_INTERVAL
        self.burst_active = True

        if return_scene is not None:
            self.burst_return_scene = return_scene
            self.burst_return_is_a = self.in_scene_a if return_is_a is None else return_is_a
            return

        # Dove atterrare DOPO il burst/lampo: stessa logica del ciclo normale.
        # La scena_B e' quella GIA' fissata per questa coppia (vedi
        # _roll_next_b_scene, chiamato solo a inizio coppia) - non se ne
        # sceglie una nuova qui, per non ruotare la _B a meta' coppia.
        if self.in_scene_a:
            self.burst_return_scene = self.current_b_scene
            self.burst_return_is_a = False
        else:
            self.burst_return_scene = self.current_couple_a
            self.burst_return_is_a = True

    def _advance_burst(self, current_time, current_scene, logger):
        """Avanza di UN frame la raffica strobo/cut attiva (assume self.burst_active == True).

        Alterna burst_alt_scene <-> scena di base per burst_total_steps frame, poi atterra
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

        self.burst_next_time = current_time + self.burst_interval
        self.last_decision_kind = "burst_step"
        target = self.burst_alt_scene if (self.burst_step % 2 == 0) else self.burst_back_scene

        log_decision(
            from_scene=current_scene,
            to_scene=target,
            reason=f"STROBE BURST frame {self.burst_step + 1}/{self.burst_total_steps}",
            energy="STROBE",
            duration=self.burst_interval,
            logger=logger
        )
        return target

    def _maybe_trigger_overlap(self, peek_target_scene, current_time, current_scene, logger, probability=None):
        """Prova ad innescare una SOVRAPPOSIZIONE invece di uno switch normale.

        Se innescata: peek verso peek_target_scene (blend 40-60%), mantenuto per un
        tempo prolungato (2-4s se il peek arriva verso _A, 0.5-2s se verso _B), poi
        ritorno alla scena di partenza (nessun cambio scena netto). Non tocca
        self.in_scene_a: la sovrapposizione e' solo visiva, non un vero switch.

        Una frazione di queste sovrapposizioni (BLACK_PAUSE_PROBABILITY)
        diventa invece una PAUSA NERA vera (100%, non un blend parziale)
        verso BLACK_PAUSE_SCENE - stessa meccanica di hold/ritorno.

        Ritorna peek_target_scene se innescata, altrimenti None (il chiamante
        procede con lo switch normale).
        """
        prob = probability if probability is not None else OVERLAP_PROBABILITY
        if random.random() >= prob:
            return None

        peek_is_return = not self.in_scene_a  # il peek va verso _A se partiamo da _B

        # PAUSA NERA: frazione delle sovrapposizioni in stato calmo diventa
        # un vero "respiro" senza immagini (black_master) invece del solito
        # peek parziale verso B/A - "manca il nero... troppo illuminata".
        # A differenza del peek normale (blend 40-60%), qui si va al 100%
        # (non ha senso una "mezza pausa nera" semi-trasparente).
        black_pause_prob = min(CALM_BLACK_PAUSE_PROB_CAP, BLACK_PAUSE_PROBABILITY * self._calm("black_prob"))
        is_black_pause = random.random() < black_pause_prob

        if is_black_pause:
            peek_target_scene = BLACK_PAUSE_SCENE
            black_hold_range = tuple(h * self._calm("black_hold") for h in BLACK_PAUSE_HOLD)
            hold_time = random.uniform(*black_hold_range)
            forward_ms = random.randint(400, 800)
        else:
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
            reason=f"{'PAUSA NERA' if is_black_pause else 'SOVRAPPOSIZIONE peek'} (hold {hold_time:.1f}s, {self.overlap_transition_choice})",
            energy="BLACK PAUSE" if is_black_pause else "OVERLAP",
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

    def _adaptive_thresholds(self):
        """Soglie di stato come PERCENTILI della dinamica recente (~45s),
        invece di valori assoluti fissi. Le soglie fisse erano tarate su un
        liveset techno/tech house denso - con un genere diverso e piu' lento
        (es. dub techno) l'energia non arriva mai a superarle, restando
        bloccati tra INTRO/RELAX/BREAK indipendentemente da come si muove
        davvero il brano. Con i percentili, la macchina a stati si
        ri-calibra da sola sul range REALE di qualunque cosa stia suonando,
        denso o sparso, veloce o lento.

        Fallback a soglie fisse ragionevoli finche' non c'e' abbastanza
        storia accumulata (es. appena avviato)."""
        if len(self.energy_history) < 60:
            return {"peak": 85, "build": 70, "groove": 50, "break": 20}

        sorted_hist = sorted(self.energy_history)
        n = len(sorted_hist)

        def pct(p):
            idx = min(n - 1, max(0, int(n * p)))
            return sorted_hist[idx]

        peak = pct(0.90)
        build = pct(0.70)
        groove = pct(0.45)
        break_th = pct(0.12)

        # Distacco minimo tra soglie: con poca varianza (es. un drone quasi
        # costante) i percentili potrebbero collassare vicini, facendo
        # oscillare lo stato per fluttuazioni minime
        build = min(build, peak - 3)
        groove = min(groove, build - 3)
        break_th = min(break_th, groove - 3)

        return {"peak": peak, "build": build, "groove": groove, "break": break_th}

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

        Soglie ADATTIVE (vedi _adaptive_thresholds): calcolate sui percentili
        della dinamica recente, non piu' fisse - si adattano al genere in
        riproduzione invece di essere tarate una volta per tutte.
        """
        energy = bass_avg
        energy_trend = bass - bass_avg if bass_avg > 0 else 0
        self.last_energy_trend = energy_trend
        self.energy_history.append(energy)

        th = self._adaptive_thresholds()

        # Logica: associa energia a stato secondo le soglie adattive correnti
        if couple_elapsed < 30:
            new_state = State.INTRO
        elif energy > th["build"] and energy_trend > 10:
            new_state = State.DROP
        elif energy > th["peak"]:
            new_state = State.PEAK
        elif energy > th["build"]:
            new_state = State.BUILD
        elif energy > th["groove"]:
            new_state = State.GROOVE
        elif energy < th["break"]:
            new_state = State.BREAK
        else:
            new_state = State.RELAX

        # Transizione stato
        if new_state != self.current_state:
            self.current_state = new_state
            self.state_start_time = current_time
            self.recent_kick_peak_bass = 0  # nuovo stato, si riparte a cercare il "kick piu' alto"

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

    def _weighted_couple_transition(self, couple_pool):
        """Sceglie tra le 2 transizioni del pool della coppia corrente,
        pesando verso quella piu' "intensa" (Displace > Burn > Blur) o
        quella piu' calma a seconda dello stato corrente - non piu' 50/50
        uniforme. Mai il 100%: resta sempre un po' di varieta', solo con
        un carattere prevalente riconoscibile per stato."""
        if len(couple_pool) < 2:
            return couple_pool[0] if couple_pool else "Burn"
        ranked = sorted(couple_pool, key=lambda t: TRANSITION_INTENSITY_RANK.get(t, 0), reverse=True)
        high, low = ranked[0], ranked[-1]
        prob_high = TRANSITION_INTENSITY_PROBABILITY.get(self.current_state, 0.5)
        return high if random.random() < prob_high else low

    def _get_fade_duration_ms(self):
        """Durata del Fade reattiva all'energia live: corto/veloce se il bass
        e' alto (la musica spinge), lungo se e' calmo - non la durata fissa
        2000ms usata finora in INTRO e nel ritorno da wave_kick ("il fade
        deve reagire in base alla musica: se spinge fade corti veloci, se
        calma fade lunghi")."""
        bass_factor = min(1.0, max(0.0, self.last_bass / 100.0))
        min_ms, max_ms = FADE_DURATION_RANGE
        base_ms = max_ms - bass_factor * (max_ms - min_ms)
        return int(base_ms * self._calm("fade"))

    def _get_fade_ms(self):
        """Ritorna la durata di transizione usata per il display nei log"""
        if self.current_state in (State.INTRO, State.BREAK):
            return self._get_fade_duration_ms()
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

        # MODALITA' DEGENERATA: lampeggio sulla stessa scena (vedi
        # validate_scenes/decide_next_scene) - pupa.py intercetta
        # kick_mode="flash_single" e chiama obs.flash_scene() invece di
        # un vero switch_scene (che sarebbe un no-op, stessa scena).
        if self.last_decision_kind == "flash_single":
            debug_log("[TRANS] MODALITA' DEGENERATA: lampeggio")
            return {"type": "Taglio", "duration_ms": 100, "is_return": False, "kick_mode": "flash_single"}

        # CAMBIO COPPIA: transizione "firma" della nuova scena_A (vedi
        # IDENTITY), un'unica volta all'ingresso. Controllato PRIMA del
        # branch generico INTRO sotto, perche' current_state e' gia' INTRO
        # a questo punto (impostato da decide_next_scene insieme al kind).
        # Fallback al normale Fade se la scena_A non ha una firma valida
        # (non in IDENTITY, o firma non disponibile in OBS - gia' filtrato
        # da validate_scenes).
        if self.last_decision_kind == "couple_start":
            signature = self._get_identity().get("transition")
            fade_ms = self._get_fade_duration_ms()
            if signature:
                debug_log(f"[TRANS] CAMBIO COPPIA: firma '{signature}' {fade_ms}ms")
                return {"type": signature, "duration_ms": fade_ms, "is_return": False, "kick_mode": "couple_start"}
            debug_log(f"[TRANS] CAMBIO COPPIA: nessuna firma per {self.current_couple_a}, Fade {fade_ms}ms")
            return {"type": "Fade", "duration_ms": fade_ms, "is_return": False, "kick_mode": "couple_start"}

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

        # Frame intermedio della raffica strobo/cut: transizione scelta al
        # trigger, durata legata all'intervallo del burst (niente pool/energia qui)
        if self.last_decision_kind == "burst_step":
            burst_duration_ms = int(self.burst_interval * 1000 * 0.7)
            is_cut_burst = self.burst_alt_scene not in STROBE_COLOR_POOL
            kick_mode = "cutburst" if is_cut_burst else "strobe"
            debug_log(f"[TRANS] {'CUT BURST' if is_cut_burst else 'STROBE'} frame: {self.burst_transition_choice} {burst_duration_ms}ms")
            return {
                "type": self.burst_transition_choice,
                "duration_ms": burst_duration_ms,
                "is_return": False,
                "kick_mode": kick_mode
            }

        # Ritorno da wave_kick a _A: Fade (controllato PRIMA del check generico
        # sotto, perche' temp_b_scene resta "wave_kick" fino a qui: se il check
        # generico venisse prima, intercetterebbe anche il ritorno, non solo l'entrata)
        if is_return and self.temp_b_scene == "wave_kick":
            self.temp_b_scene = None  # Reset temp override
            fade_ms = self._get_fade_duration_ms()
            debug_log(f"[TRANS] wave_kick -> A: Fade {fade_ms}ms")
            return {
                "type": "Fade",
                "duration_ms": fade_ms,
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
            cut_prob = min(0.8, drop_rate / 30.0) * self._calm("cut")
            if random.random() < cut_prob:
                trans_type, duration_ms = "Taglio", 300
            else:
                trans_type, duration_ms = "Fade", self._get_fade_duration_ms()
            debug_log(f"[TRANS] BREAK reattivo: {trans_type} {duration_ms}ms (drop_rate={drop_rate:.1f}, cut_prob={cut_prob:.2f})")
            return {"type": trans_type, "duration_ms": duration_ms, "is_return": is_return}

        # INTRO: ciclo wave_kick<->_A, per lo piu' Fade (Stinger gia' gestito
        # sopra), con una probabilita' di Taglio al posto del Fade ("aumentiamo
        # il numero dei cut... anche in groove e intro")
        if self.current_state == State.INTRO:
            if random.random() < CUT_PROBABILITY_INTRO * self._calm("cut"):
                debug_log(f"[TRANS] INTRO: Taglio 200ms (cut)")
                return {"type": "Taglio", "duration_ms": 200, "is_return": is_return}
            fade_ms = self._get_fade_duration_ms()
            debug_log(f"[TRANS] INTRO: Fade {fade_ms}ms")
            return {"type": "Fade", "duration_ms": fade_ms, "is_return": is_return}

        # Ciclo energetico principale: stessa pool random per A->B e B->A,
        # con Cut integrato a probabilita' crescente con l'energia (stato + bass live)
        couple_pool = COUPLE_TRANSITIONS.get(self.current_couple_a, ["Burn", "Displace"])
        base_duration = CYCLE_TRANSITION_DURATION_MS.get(self.current_state, 800)
        base_cut_prob = CUT_PROBABILITY_BY_STATE.get(self.current_state, 0.2)

        # Modulazione continua sul bass live: piu' energia = piu' veloce e piu' cut,
        # ma mai sotto una soglia minima visibile (150ms)
        bass_factor = min(1.0, max(0.0, self.last_bass / 100.0))
        duration = max(150, int(base_duration * (1.0 - 0.3 * bass_factor) * self._calm("fade")))
        cut_prob = min(0.9, base_cut_prob + 0.2 * bass_factor) * self._calm("cut")

        if random.random() < cut_prob:
            trans_type = "Taglio"
        else:
            trans_type = self._weighted_couple_transition(couple_pool)

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
        bpm = audio_data.get("bpm", 0.0)

        self.last_bass = bass
        self.last_bass_avg = bass_avg
        self.last_bpm = bpm
        couple_elapsed = current_time - self.couple_start_time

        # Default: nessun "kind" speciale finche' non impostato da un branch specifico
        self.last_decision_kind = "normal"

        # MODALITA' DEGENERATA (vedi validate_scenes): con una sola scena
        # disponibile in OBS (o nessuna coppia configurata sopravvissuta alla
        # validazione) non esiste un vero A/B da alternare - lampeggia sulla
        # stessa scena ad ogni kick invece di girare tutta la logica normale,
        # che non avrebbe senso senza scene_B/coppie reali.
        if DEGENERATE_MODE:
            if is_kick and (current_time - self.last_switch_time) >= 0.15:
                self.last_switch_time = current_time
                self.last_decision_kind = "flash_single"
                return current_scene
            return None

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

        # TIMEOUT temp_b_scene (wave_kick max 20 secondi) - rete di sicurezza:
        # forza il ritorno VERO ad A (non solo il reset del flag interno, che
        # lasciava la scena bloccata su wave_kick a schermo mentre il codice
        # pensava gia' di essere tornato ad A - causa del bug "wave_kick resta
        # troppo durante un break lungo": nessun kick reale in un break
        # silenzioso, il ritorno normale (sotto) non scattava mai).
        if self.temp_b_scene == "wave_kick" and self.temp_b_scene_time > 0 and (current_time - self.temp_b_scene_time) > 20:
            debug_log(f"[TIMEOUT] wave_kick scaduto, ritorno forzato ad A")
            self.temp_b_scene = None
            self.temp_b_scene_time = 0
            self.in_scene_a = True
            self.last_transition_is_return = True
            self.last_switch_time = current_time
            log_decision(
                from_scene=current_scene,
                to_scene=self.current_couple_a,
                reason="TIMEOUT wave_kick scaduto (20s)",
                energy="TIMEOUT",
                duration=self._get_fade_ms() / 1000,
                logger=logger
            )
            return self.current_couple_a

        # AGGIORNA STATO MUSICALE (energy_history alimentato dentro _update_state)
        prev_state = self.current_state
        self._update_state(bass, bass_avg, couple_elapsed, current_time)

        # Rileva uscita da BREAK: serve per estendere lo spazio di wave_kick
        # in BUILD/GROOVE durante una risalita rapida (vedi _wave_kick_eligible)
        if prev_state == State.BREAK and self.current_state != State.BREAK:
            self.break_exit_time = current_time
            self.bass_at_break_exit = bass

        # ====================================================================
        # 1. TIMER COPPIA SCADUTO? (4 minuti) - saltato se loop_scene attivo
        # (hotkey OBS: "questa scena_A sta funzionando, non portarmela via" -
        # il ciclo audio-reattivo interno kick->B/wave_kick/colore prosegue
        # normalmente, resta congelato solo IL CAMBIO di scena_A).
        # ====================================================================
        if couple_elapsed > self.COUPLE_DURATION and not self.loop_scene:
            if current_time - self.meta_couple_start_time > META_COUPLE_DURATION:
                self._select_new_meta_pair()
                self.meta_couple_start_time = current_time
            self.current_couple_a = self._select_new_couple()
            self.current_b_scene = self._roll_next_b_scene()
            self.couple_start_time = current_time
            self.state_start_time = current_time
            self.current_state = State.INTRO
            self.in_scene_a = True
            self.last_switch_time = current_time
            self.last_transition_is_return = False  # Nuova coppia, ingresso "forward" su _A
            # Transizione "firma" (Spiral/Diaframmatic/Circles/Fractal): un
            # kind dedicato, perche' self.current_state e' gia' INTRO qui sopra
            # - senza un kind distinto, _get_transition_info() userebbe il
            # normale Fade/Taglio di INTRO invece della firma della nuova coppia.
            self.last_decision_kind = "couple_start"

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
        #
        # In INTRO/BREAK valutato ad OGNI tick (non solo sui kick veri, stesso
        # motivo dell'ENTRATA sotto): wave_kick e' concettualmente una scena_B
        # (le scene_A restano sempre dominanti), quindi deve alternarsi con
        # _A esattamente come farebbe una _B vera - un break lungo e silenzioso
        # non ha kick reali per ore, e senza questo wave_kick restava bloccato
        # a schermo ben oltre la permanenza minima, dominando invece di _A.
        if self.temp_b_scene == "wave_kick" and not self.in_scene_a and (intro_or_break or is_kick):
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

                # LAMPO SINGOLO in INTRO (non BREAK, non richiesto li'): il
                # ciclo A/B normale (dove vive la logica dei lampi) non e'
                # raggiungibile qui, quindi va agganciato direttamente in
                # questo ramo. Resta sulla stessa scena_A (return_scene
                # esplicito) invece di saltare su una _B - il kick e'
                # "assorbito", non e' un vero switch.
                if self.current_state == State.INTRO and bass > self.recent_kick_peak_bass:
                    self.recent_kick_peak_bass = bass
                    flash_prob = STROBE_FLASH_PROBABILITY.get(State.INTRO, 0.0)
                    if flash_prob > 0 and random.random() < flash_prob:
                        self._trigger_strobe(current_scene, STROBE_FLASH_STEPS,
                                              return_scene=current_scene, return_is_a=True,
                                              interval=self._get_strobe_interval())
                        return self._advance_burst(current_time, current_scene, logger)

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

        # RAFFICA DI CUT: alterna rapidamente verso l'altra scena della coppia,
        # tutta in Taglio puro - innescata su un vero "pull-back" (bass che
        # scende bruscamente sotto la sua media recente, es. un
        # riavvolgimento/piccola pausa). INDIPENDENTE da is_kick di proposito:
        # un kick per definizione richiede il bass in SALITA (bass_delta > soglia),
        # quasi incompatibile con un trend negativo sullo stesso istante -
        # dentro "if is_kick" non scattava MAI (verificato dal vivo, zero
        # pull-back rilevati in test reali). EDGE-TRIGGERED (self.in_pullback):
        # valutata una sola volta all'INIZIO del calo, non ad ogni tick per
        # tutta la sua durata (altrimenti a 20Hz un calo di anche solo 200ms
        # ripeterebbe il dado 4 volte, alterando la probabilita' effettiva).
        cut_burst_prob = CUT_BURST_PROBABILITY.get(self.current_state, 0.0) * self._calm("cut")
        is_pullback_now = self.last_energy_trend < CUT_BURST_TREND_THRESHOLD
        if is_pullback_now and not self.in_pullback:
            cooldown_ok = (current_time - self.last_cut_burst_time) >= CUT_BURST_COOLDOWN
            debug_log(f"[CUTBURST] pull-back rilevato: trend={self.last_energy_trend:.1f} "
                      f"state={self.current_state.value} cooldown_ok={cooldown_ok} prob={cut_burst_prob:.2f}")
            if cut_burst_prob > 0 and cooldown_ok and random.random() < cut_burst_prob:
                self.last_cut_burst_time = current_time
                self.last_switch_time = current_time
                alt_scene = self.current_b_scene if self.in_scene_a else self.current_couple_a
                self._trigger_strobe(current_scene, CUT_BURST_STEPS, alt_scene=alt_scene,
                                      transition_choice="Taglio", interval=CUT_BURST_INTERVAL)
                self.in_pullback = is_pullback_now
                return self._advance_burst(current_time, current_scene, logger)
        self.in_pullback = is_pullback_now

        # KICK: A→B→A (ciclo energetico normale)
        if is_kick:
            self.last_switch_time = current_time

            # RAFFICA STROBO: possibilita' di innescare un burst strobo_B invece
            # del normale singolo switch, con probabilita' crescente per stato.
            # Colore identitario della scena_A corrente (non piu' quello legato
            # allo stato DROP/PEAK) - la raffica resta l'evento piu' vistoso
            # (8 frame vs i 2 del lampo), quindi era lei a "vincere" sempre con
            # l'azzurro/blu anche quando il lampo colorava gia' di rosso/verde/
            # giallo altrove: osservato dal vivo (9 lampi rossi contro 4
            # raffiche blu nella stessa sessione, ma il blu restava l'unico
            # colore percepito). Fallback al colore di stato se la scena_A non
            # ha un colore identitario valido (vedi _trigger_strobe/alt_scene).
            burst_prob = STROBE_BURST_PROBABILITY.get(self.current_state, 0.0) * self._calm("cut")
            if burst_prob > 0 and random.random() < burst_prob:
                calm_burst_count = max(1, round(STROBE_BURST_COUNT * self._calm("burst_len")))
                self._trigger_strobe(current_scene, calm_burst_count * 2,
                                      alt_scene=self._get_identity().get("color"),
                                      interval=self._get_strobe_interval() * self._calm("fade"))
                return self._advance_burst(current_time, current_scene, logger)

            # SOVRAPPOSIZIONE: possibilita' di un peek invece dello switch diretto
            # (probabilita' ZERO durante BUILD/GROOVE/DROP/PEAK — vedi _get_overlap_probability)
            # peek_target verso B e' SEMPRE self.current_b_scene (fissata a
            # inizio coppia, vedi _roll_next_b_scene) - nessuna nuova selezione
            # qui, per non ruotare la _B a meta' coppia ("per ogni scena_A una
            # sola scena_B per rotazione").
            if self.in_scene_a:
                peek_target = self.current_b_scene
            else:
                peek_target = self.current_couple_a
            peek = self._maybe_trigger_overlap(peek_target, current_time, current_scene, logger,
                                                probability=self._get_overlap_probability())
            if peek:
                return peek

            if self.in_scene_a:
                # Assorbi il kick con probabilita' (1 - PROB_ENTER_B_ON_KICK):
                # restiamo su _A invece di passare sempre a _B. Il ritorno da
                # _B (branch sotto) resta invece sempre immediato — asimmetria
                # voluta per dare piu' presenza a schermo a _A rispetto a _B.
                if random.random() >= PROB_ENTER_B_ON_KICK:
                    return None

                # CICLO PRINCIPALE 40/30/30 (IN PROVA): non assorbito -> 3
                # destinazioni pesate invece di andare sempre a scena_B (vedi
                # MAIN_CYCLE_* sopra). wave_kick riusa la stessa macchina di
                # dwell/timeout/ritorno gia' esistente (temp_b_scene), quindi
                # si comporta esattamente come l'ingresso INTRO/BREAK una
                # volta atterrato li'. Il colore torna sulla stessa scena_A
                # (non su _B) - e' un accento, non un vero switch.
                outcome = random.choices(
                    ["b", "wave_kick", "color"],
                    weights=[MAIN_CYCLE_B_PROB, MAIN_CYCLE_WAVE_KICK_PROB, MAIN_CYCLE_COLOR_PROB],
                    k=1
                )[0]

                if outcome == "wave_kick":
                    self.in_scene_a = False
                    self.last_transition_is_return = False  # A -> wave_kick
                    self.temp_b_scene = "wave_kick"
                    if self.temp_b_scene_time == 0:
                        self.temp_b_scene_time = current_time

                    log_decision(
                        from_scene=current_scene,
                        to_scene="wave_kick",
                        reason=f"KICK wave_kick ({couple_pct:.0f}%) | {self.current_state.value} [ciclo 40/30/30]",
                        energy=self.current_state.value.upper(),
                        duration=self._get_fade_ms() / 1000,
                        logger=logger
                    )
                    return "wave_kick"

                if outcome == "color":
                    identity_color = self._get_identity().get("color")
                    self._trigger_strobe(current_scene, STROBE_FLASH_STEPS,
                                          return_scene=current_scene, return_is_a=True,
                                          alt_scene=identity_color, interval=self._get_strobe_interval())
                    return self._advance_burst(current_time, current_scene, logger)

                self.in_scene_a = False
                self.last_transition_is_return = False  # A -> B
                # NON si rirolla la _B qui: resta quella fissata a inizio
                # coppia (self.current_b_scene), stessa per tutta la durata
                # della coppia corrente.

                log_decision(
                    from_scene=current_scene,
                    to_scene=self.current_b_scene,
                    reason=f"KICK B ({couple_pct:.0f}%) | {self.current_state.value} [ciclo 40/30/30]",
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

def force_couple(scene_name, current_time):
    """Risincronizza il modello su una scena_A scelta manualmente da un
    hotkey OBS nativo - vedi HybridCouplesModel.force_couple. Chiamato da
    pupa.py quando rileva che la scena_A REALMENTE mostrata in OBS non
    combacia piu' con quella che il modello crede attiva."""
    model.force_couple(scene_name, current_time)

def get_identity_wave_kick_variant():
    """Variante wave_kick dedicata alla scena_A corrente (vedi IDENTITY in
    scenes_config.yaml), o None se non definita/non disponibile in OBS -
    pupa.py ricade sulla selezione random generica in quel caso."""
    return model._get_identity().get("wave_kick")

def get_current_couple_a():
    """Scena_A su cui il modello crede di trovarsi ORA (randomizzata da
    initialize_model - vedi HybridCouplesModel.initialize). pupa.py la usa
    subito dopo l'init per forzare un vero switch OBS, cosi' lo schermo
    combacia da subito con lo stato interno invece di aspettare il primo
    switch organico."""
    return model.current_couple_a

def set_calm_level(level):
    """Imposta il livello di CALM MODE (0-3, vedi CALM_MULTIPLIERS) -
    chiamato da pupa.py quando rileva un cambio nell'hotkey OBS dedicato.
    Clampato a [0,3] per sicurezza (un valore fuori range non deve rompere
    _calm())."""
    model.calm_level = max(0, min(3, level))

def get_calm_level():
    """Livello di CALM MODE corrente (per il print console di pupa.py)."""
    return model.calm_level

def set_loop_scene(enabled, current_time):
    """Attiva/disattiva il loop sulla scena_A corrente (hotkey OBS: congela
    il timer dei 4 minuti, il ciclo audio-reattivo interno prosegue normale).
    Alla DISATTIVAZIONE il timer riparte fresco da questo momento, invece di
    far scattare un cambio immediato per il tempo accumulato mentre era
    congelato - stessa logica di force_couple() per un rientro "morbido"."""
    was_active = model.loop_scene
    model.loop_scene = enabled
    if was_active and not enabled:
        model.couple_start_time = current_time

def get_loop_scene():
    """True se il loop sulla scena_A corrente e' attivo."""
    return model.loop_scene

def get_monitor_outputs(current_time):
    """Quale/i delle 2 uscite show mostrare accesa - vedi
    HybridCouplesModel.get_monitor_outputs. Chiamato da pupa.py ad ogni
    tick (solo Linux)."""
    return model.get_monitor_outputs(current_time)


_UNIVERSAL_FALLBACK_TRANSITIONS = ["Cut", "Taglio", "Fade", "Dissolvenza"]


def _find_fallback_transition(available_transitions):
    """Transizione di ripiego per un pool che resta vuoto dopo la
    validazione - Cut/Fade sono nativi OBS, presenti in QUALUNQUE
    installazione fresca (a differenza di Burn/Displace/Blur, che
    richiedono Shadertastic). Prova nomi sia inglesi sia italiani, visto
    che il nome e' localizzato e non c'e' un ID stabile via WebSocket."""
    for name in _UNIVERSAL_FALLBACK_TRANSITIONS:
        if name in available_transitions:
            return name
    return available_transitions[0] if available_transitions else "Fade"


def validate_scenes(available_scenes, available_transitions):
    """Filtra COUPLES/COUPLE_TRANSITIONS contro le scene/transizioni
    REALMENTE presenti in OBS. Va chiamata da pupa.py subito dopo la
    connessione (prima di initialize_model).

    - Scene_A non trovate in OBS: la coppia intera viene rimossa
    - Scene_B mancanti: tolte dal pool della coppia (la coppia resta se ne
      sopravvive almeno una)
    - Transizioni mancanti nel pool di una coppia: sostituite con un
      fallback nativo OBS (Cut/Fade)
    - Se alla fine non sopravvive nessuna coppia, o resta una sola scena in
      tutto: attiva DEGENERATE_MODE (vedi decide_next_scene), pupa lampeggia
      sulla stessa scena invece di alternare A/B.
    - STROBE_COLOR_POOL: colori non trovati in OBS tolti dal pool (es. se
      non hai ancora creato red/blue/yellow_master, resta solo white_master).
    - BLACK_PAUSE_SCENE non trovata: pausa nera disattivata (probabilita' a
      zero) invece di tentare switch a vuoto verso una scena inesistente.
    """
    global COUPLES, COUPLE_TRANSITIONS, DEGENERATE_MODE, STROBE_COLOR_POOL, BLACK_PAUSE_PROBABILITY, IDENTITY

    available_scenes_set = set(available_scenes)
    available_transitions = list(available_transitions)
    fallback_trans = _find_fallback_transition(available_transitions)

    filtered_couples = {}
    filtered_transitions = {}
    for a_scene, b_pool in COUPLES.items():
        if a_scene not in available_scenes_set:
            debug_log(f"[VALIDATE] {a_scene} non trovata in OBS, coppia rimossa")
            continue
        available_b = [b for b in b_pool if b in available_scenes_set]
        if not available_b:
            debug_log(f"[VALIDATE] {a_scene}: nessuna scena_B disponibile nel pool {b_pool}, coppia rimossa")
            continue
        if len(available_b) < len(b_pool):
            debug_log(f"[VALIDATE] {a_scene}: pool_B ridotto a {available_b} (mancava/mancavano {set(b_pool) - set(available_b)})")
        filtered_couples[a_scene] = available_b

        trans_pool = COUPLE_TRANSITIONS.get(a_scene, [])
        available_trans = [t for t in trans_pool if t in available_transitions]
        if not available_trans:
            debug_log(f"[VALIDATE] {a_scene}: nessuna transizione del pool {trans_pool} disponibile, fallback a '{fallback_trans}'")
            available_trans = [fallback_trans]
        filtered_transitions[a_scene] = available_trans

    COUPLES = filtered_couples
    COUPLE_TRANSITIONS = filtered_transitions

    if not COUPLES or len(available_scenes_set) <= 1:
        DEGENERATE_MODE = True
        debug_log(f"[VALIDATE] MODALITA' DEGENERATA: {len(available_scenes_set)} scena/e OBS, "
                  f"{len(COUPLES)} coppie valide - lampeggio invece di alternare A/B")
    else:
        DEGENERATE_MODE = False
        debug_log(f"[VALIDATE] {len(COUPLES)} coppie valide su {len(available_scenes_set)} scene OBS")

    available_colors = [c for c in STROBE_COLOR_POOL if c in available_scenes_set]
    if not available_colors:
        available_colors = [STROBE_SCENE] if STROBE_SCENE in available_scenes_set else []
    if available_colors != STROBE_COLOR_POOL:
        debug_log(f"[VALIDATE] STROBE_COLOR_POOL ridotto a {available_colors} "
                  f"(mancava/mancavano {set(STROBE_COLOR_POOL) - set(available_colors)})")
    STROBE_COLOR_POOL = available_colors or ["white_master"]  # ultima rete di sicurezza, mai lista vuota

    if BLACK_PAUSE_SCENE not in available_scenes_set:
        debug_log(f"[VALIDATE] {BLACK_PAUSE_SCENE} non trovata, pausa nera disattivata")
        BLACK_PAUSE_PROBABILITY = 0.0

    # IDENTITA' per scena_A: scarta le entry di scene_A non sopravvissute alla
    # validazione, e per quelle rimaste toglie i singoli campi (transition/
    # color/wave_kick) non ancora presenti in OBS - _get_identity() ricade sui
    # meccanismi generici pre-esistenti quando un campo manca, invece di
    # tentare uno switch a vuoto verso una transizione/scena inesistente.
    filtered_identity = {}
    for a_scene, entry in IDENTITY.items():
        if a_scene not in COUPLES:
            continue
        fixed_entry = dict(entry)
        if fixed_entry.get("transition") not in available_transitions:
            debug_log(f"[VALIDATE] identity[{a_scene}].transition '{fixed_entry.get('transition')}' "
                      f"non disponibile, tolta (fallback generico)")
            fixed_entry.pop("transition", None)
        if fixed_entry.get("color") not in available_scenes_set:
            debug_log(f"[VALIDATE] identity[{a_scene}].color '{fixed_entry.get('color')}' "
                      f"non disponibile, tolta (fallback generico)")
            fixed_entry.pop("color", None)
        if fixed_entry.get("wave_kick") not in available_scenes_set:
            debug_log(f"[VALIDATE] identity[{a_scene}].wave_kick '{fixed_entry.get('wave_kick')}' "
                      f"non disponibile, tolta (fallback generico)")
            fixed_entry.pop("wave_kick", None)
        filtered_identity[a_scene] = fixed_entry
    IDENTITY = filtered_identity

    return {"couples": COUPLES, "couple_transitions": COUPLE_TRANSITIONS, "degenerate": DEGENERATE_MODE,
            "strobe_color_pool": STROBE_COLOR_POOL, "black_pause_enabled": BLACK_PAUSE_PROBABILITY > 0}
