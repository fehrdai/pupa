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

import math
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

import scene_discovery as sd

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
    "futureflash_A": ["urbanfree_B"],
    "kusanagi_A":    ["strobo_B"],
    "montezuma_A":   ["psicodance_B"],
    "mri_A":         ["segnali_B"],
}
# POOL CONDIVISO (2026-07-?? Fase 2, operatore): stesso pool per OGNI
# scena_A invece di pool dedicati diversi per coppia - stessa filosofia
# gia' usata per ALL_B_SCENES/STROBE_COLOR_POOL, non piu' una curatela
# per-coppia. Fractal/Plasma non sono piu' firme d'identita' esclusive
# (vedi _DEFAULT_IDENTITY_SETS sotto), Digital Gltch entra qui invece di
# essere solo per l'ingresso wave_kick (che ora riusa questo stesso pool).
_SHARED_COUPLE_TRANSITIONS_POOL = ["Burn", "Displace", "Digital Gltch", "Plasma", "Blur"]
_DEFAULT_COUPLE_TRANSITIONS = {
    "montezuma_A": list(_SHARED_COUPLE_TRANSITIONS_POOL),
    "kusanagi_A": list(_SHARED_COUPLE_TRANSITIONS_POOL),
    "mri_A": list(_SHARED_COUPLE_TRANSITIONS_POOL),
    "futureflash_A": list(_SHARED_COUPLE_TRANSITIONS_POOL),
}
_DEFAULT_SPECIAL_SCENES = {"wave_kick": "wave_kick", "strobo": "white_color", "black": "black_color"}
_DEFAULT_STROBE_COLOR_POOL = ["white_color", "red_color", "blue_color", "green_color"]
# IDENTITA' (vedi scenes_config.yaml per la spiegazione): bundle fissi
# (transizione firma + colore + variante kick + waveform), SLEGATI dalle
# scene_A - ruotano in modo indipendente (vedi self.identity_shuffle_bag),
# non piu' un abbinamento fisso scena_A -> identita'.
_DEFAULT_IDENTITY_SETS = [
    # 2026-07-29 (operatore): giallo eliminato, solo RGB puri (rosso/verde/
    # blu) - bianco riservato allo strobo, non e' una identita' che ruota.
    # Lightspeed resta firma UNICA per tutte.
    {"transition": "Lightspeed", "color": "red_color",   "wave_kick": "kick1", "waveform": "red_wave"},
    {"transition": "Lightspeed", "color": "blue_color",  "wave_kick": "kick3", "waveform": "blue_wave"},
    {"transition": "Lightspeed", "color": "green_color", "wave_kick": "kick4", "waveform": "green_wave"},
]
# META-COPPIE (RIPRISTINATO 2026-07-21 - vedi _get_identity_duos): 2 coppie
# FISSE di scene_A, non piu' derivate dal pool_B condiviso (quel calcolo
# smise di avere senso quando la ristrutturazione 2026-07-15 diede ad ogni
# scena_A un pool_B dedicato univoco - ogni "duo" finiva per avere 1 sola
# scena_A, bloccando la finestra di META_COUPLE_DURATION su una sola
# scena_A per l'intera finestra invece di alternarne 2).
_DEFAULT_META_PAIR_DUOS = [
    ["futureflash_A", "kusanagi_A"],
    ["montezuma_A", "mri_A"],
]

SCENES_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scenes_config.yaml")


def _load_scenes_config(path=SCENES_CONFIG_PATH):
    """Carica coppie/transizioni/scene speciali/pool colori/identita' da
    scenes_config.yaml. Fallback ai valori hardcoded sopra se il file manca,
    e' invalido, o pyyaml non e' installato - nessuna rottura per chi non lo tocca."""
    defaults = (dict(_DEFAULT_COUPLES), dict(_DEFAULT_COUPLE_TRANSITIONS),
                dict(_DEFAULT_SPECIAL_SCENES), list(_DEFAULT_STROBE_COLOR_POOL),
                [dict(s) for s in _DEFAULT_IDENTITY_SETS],
                [list(d) for d in _DEFAULT_META_PAIR_DUOS])
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
        identity_sets = data.get("identity_sets") or _DEFAULT_IDENTITY_SETS
        meta_pair_duos = data.get("meta_pair_duos") or _DEFAULT_META_PAIR_DUOS
        debug_log(f"[CONFIG] scenes_config.yaml caricato: {len(couples)} coppie")
        return couples, transitions, special, color_pool, identity_sets, meta_pair_duos
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
COUPLES, COUPLE_TRANSITIONS, SPECIAL_SCENES, STROBE_COLOR_POOL, IDENTITY_SETS, META_PAIR_DUOS = _load_scenes_config()

# POOL CONDIVISO di tutte le scene_B (ristrutturazione 2026-07-15: prima
# ogni scena_A pescava SOLO dal proprio pool in COUPLES, ora _select_b_scene
# pesca da QUESTO pool comune a tutte le coppie - stessa filosofia di
# disaccoppiamento gia' usata per IDENTITY_SETS, per piu' varieta' nel tempo.
# COUPLES resta invariato e serve ancora per la VALIDAZIONE (una scena_A e'
# valida solo se il SUO pool ha almeno una _B reale in OBS) - ricalcolato
# anche dentro validate_scenes() dopo il filtraggio.
def _compute_all_b_scenes():
    seen = []
    for pool in COUPLES.values():
        for b in pool:
            if b not in seen:
                seen.append(b)
    return seen


ALL_B_SCENES = _compute_all_b_scenes()


def discover_and_merge_config(available_scenes, all_inputs, scene_item_names, path=SCENES_CONFIG_PATH):
    """Riempie le sezioni MANCANTI di scenes_config.yaml (couples,
    strobe_color_pool, identity_sets) scoprendole dalla convenzione di
    denominazione OBS (vedi scene_discovery.py: _A/_B/_kick/_color/_wave),
    lasciando intatta ogni sezione gia' presente nel file - "il config vince
    se c'e', altrimenti si scopre". Scrive il risultato su disco cosi'
    scenes_config.yaml diventa un artefatto GENERATO/ispezionabile (obiettivo
    esplicito dell'operatore: "il file popolato da pupa quando si interfaccia
    con OBS"), non solo un file da editare a mano.

    Va chiamata da pupa.py DOPO la connessione a OBS (serve la lista scene/
    input reale) e PRIMA di validate_scenes() (che poi filtra/degrada come
    sempre). Ricarica e riassegna i globals del modulo dal file appena
    scritto, cosi' la sessione corrente usa gia' quanto scoperto.

    available_scenes: lista nomi scena (obs.cache_scenes()).
    all_inputs: obs.get_all_inputs() - per riconoscere slide/colore per KIND,
        non per nome.
    scene_item_names: {nome_scena: [nomi sorgenti nidificate]} per ogni
        scena _A/_B scoperta - obs.get_scene_item_source_names(scena) per
        ciascuna, serve solo al riconoscimento slide qui.

    Ritorna la lista delle scene_A/_B riconosciute come slide per CONTENUTO
    (sorgente di kind 'slideshow', qualunque nome) - non scritta su disco,
    ricalcolata ad ogni avvio dal contenuto reale, non e' una curatela."""
    raw = {}
    if yaml is not None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f) or {}
        except FileNotFoundError:
            raw = {}
        except Exception as e:
            debug_log(f"[CONFIG] {path} illeggibile per la scoperta ({e}), riparto da vuoto")
            raw = {}

    changed = False

    if not raw.get("couples"):
        raw["couples"] = sd.discover_couples(available_scenes)
        changed = True
        debug_log(f"[DISCOVERY] couples scoperte: {raw['couples']}")

    color_scenes = sd.discover_color_scenes(available_scenes)
    non_black_colors = [c for c in color_scenes if c != sd.BLACK_COLOR_SCENE]

    if not raw.get("strobe_color_pool"):
        raw["strobe_color_pool"] = list(non_black_colors)
        changed = True
        debug_log(f"[DISCOVERY] strobe_color_pool scoperto: {raw['strobe_color_pool']}")

    if not raw.get("identity_sets"):
        # Un colore diventa una vera IDENTITA' (bundle color+waveform+kick
        # che viaggiano insieme) solo se ha una scena _wave omonima - questo
        # e' il segnale di contenuto che lo distingue da un colore
        # puramente utility/accento (es. white_color, usato solo per lo
        # strobo - niente "white_wave" in questa convenzione). Evita di
        # dover escludere "white"/"black" per nome, che sarebbe di nuovo un
        # hardcode - qui il criterio e' "ha un waveform abbinato", non "non
        # e' nero".
        wave_scenes = set(sd.discover_wave_scenes(available_scenes))
        kick_scenes = sd.discover_kick_scenes(available_scenes)
        identity_colors = []
        for color in non_black_colors:
            base = color[: -len("_color")]
            if f"{base}_wave" in wave_scenes:
                identity_colors.append((color, f"{base}_wave"))
        if identity_colors:
            identity_sets = []
            for i, (color, waveform) in enumerate(identity_colors):
                entry = {"color": color, "waveform": waveform}
                if kick_scenes:
                    entry["wave_kick"] = kick_scenes[i % len(kick_scenes)]
                identity_sets.append(entry)
            raw["identity_sets"] = identity_sets
            changed = True
        debug_log(f"[DISCOVERY] identity_sets scoperti: {identity_sets}")

    if not sd.has_black_color(color_scenes):
        print(f"[PUPA] ATTENZIONE: nessuna scena '{sd.BLACK_COLOR_SCENE}' trovata in OBS - "
              f"i flash colorati e lo strobo bianco saranno sostituiti dal nero.")
        debug_log(f"[CONFIG] {sd.BLACK_COLOR_SCENE} assente - fallback al nero attivo per flash/strobo")

    if changed and yaml is not None:
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(
                    "# scenes_config.yaml - sezioni mancanti generate/integrate automaticamente\n"
                    "# da PUPA (scene_discovery.py) leggendo la convenzione di denominazione OBS\n"
                    "# (_A/_B/_kick/_color/_wave). Le sezioni gia' presenti qui sono state\n"
                    "# rispettate come override - solo quelle assenti sono state riempite.\n\n"
                )
                yaml.safe_dump(raw, f, allow_unicode=True, sort_keys=False)
            debug_log(f"[CONFIG] {path} aggiornato con le sezioni scoperte da OBS")
        except Exception as e:
            debug_log(f"[CONFIG] scrittura di {path} fallita: {e}")

    global COUPLES, COUPLE_TRANSITIONS, SPECIAL_SCENES, STROBE_COLOR_POOL, IDENTITY_SETS, META_PAIR_DUOS
    global ALL_B_SCENES, STROBE_SCENE, BLACK_PAUSE_SCENE
    COUPLES, COUPLE_TRANSITIONS, SPECIAL_SCENES, STROBE_COLOR_POOL, IDENTITY_SETS, META_PAIR_DUOS = _load_scenes_config(path)
    ALL_B_SCENES = _compute_all_b_scenes()
    STROBE_SCENE = SPECIAL_SCENES.get("strobo", "white_color")
    BLACK_PAUSE_SCENE = SPECIAL_SCENES.get("black", "black_color")

    slide_input_names = sd.slideshow_input_names(all_inputs)
    slide_scenes = [
        scene for scene, items in scene_item_names.items()
        if sd.is_slide_scene(items, slide_input_names)
    ]
    return slide_scenes


# TEST TEMPORANEO 2026-07-17 - "ho bisogno di testarla in azione nella
# logica di pupa per sapere se funziona" (slideshow "slide"): se valorizzato,
# forza OGNI rotazione di coppia a scegliere questa scena_B invece della
# normale pescata dal pool (~1/5 di probabilita' altrimenti) - cosi' si vede
# entrare in gioco ripetutamente in pochi minuti invece di aspettare la
# fortuna. Test 2026-07-17 CONFERMATO FUNZIONANTE (dopo aver corretto un
# percorso immagini non piu' valido in OBS, causa reale del "non si vede" -
# vedi [[project_color_overlay_and_slideshow]]). Rimesso a None per tornare
# al funzionamento normale (pescata dal pool intero) - riattivare mettendo
# il nome di una scena_B per un futuro test mirato, senza doverlo
# reimplementare da zero.
DEBUG_FORCE_B_SCENE = None

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
#
# FINESTRA invece di istante fisso (2026-07-14): cambiare scena_A a un
# istante fisso, indipendentemente da cosa sta succedendo nella musica,
# rischiava di cadere in pieno DROP/BUILD - il taglio piu' grande e vistoso
# che PUPA fa, nel momento peggiore per farlo. Ora e' una finestra
# [MIN, MAX]: raggiunto il MIN, il cambio scatta al primo momento buono
# (ingresso in BREAK, gia' un segnale affidabile usato altrove) invece che
# subito - MAX resta come tetto di sicurezza se la musica non scende mai
# (set molto sostenuti, nessun break naturale entro la finestra).
META_COUPLE_DURATION_MIN = 900   # 15 minuti
META_COUPLE_DURATION_MAX = 1500  # 25 minuti

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
TRANSITION_INTENSITY_RANK = {"Digital Gltch": 4, "Plasma": 4, "Displace": 3, "Burn": 2, "Blur": 1}
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
# "esaltando" la coppia col suo colore/wave_kick identitari (vedi IDENTITY_SETS).
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
STROBE_SCENE = SPECIAL_SCENES.get("strobo", "white_color")  # rinominata da strobo_B
BLACK_PAUSE_SCENE = SPECIAL_SCENES.get("black", "black_color")
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
# Allargati +50% il 2026-07-15 (operatore: un'uscita restava accesa troppo
# poco tempo per volta). Allargati ANCORA (~60-70%) il 2026-07-17: tripla
# motivazione (sensazione generale "scattosa", il blocco visivo dei 2
# monitor sotto carico, e il gap di responsivita' Linux/Windows) - meno
# chiamate wmctrl al secondo aiuta tutte e tre, vedi PUPA_DEVELOPMENT_LOG.md.
MONITOR_ALTERNATION_INTERVAL_RANGE = {
    State.INTRO:  (5.0, 9.0),
    State.BREAK:  (5.0, 9.0),
    State.RELAX:  (3.5, 7.0),
    State.GROOVE: (1.5, 3.5),
    State.BUILD:  (0.7, 2.0),
}
MONITOR_BOTH_ON_STATES = (State.DROP, State.PEAK)
BEATS_PER_BAR = 4  # griglia interna di beat_count (audio_analyzer), non il vero downbeat del DJ - vedi get_monitor_outputs

# 2026-07-17 (opzione B - SEQUENZA PROGRAMMATA): sostituisce il sistema
# probabilistico costruito in precedenza lo stesso giorno (rerolling pesato
# ad ogni bivio, poi reso "appiccicoso" con un dwell casuale) - l'operatore:
# "le transizioni da monitor_a e monitor_b o sono a raffica o niente altro...
# troppo statico [nel senso: mai un vero ciclo leggibile]", chiesta una vera
# struttura (monitor_A acceso N battute, poi monitor_B N battute, ripeti) con
# variazioni creative SOPRA quella struttura, non al posto suo. Il vecchio
# sistema (MONITOR_CONFIG_WEIGHTS/DWELL_CYCLES, disegnato per la stessa
# esigenza) e' stato accantonato perche' restava probabilistico anche nella
# sua forma "appiccicosa": la struttura A/B era comunque un evento raro tra
# tanti possibili, non un ciclo garantito.
#
# Quanti battute dura ciascun lato (A o B) del ciclo, per stato - piu' lungo
# negli stati calmi, piu' corto in quelli energici (stessa filosofia della
# vecchia MONITOR_ALTERNATION_INTERVAL_RANGE, ora espressa direttamente in
# battute invece che in secondi convertiti).
MONITOR_SEQUENCE_BARS = {
    State.INTRO:  4,
    State.BREAK:  4,
    State.RELAX:  3,
    State.GROOVE: 2,
    State.BUILD:  1,
}
# "Respiro" occasionale (entrambe accese o entrambe spente) inserito DOPO un
# giro A+B completo - l'accento creativo sopra la struttura, non un rimpiazzo
# della stessa. Probabilita' di innescarsi a fine giro, per stato:
MONITOR_BREATHER_PROBABILITY = {
    State.INTRO:  0.30,
    State.BREAK:  0.40,
    State.RELAX:  0.30,
    State.GROOVE: 0.15,
    State.BUILD:  0.10,
}
MONITOR_BREATHER_BARS = 2  # durata del respiro quando innescato, in battute - fisso, non serve altra variabilita' qui
MONITOR_BREATHER_CHOICE_WEIGHTS = {
    State.INTRO:  {"both_off": 0.6, "both_on": 0.4},
    State.BREAK:  {"both_off": 0.7, "both_on": 0.3},
    State.RELAX:  {"both_off": 0.6, "both_on": 0.4},
    State.GROOVE: {"both_off": 0.3, "both_on": 0.7},
    State.BUILD:  {"both_off": 0.2, "both_on": 0.8},
}

# AMBIENT LUCI (2026-07-29, Step 2 del piano luci - wiggly-moseying-blum.md):
# durante gli stati di quiete le luci fisiche passano a un wash soffuso
# indipendente dai kick, che SOSTITUISCE del tutto il pulsare a kick su QLC+
# (conferma esplicita dell'operatore - non convive con esso). Il colore
# resta quello dell'identita' corrente (get_identity_color_name(), stesso
# dizionario nome->RGB gia' usato dal pulso a kick in pupa.py) - solo
# l'intensita' cambia, un respiro lento (coseno, mai negativo) invece del
# picco/decadimento legato al kick.
AMBIENT_LIGHT_STATES = (State.INTRO, State.BREAK, State.RELAX)
AMBIENT_BREATH_PERIOD_S = 10.0  # durata di un ciclo respiro completo (salita+discesa)
AMBIENT_PEAK_PCT = 22  # intensita' di picco del respiro, percentuale - basso apposta ("soffuso")

# ALTERNANZA FARI (2026-07-29, Step 3 del piano luci): stesso schema di
# get_monitor_outputs()/_advance_monitor_sequence() (sequenza A/B programmata
# a battute + respiro occasionale both_on/both_off) applicato ai 2 fari
# fisici invece delle 2 uscite monitor - stato indipendente (light_seq_phase,
# non sincronizzato con monitor_seq_phase), stesse tabelle di partenza
# (riferimento diretto, non copia - se in futuro servono tempi diversi per
# le luci basta assegnare dict separati qui).
LIGHT_SEQUENCE_BARS = MONITOR_SEQUENCE_BARS
LIGHT_BREATHER_PROBABILITY = MONITOR_BREATHER_PROBABILITY
LIGHT_BREATHER_BARS = MONITOR_BREATHER_BARS
LIGHT_BREATHER_CHOICE_WEIGHTS = MONITOR_BREATHER_CHOICE_WEIGHTS

# 2026-07-30 (operatore, modalita' 'inverse'): soglia (percentuale, 0-100)
# oltre la quale lo schermo e' considerato "abbastanza nero" da accendere
# entrambi i fari - vedi _get_light_outputs_inverse(). Un valore basso
# (non serve schermo TOTALMENTE nero) cattura anche l'inizio/fine del
# respiro a battito, non solo il picco.
LIGHT_INVERSE_BLACKNESS_THRESHOLD_PCT = 20

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
    # 2026-07-29 (operatore): via gli accenti RGB - solo bianco/nero, ma con
    # un mix che varia per stato invece di un peso fisso 35/35 uguale
    # ovunque, cosi' da ottenere "sfumature di grigio" statistiche (piu'
    # nero = grigio scuro negli stati calmi, piu' bianco = grigio chiaro/
    # luminoso in quelli energici) senza bisogno di scene _color grigie
    # dedicate (non esistono in OBS, non richieste).
    State.INTRO:  {"white_color": 0.30, "black_color": 0.70},
    State.BREAK:  {"white_color": 0.30, "black_color": 0.70},
    State.RELAX:  {"white_color": 0.30, "black_color": 0.70},
    State.GROOVE: {"white_color": 0.50, "black_color": 0.50},
    State.BUILD:  {"white_color": 0.50, "black_color": 0.50},
    State.DROP:   {"white_color": 0.70, "black_color": 0.30},
    State.PEAK:   {"white_color": 0.70, "black_color": 0.30},
}

STROBE_BURST_COUNT = 4          # numero di flash (ON+OFF) per raffica
STROBE_BURST_INTERVAL = 0.10    # secondi tra un frame e l'altro, fallback se il BPM non e' ancora stimato
STROBE_BEAT_DIVISOR = 4         # 1/4 di battito (sedicesimo) - a 150 BPM coincide col vecchio 0.10 fisso
STROBE_BURST_PROBABILITY = {
    # Dimezzate 2026-07-21 (stesso intervento gia' fatto su CUT_BURST_PROBABILITY
    # il 07-17 per lo stesso tipo di lamentela - "molto meglio" il verdetto
    # allora): test dal vivo di stasera, 2501 STROBE su ~4300 switch totali
    # (~58%) - dominava nettamente il traffico di scena, coerente col
    # "troppi flash color_master" gia' segnalato la sessione precedente.
    State.PEAK: 0.175,  # era 0.35
    State.DROP: 0.075,  # era 0.15
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
# L'intervallo tra un taglio e l'altro usa _get_strobe_interval() (agganciato
# al beat, stesso meccanismo dello strobe) invece di un valore fisso proprio -
# a BPM tipici (~125-130) un sedicesimo coincide quasi esattamente col
# vecchio 0.12s fisso, quindi il cambio e' a basso rischio: stessa sensazione
# quando il BPM e' nella norma, ma ora davvero agganciato al brano invece di
# essere una coincidenza numerica.
# Soglia abbassata da -15 a -10 (piu' permissiva, cattura anche pull-back
# piu' leggeri) e probabilita' raddoppiate: nel primo test live nessuna
# raffica e' scattata in 2 minuti nonostante pull-back osservati - non
# sappiamo se per soglia mai raggiunta o dado sfavorevole (non logghiamo
# il trend), quindi si agisce su entrambe per sicurezza.
CUT_BURST_TREND_THRESHOLD = -10.0  # quanto deve scendere bass sotto bass_avg per contare come "pull-back"
CUT_BURST_COOLDOWN = 2.0  # secondi minimi tra una raffica di cut e la successiva

# FLASH NERO PRE-DROP (2026-07-17, V2 - vera previsione, non il semplice
# bordo BUILD->DROP): a differenza di CUT_BURST_TREND_THRESHOLD (trend
# ISTANTANEO, bass singolo blocco vs bass_avg), qui serve una risalita
# SOSTENUTA su qualche secondo - la classica rampa prima di un drop. Confronta
# la media energia dell'ultimo mezzo secondo con quella di 0.5-2s fa
# (RUNUP_WINDOW_SHORT/LONG, in campioni a 20Hz) contro l'escursione recente
# (percentile 10-90 di energy_history, stessa filosofia adattiva di
# _adaptive_thresholds - non soglie fisse, si adatta al genere). Se sale
# abbastanza rispetto a quell'escursione, consideriamo in corso una risalita
# degna del flash - PRIMA che sfoci in un vero State.DROP/PEAK.
# 2026-07-17: RICALIBRATO dopo il primo test dal vivo (log RUNUP-CALIBRAZIONE
# in _detect_runup) - con finestre corte (0.5s/2s) e soglia 0.35, slope_frac
# oscillava tra -0.9 e +0.9 nel giro di pochi secondi anche su musica "piatta"
# (tecno con bass_avg gia' costantemente alto, range dinamico compresso -
# bastano piccole oscillazioni naturali per generare frazioni enormi con un
# range 10-90 percentile piccolo). 69 falsi positivi in un test di pochi
# minuti. Fix combinato (non una sola leva): finestre piu' larghe (smussano
# il rumore), soglia piu' alta, E persistenza nel tempo (deve superarla per
# RUNUP_PERSISTENCE_S di fila, non un istante isolato - un singolo campione
# sopra soglia si azzera subito se scende anche per un frame).
RUNUP_WINDOW_SHORT = 24   # ~1.2s a 20Hz: media "adesso" (era 10/~0.5s)
RUNUP_WINDOW_LONG = 80    # ~4.0s a 20Hz: finestra totale, la parte prima di SHORT e' la "baseline" (era 40/~2.0s)
# 2026-07-17: ricalibrato una seconda volta - 54 flash in ~13min dal vivo,
# ancora troppo frequente per un accento che deve leggersi come raro/
# predittivo. Tre leve insieme (non una sola) come nella prima calibrazione:
# soglia piu' alta, persistenza piu' lunga, cooldown piu' lungo.
RUNUP_SLOPE_FRACTION = 0.65  # era 0.5 (originale 0.35)
RUNUP_PERSISTENCE_S = 0.7  # era 0.4 - il superamento soglia deve reggere almeno cosi' a lungo di fila
RUNUP_FLASH_COOLDOWN = 10.0  # era 6.0 - secondi minimi tra un flash e il successivo
RUNUP_FLASH_MAX_ACTIVE_S = 15.0  # se la risalita si blocca senza mai sfociare in DROP/PEAK, si riarma comunque dopo questo tempo

# ELEGGIBILITA' PER STATO (2026-07-22): prima escludeva solo DROP/PEAK (il
# flash ha gia' "fatto centro" li'), lasciando eleggibili anche INTRO/BREAK/
# RELAX - dove una "risalita" prima di un drop non ha senso musicale (non
# si sta costruendo verso niente). Analizzando un test dal vivo: 15 flash
# in 14 minuti, ma 7/15 durante INTRO/RELAX/GROOVE invece che durante una
# vera tensione in crescita - probabile causa del "non sembra legato a un
# drop vero" riportato dal vivo. Ristretto a BUILD/GROOVE, gli unici stati
# dove una risalita sostenuta rappresenta davvero un'anticipazione di drop.
RUNUP_ELIGIBLE_STATES = (State.BUILD, State.GROOVE)

# SOGLIA BREAK STABILE (2026-07-22): _adaptive_thresholds() calcolava anche
# la soglia break dagli stessi ~45s di energy_history usati per peak/build/
# groove - per un break BREVE va bene (si adatta al genere), ma un break
# SOSTENUTO riempie sempre piu' quella finestra di campioni "bassi", facendo
# scendere il 12* percentile INSIEME al break stesso: rincorsa infinita,
# osservato dal vivo (un break lungo "sfuma" in RELAX senza che la musica
# sia cambiata). Su una finestra molto piu' lunga lo stesso break pesa una
# frazione molto piu' piccola, quindi la soglia resta stabile - vedi
# self.energy_history_long in __init__ e l'uso dentro _adaptive_thresholds.
LONG_ENERGY_HISTORY_SAMPLES = 4800  # ~4 minuti a 20Hz (contro i 900/~45s della finestra breve)
# Rete di sicurezza indipendente dai percentili: sotto questo valore
# assoluto (0-100, stessa scala normalizzata di bass/bass_avg) e' SEMPRE
# break, anche nell'improbabile caso in cui pure la finestra lunga si fosse
# adattata verso il basso - un vero quasi-silenzio non deve mai "sfumare"
# in RELAX.
BREAK_ABSOLUTE_FLOOR = 8

# SEGNALE BREAK ROBUSTO (2026-07-22): bass_avg normale e' comunque diviso
# per il tetto AGC VELOCE di audio_analyzer.py (AGC_RELEASE) - durante un
# break prolungato quel tetto insegue verso il basso in pochi minuti, e una
# volta abbassato un silenzio successivo torna a sembrare una percentuale
# "normale" invece di un crollo netto (scoperto dal vivo 2026-07-22). Il
# bass_avg_long esposto da audio_analyzer.py (stesso bass grezzo, tetto a
# rilascio 10x piu' lento, AGC_RELEASE_LONG) non si e' ancora eroso allo
# stesso modo - sotto questa soglia e' un break vero anche se bass_avg
# "normale" non lo mostra piu' chiaramente.
BREAK_LONG_FLOOR_PCT = 15

# CAMBIO TRACCIA (2026-07-22): PUPA non aveva nessun modo di distinguere un
# vero cambio di brano/DJ transition da una pausa interna al brano corrente
# - "INTRO" esisteva solo come timer legato alla rotazione scena_A, mai
# all'audio. Riusa due segnali gia' esistenti invece di costruire una nuova
# analisi spettrale da zero: un break piu' lungo del tipico "respiro"
# interno a un brano (TRACK_CHANGE_MIN_BREAK_S) SEGUITO da un BPM che, una
# volta ristabilizzato dopo il break, e' cambiato di parecchio rispetto a
# prima (TRACK_CHANGE_BPM_DELTA) - la combinazione dei due e' molto piu'
# specifica di uno solo (un break lungo da solo puo' essere solo un
# breakdown lungo dello stesso brano; un piccolo drift di BPM da solo puo'
# essere solo l'imprecisione naturale della stima). Se scatta, forza un
# mini-INTRO (stessa durata di _intro_window(), non un nuovo numero) -
# cosi' un vero cambio di traccia riceve lo stesso trattamento "calmo"
# gia' riservato all'inizio di ogni coppia_A, indipendentemente da quando
# e' scattato l'ultimo cambio scena_A.
TRACK_CHANGE_MIN_BREAK_S = 8.0    # sotto questa durata, un break e' probabilmente solo un respiro interno al brano
TRACK_CHANGE_BPM_DELTA = 8.0      # BPM ristabilizzato che si scosta almeno cosi' tanto da quello pre-break
TRACK_CHANGE_CHECK_DELAY_S = 8.0  # attesa dopo l'uscita da break prima di confrontare il BPM - serve tempo perche' si ristabilizzi sui nuovi kick (audio_analyzer._update_bpm richiede >=4 intervalli validi)
TRACK_CHANGE_COOLDOWN_S = 30.0    # secondi minimi tra un rilevamento e il successivo

# 2026-07-17: dimezzate su tutti gli stati - "diminuzione dei tagli",
# tripla motivazione (sensazione scattosa + blocco monitor sotto carico +
# gap Linux/Windows, vedi PUPA_DEVELOPMENT_LOG.md). STROBE_BURST_PROBABILITY
# lasciata invariata (gia' riservata a DROP/PEAK, non la fonte principale).
CUT_BURST_PROBABILITY = {
    State.BUILD:  0.25,
    State.GROOVE: 0.20,
    State.DROP:   0.15,
    State.PEAK:   0.15,
    State.RELAX:  0.10,
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
        # Finestra [MIN, MAX] invece di istante fisso - vedi commento sopra
        # META_COUPLE_DURATION_MIN/MAX per il perche'.
        self.COUPLE_DURATION_MIN = 150  # 2.5 minuti
        self.COUPLE_DURATION_MAX = 360  # 6 minuti

        self.last_switch_time = 0

        # GIRO INIZIALE (vedi initialize()/_select_new_couple()): mazzo
        # mescolato con TUTTE le scene_A, consumato una volta sola all'avvio
        # per mostrarle tutte in fila senza ripetizioni prima di passare al
        # ciclo a coppie fisse - vedi META_PAIR_DUOS. startup_tour_done
        # diventa True quando il mazzo si svuota (non a tempo).
        self.startup_tour_bag = []
        self.startup_tour_done = False

        # META-COPPIA tra scene_A (vedi META_COUPLE_DURATION sopra): quali 2
        # scene_A sono "in gioco" per i prossimi ~20 minuti
        self.meta_couple_start_time = 0
        self.current_meta_pair = []
        self.meta_pair_shuffle_bag = []  # "mazzo mescolato", vedi _select_new_meta_pair
        self.current_identity = {}  # identita' (transition/color/wave_kick/waveform) della coppia corrente, vedi _roll_new_identity
        self.identity_shuffle_bag = []  # "mazzo mescolato", vedi _roll_new_identity

        # STATE MACHINE
        self.current_state = State.INTRO
        self.state_start_time = 0
        self.energy_history = deque(maxlen=900)  # ~45s a 20Hz, per le soglie adattive (vedi _adaptive_thresholds)
        self.energy_history_long = deque(maxlen=LONG_ENERGY_HISTORY_SAMPLES)  # ~4min, solo per la soglia break (vedi _adaptive_thresholds)
        self.last_bass = 0  # Ultimo valore bass live, per modulazione continua
        self.last_bass_avg = 0  # Ultima media bass, per calcolare la velocita' del break
        self.last_bass_avg_long = 0  # bass_avg sul tetto AGC lento (vedi BREAK_LONG_FLOOR_PCT) - break piu' robusto
        self.last_bpm = 0.0  # Ultimo BPM stimato da audio_analyzer, per l'intervallo strobo agganciato al beat
        self.last_is_beat = False  # Griglia di beat di audio_analyzer (vedi get_monitor_outputs)
        self.last_beat_count = 0
        self.calm_level = 0  # 0-3, impostato dall'hotkey OBS (vedi CALM_MULTIPLIERS e set_calm_level)
        self.loop_scene = False  # hotkey OBS: congela il timer 4min sulla scena_A corrente (vedi set_loop_scene)

        # FLASH NERO PRE-DROP (vedi RUNUP_* e _detect_runup)
        self.runup_flash_active = False  # gia' scattato per QUESTA risalita, in attesa che si risolva prima di poter riscattare
        self.runup_flash_start_time = 0  # per il timeout RUNUP_FLASH_MAX_ACTIVE_S
        self.last_runup_flash_time = -RUNUP_FLASH_COOLDOWN  # permette un flash gia' dal primo avvio, non solo dopo il cooldown
        self.pre_drop_flash_pending = False  # letto (e consumato) da pupa.py per pilotare black_overlay
        self.last_runup_debug_log_time = 0  # rate-limit per il log temporaneo di calibrazione in _detect_runup
        self.runup_condition_since = None  # timestamp da cui slope_frac supera la soglia SENZA interruzioni (persistenza)

        self.monitor_last_flip_time = 0
        self.monitor_last_flip_bar = 0  # bar corrente (beat_count // BEATS_PER_BAR) al momento dell'ultimo cambio di fase
        self.monitor_seq_phase = "A"  # fase corrente della sequenza programmata: "A" / "B" / "both_on" / "both_off" (vedi get_monitor_outputs)
        self.light_last_flip_time = 0
        self.light_last_flip_bar = 0  # stessa idea di monitor_last_flip_bar, stato indipendente (Step 3 piano luci)
        self.light_seq_phase = "A"  # fase dell'alternanza fari - stesso schema di monitor_seq_phase, non sincronizzata con esso
        self.last_energy_trend = 0  # bass - bass_avg dell'ultimo _update_state, per la raffica di cut
        self.recent_kick_peak_bass = 0  # Massimo kick visto nello stato corrente (per il lampo singolo GROOVE/BUILD)
        self.last_cut_burst_time = 0  # Cooldown tra una raffica di cut e la successiva
        self.in_pullback = False  # Per l'edge-trigger della raffica di cut (vedi sopra)

        # Tracciamento uscita da BREAK: per estendere lo spazio di wave_kick
        # in BUILD/GROOVE durante una risalita rapida post-break
        self.break_exit_time = None
        self.bass_at_break_exit = 0

        # CAMBIO TRACCIA (vedi TRACK_CHANGE_* sopra)
        self.break_entered_at = 0.0        # timestamp di inizio del BREAK corrente, per misurarne la durata
        self.bpm_at_break_start = 0.0      # bpm stabile subito prima di entrare in BREAK (per il confronto post-break)
        self.break_duration_pending = 0.0  # durata dell'ultimo break, in attesa che il controllo BPM si esegua
        self.track_change_pending_check_at = 0.0  # 0 = nessun controllo in sospeso
        self.track_change_intro_until = 0.0       # forza INTRO fino a questo timestamp se rilevato un vero cambio traccia
        self.last_track_change_time = -TRACK_CHANGE_COOLDOWN_S  # permette un rilevamento gia' dal primo break, non solo dopo il cooldown

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
        self.overlap_start_time = 0          # per sintetizzare il respiro in-out-in-out (vedi get_black_pause_breath)
        self.overlap_hold_until = 0
        self.overlap_forward_duration_ms = 0
        self.overlap_reverse_duration_ms = 0
        self.overlap_transition_choice = "Fade"
        self.overlap_is_black_pause = False   # True solo se l'overlap corrente e' una vera PAUSA NERA

    def initialize(self, current_scene, current_time):
        """Inizializza stato e coppia.

        Scena_A di partenza e giro iniziale SEMPRE mescolati tra tutte
        quelle valide, ignorando quale scena_A OBS sta gia' mostrando (che
        tra un test e l'altro resta quasi sempre la stessa, es.
        urbanfree_A - "vedo sempre urbanfree_A"). pupa.py forza poi lo
        switch reale in OBS subito dopo initialize_model(), cosi' quello
        che si vede combacia da subito con quello che il modello crede.

        Giro iniziale (RIPRISTINATO 2026-07-21, vedi startup_tour_bag/
        _select_new_couple): mazzo mescolato con TUTTE le scene_A, "in
        fila" senza ripetizioni - non piu' una finestra a tempo
        (META_COUPLE_DURATION) come prima, che poteva tagliare il giro a
        meta' o prolungarlo oltre la copertura completa. Il passaggio al
        ciclo normale (coppie fisse, META_PAIR_DUOS) scatta quando il mazzo
        si SVUOTA (vedi _select_new_couple), non a un tempo fisso."""
        all_a = list(COUPLES.keys())
        self.startup_tour_bag = list(all_a)
        random.shuffle(self.startup_tour_bag)
        self.current_couple_a = self.startup_tour_bag.pop() if self.startup_tour_bag else "urbanfree_A"
        self.startup_tour_done = not self.startup_tour_bag  # gia' vero se c'era <=1 scena_A in tutto
        self.in_scene_a = True

        self.current_meta_pair = list(all_a)  # ancora "aperto" finche' il giro non finisce
        self.meta_pair_shuffle_bag = []  # forza un mescolamento fresco al primo vero cambio meta-coppia
        self.meta_couple_start_time = current_time

        self.identity_shuffle_bag = []  # forza un mescolamento fresco al primo vero cambio identita'
        self._roll_new_identity()

        self.current_b_scene = self._roll_next_b_scene()
        # PRIORITA' AVVIO 2026-07-16: se "slide" e' nel pool (appena
        # aggiunta, non ancora vista dal vivo), forza che sia la prima
        # scena_B mostrata al primo avvio di pupa.py - "cosi' la vediamo
        # subito all'opera". Solo un override una tantum su questo avvio,
        # non cambia il pool ne' le rotazioni successive.
        if "slide" in ALL_B_SCENES:
            self.current_b_scene = "slide"
            self.last_shown_b_scene = "slide"
        self.couple_start_time = current_time
        self.state_start_time = current_time
        self.current_state = State.INTRO
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

        self.burst_active = False
        self.overlap_active = False
        self.temp_b_scene = None
        self.temp_b_scene_time = 0
        self.in_pullback = False
        self.recent_kick_peak_bass = 0

    def _get_identity_duos(self):
        """Ritorna le meta-coppie FISSE di scene_A (META_PAIR_DUOS, da
        scenes_config.yaml) su cui _select_new_meta_pair() fa lo shuffle-bag.

        FINO al 2026-07-21 questo raggruppava le scene_A per pool_B
        condiviso - funzionava quando 2 scene_A condividevano lo stesso
        pool_B (vecchio sistema a 8 scene_A), ma la ristrutturazione
        2026-07-15 diede ad ogni scena_A un pool_B dedicato univoco: ogni
        "duo" calcolato cosi' finiva per avere 1 sola scena_A, bloccando la
        finestra di META_COUPLE_DURATION (15-25min) su una sola scena_A per
        l'intera finestra invece di alternarne 2 come previsto - una
        ricorrenza da 5+ ripetizioni consecutive serviva perche' emergesse
        chiaramente dal vivo. Le coppie sono ora esplicite e fisse, gia'
        filtrate da validate_scenes() sulle scene_A REALMENTE disponibili."""
        return [list(duo) for duo in META_PAIR_DUOS if duo]

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
        """Seleziona la prossima scena_A.

        GIRO INIZIALE (self.startup_tour_done ancora False): pesca dal
        mazzo mescolato (startup_tour_bag, riempito una volta sola in
        initialize()) - garantisce che tutte le scene_A compaiano una volta
        "in fila" prima di qualunque ripetizione. Quando il mazzo si
        svuota, passa SUBITO al ciclo normale chiamando
        _select_new_meta_pair() (sceglie la prima vera coppia fissa) -
        RIPRISTINATO 2026-07-21: prima il passaggio era legato al tempo
        (META_COUPLE_DURATION), che poteva tagliare il giro a meta' o
        prolungarlo oltre la copertura completa a seconda di quanto
        duravano le singole coppie.

        CICLO NORMALE (dopo il giro, coppie fisse da 2 - vedi
        META_PAIR_DUOS): alternanza DETERMINISTICA, l'unica scelta che non
        ripete quella attuale e' sempre l'altra - nessun mazzo/finestra
        serve con solo 2 opzioni fisse (il vecchio anti-repeat a finestra
        di 5, rimosso qui, si saturava in 2 pescate e permetteva comunque
        una ripetizione per puro caso, osservato dal vivo 2026-07-21)."""
        if not self.startup_tour_done:
            new_couple = self.startup_tour_bag.pop() if self.startup_tour_bag else self.current_couple_a
            if not self.startup_tour_bag:
                self.startup_tour_done = True
                self._select_new_meta_pair()
            return new_couple

        pool = self.current_meta_pair if self.current_meta_pair else list(COUPLES.keys())
        available = [a for a in pool if a != self.current_couple_a]
        return available[0] if available else pool[0]

    def _select_b_scene(self, couple_a, exclude=None):
        """Sceglie una scena _B dal pool CONDIVISO tra tutte le coppie
        (ALL_B_SCENES, ristrutturazione 2026-07-15 - prima pescava solo dal
        pool della coppia_A data, ora tutte le _B sono in gioco per
        qualunque scena_A, stessa filosofia di IDENTITY_SETS). `couple_a`
        non e' piu' usato per filtrare (tenuto come parametro per
        compatibilita' di firma) - se il pool e' vuoto o non validato,
        ricade su COUPLES[couple_a] come rete di sicurezza. Evita di
        ripetere `exclude` (l'ultima usata) se il pool ha piu' di
        un'opzione — cosi' la rotazione e' realmente percepibile."""
        if DEBUG_FORCE_B_SCENE and DEBUG_FORCE_B_SCENE in ALL_B_SCENES:
            return DEBUG_FORCE_B_SCENE
        pool = ALL_B_SCENES or COUPLES.get(couple_a, [])
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

    def _roll_new_identity(self):
        """Assegna una nuova identita' (vedi IDENTITY_SETS) alla coppia_A
        appena iniziata - chiamata da decide_next_scene ad ogni cambio
        scena_A, INDIPENDENTEMENTE da quale scena_A sia stata scelta
        (ristrutturazione 2026-07-15: prima ogni scena_A aveva
        un'identita' fissa, ora le 4 identita' ruotano libere cosi' ogni
        scena_A puo' comparire nel tempo con colori/kick diversi).

        "Mazzo mescolato" (self.identity_shuffle_bag), stesso principio di
        _select_new_meta_pair(): tutte le identita' vengono mescolate una
        volta e consumate una alla volta finche' il mazzo non si svuota,
        poi si rimescola - garantisce che tutte e 4 compaiano esattamente
        una volta ogni giro completo, invece di affidarsi al puro caso
        (che potrebbe ripetere la stessa identita' piu' volte di fila per
        coincidenza)."""
        if not IDENTITY_SETS:
            self.current_identity = {}
            return self.current_identity

        if not self.identity_shuffle_bag:
            self.identity_shuffle_bag = list(range(len(IDENTITY_SETS)))
            random.shuffle(self.identity_shuffle_bag)

        idx = self.identity_shuffle_bag.pop()
        self.current_identity = IDENTITY_SETS[idx] if idx < len(IDENTITY_SETS) else {}
        return self.current_identity

    def _get_identity(self):
        """Ritorna il dict identita' (transition/color/wave_kick/waveform)
        assegnato alla coppia_A CORRENTE (vedi _roll_new_identity) - gia'
        filtrato da validate_scenes() sui campi REALMENTE disponibili in
        OBS. Dict vuoto se non ancora assegnata (non dovrebbe capitare
        dopo initialize()) - i chiamanti gestiscono il fallback campo per
        campo."""
        return self.current_identity

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

    INTRO_SHARE_OF_COUPLE_MIN = 30.0 / 240.0  # rapporto tarato in origine (30s su una coppia da 240s fissi)

    def _intro_window(self):
        """Durata (secondi) della finestra INTRO forzata all'inizio di ogni
        coppia - proporzionale a COUPLE_DURATION_MIN invece di un valore
        fisso (era 30s fisso quando le coppie duravano 240s fissi SEMPRE).
        Con la finestra [MIN,MAX] introdotta il 2026-07-14 le coppie
        ruotano piu' spesso (media ~150-200s osservata dal vivo, contro i
        240s fissi di prima) - un INTRO fisso a 30s finiva per occupare una
        fetta di tempo PROPORZIONALMENTE piu' grande (~19% osservato invece
        del ~12.5% originale su 7 rotazioni/18min), favorendo wave_kick/
        ago_talk (eleggibili per default in INTRO) a scapito delle scene_B
        (osservato dal vivo: scene_B nettamente sotto-rappresentate, 11
        eventi contro 57 di wave_kick sulle stesse 300 righe di log).
        Scalare mantiene lo stesso rapporto tarato in origine indipendentemente
        da quanto dura una coppia adesso."""
        return self.COUPLE_DURATION_MIN * self.INTRO_SHARE_OF_COUPLE_MIN

    def _calm(self, key):
        """Moltiplicatore CALM MODE per l'asse richiesto ("cut"/"fade"/
        "black_prob"/"black_hold"), in base a self.calm_level (0-3). Vedi
        CALM_MULTIPLIERS - livello 0 ritorna sempre 1.0 (nessun effetto)."""
        return CALM_MULTIPLIERS.get(self.calm_level, CALM_MULTIPLIERS[0])[key]

    def get_monitor_outputs(self, current_time):
        """Decide quale/i delle 2 uscite show mostrare 'accesa' in questo
        istante - chiamata ad ogni tick da pupa.py (solo Linux, vedi
        secrets_local.py). In DROP/PEAK converge su ENTRAMBE accese fisse
        (MONITOR_BOTH_ON_STATES), sempre.

        2026-07-17 (opzione B, sostituisce il sistema probabilistico dello
        stesso giorno - vedi commento sopra MONITOR_SEQUENCE_BARS): una vera
        SEQUENZA programmata, non un dado ad ogni bivio. Fase "A" (show1
        acceso) per MONITOR_SEQUENCE_BARS[stato] battute, poi fase "B"
        (show2 acceso) per altrettante - un ciclo leggibile, non un'illusione
        statistica. Dopo un giro A+B completo, un "respiro" (both_on/
        both_off, MONITOR_BREATHER_PROBABILITY) puo' inserirsi come accento
        creativo SOPRA la struttura, non al suo posto. Fallback al vecchio
        timer libero (usando il MAX del range storico) se il BPM non e'
        ancora stimato.

        Ritorna {"show1": bool, "show2": bool}."""
        if self.current_state in MONITOR_BOTH_ON_STATES:
            return {"show1": True, "show2": True}

        bars_needed = MONITOR_BREATHER_BARS if self.monitor_seq_phase in ("both_on", "both_off") \
            else MONITOR_SEQUENCE_BARS.get(self.current_state, 2)

        if self.last_bpm > 0:
            current_bar = self.last_beat_count // BEATS_PER_BAR
            is_bar_start = self.last_beat_count % BEATS_PER_BAR == 0
            if (self.last_is_beat and is_bar_start
                    and (current_bar - self.monitor_last_flip_bar) >= bars_needed):
                self.monitor_last_flip_bar = current_bar
                self.monitor_last_flip_time = current_time
                self._advance_monitor_sequence()
                debug_log(f"[MONITOR-SEQ] fase={self.monitor_seq_phase} stato={self.current_state.value} "
                          f"bar={current_bar} bars_needed={bars_needed}")
        else:
            _, hi = MONITOR_ALTERNATION_INTERVAL_RANGE.get(self.current_state, (2.0, 4.0))
            if current_time - self.monitor_last_flip_time >= hi:
                self.monitor_last_flip_time = current_time
                self.monitor_last_flip_bar = self.last_beat_count // BEATS_PER_BAR
                self._advance_monitor_sequence()
                debug_log(f"[MONITOR-SEQ] fase(fallback)={self.monitor_seq_phase} stato={self.current_state.value}")

        if self.monitor_seq_phase == "A":
            return {"show1": True, "show2": False}
        if self.monitor_seq_phase == "B":
            return {"show1": False, "show2": True}
        if self.monitor_seq_phase == "both_on":
            return {"show1": True, "show2": True}
        return {"show1": False, "show2": False}  # "both_off"

    def _advance_monitor_sequence(self):
        """Avanza la sequenza programmata (vedi get_monitor_outputs):
        A -> B sempre; da B, o torna ad A (giro normale) o - con
        MONITOR_BREATHER_PROBABILITY - si inserisce un respiro (both_on/
        both_off, scelto pesato per stato via MONITOR_BREATHER_CHOICE_WEIGHTS);
        da un respiro si torna sempre ad A."""
        if self.monitor_seq_phase == "A":
            self.monitor_seq_phase = "B"
        elif self.monitor_seq_phase == "B":
            prob = MONITOR_BREATHER_PROBABILITY.get(self.current_state, 0.0)
            if random.random() < prob:
                weights = MONITOR_BREATHER_CHOICE_WEIGHTS.get(self.current_state, {"both_off": 0.5, "both_on": 0.5})
                self.monitor_seq_phase = random.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
            else:
                self.monitor_seq_phase = "A"
        else:  # era un respiro (both_on/both_off) - torna al ciclo normale
            self.monitor_seq_phase = "A"

    def get_ambient_light(self, current_time):
        """Intensita' (0.0-1.0, gia' scalata al picco AMBIENT_PEAK_PCT) del
        wash ambient per gli stati di quiete - None se lo stato corrente non
        e' uno di AMBIENT_LIGHT_STATES (nessun ambient da applicare, il
        chiamante deve usare il pulso a kick normale). Il colore da usare e'
        lo stesso get_identity_color_name() - pupa.py risolve nome->RGB e
        scala per questa intensita', stesso dizionario gia' usato dal pulso
        a kick, nessuna palette ambient separata."""
        if self.current_state not in AMBIENT_LIGHT_STATES:
            return None
        phase = (current_time % AMBIENT_BREATH_PERIOD_S) / AMBIENT_BREATH_PERIOD_S
        breath = (1 - math.cos(2 * math.pi * phase)) / 2  # 0..1..0, liscio, mai negativo
        return breath * (AMBIENT_PEAK_PCT / 100.0)

    # MODALITA' LUCI (2026-07-29, operatore) - 3 modalita' di funzionamento
    # per il rapporto fari/monitor, pensate come selezionabili a runtime via
    # hotkey OBS (stesso meccanismo di set_calm_level/set_loop_scene) - non
    # ancora cablate a nessun hotkey, solo scaffold/commento per ora, come
    # richiesto esplicitamente ("mantieni le 3 opzioni solo commentandole").
    # Quella ATTIVA oggi e' "alternate" (get_light_outputs sotto).
    #   1. "sync"      - i 2 fari mostrano sempre lo stesso colore
    #                    contemporaneamente, nessuna alternanza - il
    #                    comportamento originale pre-Step 3 (_qlc_set_rgb_both
    #                    manda RGB pieno a entrambi, senza gate).
    #   2. "alternate" - alternanza A/B indipendente dai monitor (Step 3,
    #                    ATTIVA oggi): get_light_outputs()/_advance_light_sequence()
    #                    sotto, stato proprio (light_seq_phase).
    #   3. "inverse"   - complementare ai monitor (proposta operatore
    #                    2026-07-29, collegata alla visione "luci/video
    #                    complementari" di una sessione precedente): fari
    #                    accesi SOLO quando i monitor sono nella fase
    #                    both_off (get_monitor_outputs() -> entrambi False),
    #                    spenti mentre il video e' visibile. Da implementare
    #                    come branch alternativo qui dentro, selezionato da
    #                    una futura LIGHT_MODE invece che come stato a parte -
    #                    l'operatore vuole provarla ma non ha ancora deciso se
    #                    sostituisce "alternate" o si aggiunge come 3a opzione.
    def get_light_outputs(self, current_time, screen_blackness_pct=0.0, wave_scene_showing=False):
        """Dispatcher tra le 3 modalita' luci (vedi commento sopra) - non
        ancora un vero hotkey/LIGHT_MODE selezionabile, solo lo switch
        manuale usato per il test dal vivo del 2026-07-29. ATTIVA ORA:
        'inverse' (proposta dell'operatore, da provare dal vivo). 'alternate'
        (Step 3, comportamento precedente) resta sotto come metodo separato,
        pronto per essere rimesso attivo cambiando questa riga.

        screen_blackness_pct (0-100): quanto nero c'e' REALMENTE a schermo
        in questo istante (overlay a battito + flash pre-drop + respiro
        pausa nera - gia' unificati da pupa.py in un solo combined_pct,
        vedi BLACK_OVERLAY_*/PRE_DROP_FLASH_*/get_black_pause_breath_phase).
        wave_scene_showing: True se la scena Program corrente e' una _wave -
        vedi _get_light_outputs_inverse per l'enfasi che abilita. Entrambi
        passati solo a 'inverse' - le altre modalita' li ignorano."""
        return self._get_light_outputs_inverse(current_time, screen_blackness_pct, wave_scene_showing)
        # return self._get_light_outputs_alternate(current_time)  # modalita' precedente (Step 3), non attiva ora

    def _get_light_outputs_inverse(self, current_time, screen_blackness_pct=0.0, wave_scene_showing=False):
        """Modalita' 'inverse': complementare PER POSIZIONE, non "tutto o
        niente" - fixture1 e' l'inverso di show1, fixture2 l'inverso di
        show2 (2026-07-29, corretto dopo il primo test dal vivo - la prima
        versione trattava "1 monitor acceso" come "1 monitor acceso" invece
        di differenziare quale faro si accende). Soddisfa tutti e 4 i casi
        richiesti dall'operatore:
          - 1 monitor acceso + 1 spento -> il faro CORRISPONDENTE a quello
            spento si accende, l'altro resta spento (non "tutti e due o
            nessuno").
          - 2 monitor spenti (both_off) -> 2 fari accesi.
          - 2 monitor accesi (both_on) -> 2 fari spenti (lo strobo NON e'
            toccato da questo gate - pilota Master per-frame indipendentemente,
            vedi Step 1 - "eccetto strobo" e' gia' vero strutturalmente).
        Riusa get_monitor_outputs() per leggere la fase corrente - funziona
        anche se l'attivazione fisica dei 2 monitor (stacking finestre, solo
        quando configurata) non e' attiva su questa macchina, dato che qui
        serve solo la FASE del sequencer, non lo switch fisico delle finestre.

        2026-07-30 (operatore): la fase A/B/both_on/both_off del sequencer
        monitor e' TROPPO GROSSOLANA da sola - non cattura l'overlay nero a
        battito ne' la pausa nera vera e propria durante le sovrapposizioni,
        che sono i "nero" che l'operatore percepisce piu' spesso ("quando i
        2 monitor fanno intermittenza sul nero le luci dovrebbero seguire").
        Proposta operatore, confermata: unificare TUTTI e 3 i "neri" invece
        di ascoltare solo il sequencer - se lo schermo e' visivamente scuro
        ORA (screen_blackness_pct sopra soglia - gia' unifica overlay a
        battito + flash pre-drop + respiro pausa nera, vedi pupa.py
        combined_pct) ENTRAMBI i fari si accendono, a prescindere dalla fase
        del sequencer monitor.

        2026-07-30, ENFASI colore_wave (operatore, implementata): la scena
        Program e' UNICA e condivisa (show1/show2 mostrano lo stesso
        contenuto su 2 uscite fisiche diverse, si alterna solo QUALE delle 2
        e' visibile, non il contenuto stesso) - quindi "il faro corrispondente
        al monitor che mostra la sua scena _wave" si riduce a: se la scena
        Program corrente e' una _wave (wave_scene_showing=True, passato da
        pupa.py che conosce current_scene), il faro il cui lato e' "acceso"
        si accende COMUNQUE come accento, anche se la regola inverse sopra
        direbbe spento per quel lato."""
        if screen_blackness_pct >= LIGHT_INVERSE_BLACKNESS_THRESHOLD_PCT:
            return {"fixture1": True, "fixture2": True}
        monitor_state = self.get_monitor_outputs(current_time)
        fixture1 = (not monitor_state["show1"]) or wave_scene_showing
        fixture2 = (not monitor_state["show2"]) or wave_scene_showing
        return {"fixture1": fixture1, "fixture2": fixture2}

    def _get_light_outputs_alternate(self, current_time):
        """Modalita' 'alternate' (Step 3 originale): stessa identica
        filosofia di get_monitor_outputs() (sequenza A/B programmata a
        battute, con un respiro occasionale both_on/both_off), ma con stato
        indipendente (light_seq_phase) - NON sincronizzata con l'alternanza
        monitor, stesse tabelle di partenza. In DROP/PEAK converge su
        entrambi accesi fissi, come per i monitor. Ritorna {"fixture1":
        bool, "fixture2": bool}."""
        if self.current_state in MONITOR_BOTH_ON_STATES:
            return {"fixture1": True, "fixture2": True}

        bars_needed = LIGHT_BREATHER_BARS if self.light_seq_phase in ("both_on", "both_off") \
            else LIGHT_SEQUENCE_BARS.get(self.current_state, 2)

        if self.last_bpm > 0:
            current_bar = self.last_beat_count // BEATS_PER_BAR
            is_bar_start = self.last_beat_count % BEATS_PER_BAR == 0
            if (self.last_is_beat and is_bar_start
                    and (current_bar - self.light_last_flip_bar) >= bars_needed):
                self.light_last_flip_bar = current_bar
                self.light_last_flip_time = current_time
                self._advance_light_sequence()
                debug_log(f"[LIGHT-SEQ] fase={self.light_seq_phase} stato={self.current_state.value} "
                          f"bar={current_bar} bars_needed={bars_needed}")
        else:
            _, hi = MONITOR_ALTERNATION_INTERVAL_RANGE.get(self.current_state, (2.0, 4.0))
            if current_time - self.light_last_flip_time >= hi:
                self.light_last_flip_time = current_time
                self.light_last_flip_bar = self.last_beat_count // BEATS_PER_BAR
                self._advance_light_sequence()
                debug_log(f"[LIGHT-SEQ] fase(fallback)={self.light_seq_phase} stato={self.current_state.value}")

        if self.light_seq_phase == "A":
            return {"fixture1": True, "fixture2": False}
        if self.light_seq_phase == "B":
            return {"fixture1": False, "fixture2": True}
        if self.light_seq_phase == "both_on":
            return {"fixture1": True, "fixture2": True}
        return {"fixture1": False, "fixture2": False}  # "both_off"

    def _advance_light_sequence(self):
        """Avanza la sequenza fari - identica a _advance_monitor_sequence()
        ma su light_seq_phase/LIGHT_BREATHER_*, stato indipendente."""
        if self.light_seq_phase == "A":
            self.light_seq_phase = "B"
        elif self.light_seq_phase == "B":
            prob = LIGHT_BREATHER_PROBABILITY.get(self.current_state, 0.0)
            if random.random() < prob:
                weights = LIGHT_BREATHER_CHOICE_WEIGHTS.get(self.current_state, {"both_off": 0.5, "both_on": 0.5})
                self.light_seq_phase = random.choices(list(weights.keys()), weights=list(weights.values()), k=1)[0]
            else:
                self.light_seq_phase = "A"
        else:  # era un respiro (both_on/both_off) - torna al ciclo normale
            self.light_seq_phase = "A"

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
        # un vero "respiro" senza immagini invece del solito peek parziale
        # verso B/A - "manca il nero... troppo illuminata". A differenza del
        # peek normale (blend 40-60%), qui si va al 100% (non ha senso una
        # "mezza pausa nera" semi-trasparente).
        #
        # 2026-07-17: target non piu' fisso su BLACK_PAUSE_SCENE
        # (black_master) - "la pulsazione nera li' non si vede, magari con
        # sotto waveform". Ora va sulla waveform_color dell'identita'
        # corrente (che ha black_overlay nidificato, vedi pupa.py) cosi' il
        # respiro pulsa contro il movimento della waveform invece che contro
        # nero fisso; fallback su BLACK_PAUSE_SCENE se l'identita' non ha
        # ancora una waveform assegnata/disponibile in questa installazione OBS.
        black_pause_prob = min(CALM_BLACK_PAUSE_PROB_CAP, BLACK_PAUSE_PROBABILITY * self._calm("black_prob"))
        is_black_pause = random.random() < black_pause_prob

        if is_black_pause:
            peek_target_scene = self._get_identity().get("waveform") or BLACK_PAUSE_SCENE
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
        self.overlap_start_time = current_time  # per sintetizzare il respiro in-out-in-out (vedi get_black_pause_breath)
        self.overlap_hold_until = current_time + hold_time
        self.overlap_forward_duration_ms = forward_ms
        self.overlap_reverse_duration_ms = reverse_ms
        self.overlap_transition_choice = random.choice(OVERLAP_TRANSITION_CHOICES)
        self.overlap_is_black_pause = is_black_pause

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

        def pct(p, data=None, data_n=None):
            d = data if data is not None else sorted_hist
            dn = data_n if data_n is not None else n
            idx = min(dn - 1, max(0, int(dn * p)))
            return d[idx]

        peak = pct(0.90)
        build = pct(0.70)
        groove = pct(0.45)

        # BREAK: dalla finestra LUNGA (energy_history_long, ~4min) invece
        # che dai 45s brevi usati sopra - vedi commento su
        # LONG_ENERGY_HISTORY_SAMPLES/BREAK_ABSOLUTE_FLOOR. Un break
        # sostenuto pesa una frazione molto piu' piccola su 4 minuti che su
        # 45 secondi, quindi il 12* percentile non lo rincorre piu' verso
        # il basso mentre e' ancora in corso. Fallback alla finestra breve
        # se quella lunga non ha ancora abbastanza storia.
        if len(self.energy_history_long) >= 60:
            long_sorted = sorted(self.energy_history_long)
            break_th = pct(0.12, long_sorted, len(long_sorted))
        else:
            break_th = pct(0.12)

        # Distacco minimo tra peak/build/groove: con poca varianza (es. un
        # drone quasi costante) i percentili potrebbero collassare vicini,
        # facendo oscillare lo stato per fluttuazioni minime. break_th NON
        # entra piu' in questa catena (2026-07-22): agganciarlo a
        # "groove - 3" lo ricollegava al valore del gruppo breve, che
        # durante un break sostenuto decade ESATTAMENTE come break_th
        # decadeva prima del fix - vanificando la finestra lunga appena
        # introdotta. break_th resta libero di essere il 12* percentile
        # della finestra lunga, punto.
        build = min(build, peak - 3)
        groove = min(groove, build - 3)

        return {"peak": peak, "build": build, "groove": groove, "break": break_th}

    def _detect_runup(self):
        """Rileva una risalita di energia sostenuta (vedi RUNUP_* sopra) -
        vera previsione pre-drop, non il semplice bordo BUILD->DROP. Ritorna
        True al massimo una volta per risalita (self.runup_flash_active fa
        da guardia, resettato altrove quando la risalita si risolve)."""
        if self.runup_flash_active:
            self.runup_condition_since = None  # non accumulare persistenza mentre un flash e' gia' in corso
            return False
        if len(self.energy_history) < RUNUP_WINDOW_LONG:
            return False

        hist = list(self.energy_history)[-RUNUP_WINDOW_LONG:]
        recent = hist[-RUNUP_WINDOW_SHORT:]
        baseline = hist[:-RUNUP_WINDOW_SHORT]

        def median(values):
            s = sorted(values)
            mid = len(s) // 2
            return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2

        # Mediana, non media: una risalita VERA deve elevare l'intera
        # finestra recente, non un singolo blocco isolato (un kick forte da
        # solo sposterebbe una media su 10 campioni, ma non la mediana) -
        # distingue una rampa sostenuta da un transiente isolato, che ha
        # gia' il suo segnale dedicato (is_kick/last_energy_trend).
        recent_med = median(recent)
        baseline_med = median(baseline)

        sorted_hist = sorted(hist)
        n = len(sorted_hist)
        p10 = sorted_hist[max(0, int(n * 0.10))]
        p90 = sorted_hist[min(n - 1, int(n * 0.90))]
        dynamic_range = max(1.0, p90 - p10)

        slope_frac = (recent_med - baseline_med) / dynamic_range

        now = time.time()

        # PERSISTENZA: slope_frac deve reggere sopra soglia per
        # RUNUP_PERSISTENCE_S di fila, non un istante isolato - un solo
        # campione sopra soglia (spike di rumore) azzera il timer al primo
        # frame in cui ridiscende, niente "quasi ce l'aveva fatta".
        above = slope_frac > RUNUP_SLOPE_FRACTION
        if above:
            if self.runup_condition_since is None:
                self.runup_condition_since = now
        else:
            self.runup_condition_since = None
        sustained = above and self.runup_condition_since is not None and (now - self.runup_condition_since) >= RUNUP_PERSISTENCE_S

        # LOG TEMPORANEO 2026-07-17 - per ricalibrare con numeri REALI
        # (primo giro: 69 falsi positivi con finestre/soglia troppo strette,
        # vedi commento su RUNUP_WINDOW_SHORT/LONG) invece di ritentare alla
        # cieca. Logga il valore anche quando NON scatta (rate-limited a
        # 1/s) - da rimuovere una volta ricalibrata la soglia.
        if now - self.last_runup_debug_log_time > 1.0:
            self.last_runup_debug_log_time = now
            debug_log(f"[RUNUP-CALIBRAZIONE] slope_frac={slope_frac:.3f} "
                       f"(recent_med={recent_med:.1f} baseline_med={baseline_med:.1f} "
                       f"range={dynamic_range:.1f}) soglia={RUNUP_SLOPE_FRACTION} sustained={sustained}")

        return sustained

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
        self.energy_history_long.append(energy)

        th = self._adaptive_thresholds()

        # Logica: associa energia a stato secondo le soglie adattive correnti.
        # INTRO forzato in due casi, non solo il primo: appena iniziata la
        # coppia_A corrente, OPPURE appena rilevato un vero cambio di
        # traccia (vedi TRACK_CHANGE_*/decide_next_scene) - stesso
        # trattamento "calmo", due trigger diversi (rotazione scena vs
        # audio reale).
        if couple_elapsed < self._intro_window() or current_time < self.track_change_intro_until:
            new_state = State.INTRO
        elif energy > th["build"] and energy_trend > 10:
            new_state = State.DROP
        elif energy > th["peak"]:
            new_state = State.PEAK
        elif energy > th["build"]:
            new_state = State.BUILD
        elif energy > th["groove"]:
            new_state = State.GROOVE
        elif (energy < th["break"] or energy < BREAK_ABSOLUTE_FLOOR
                or (self.last_bass_avg_long > 0 and self.last_bass_avg_long < BREAK_LONG_FLOOR_PCT)):
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
        """Sceglie UNA transizione tra TUTTE quelle del pool (non piu' solo
        le 2 estreme per rango - 2026-07-?? Fase 2, operatore: un pool
        condiviso a 5+ membri con la vecchia meccanica a 2 estremi ne
        sprecava sempre 3, mai scelte), pesando verso quelle piu' "intense"
        (rank piu' alto in TRANSITION_INTENSITY_RANK) o piu' "calme" a
        seconda dello stato corrente.

        Blend continuo: peso_i = p*rank_i + (1-p)*(rank_max+1-rank_i), dove
        p = TRANSITION_INTENSITY_PROBABILITY dello stato corrente. A p=1.0
        pesa puramente sul rango (favorisce le piu' intense); a p=0.0 pesa
        sull'inverso (favorisce le piu' calme); a p=0.5 tutti i pesi
        diventano uguali (scelta uniforme) - stesso comportamento di prima
        nei 3 casi limite, ma ora su tutto il pool invece che su 2 estremi
        fissi."""
        if len(couple_pool) < 2:
            return couple_pool[0] if couple_pool else "Burn"
        ranks = [TRANSITION_INTENSITY_RANK.get(t, 0) for t in couple_pool]
        rank_max = max(ranks)
        p = TRANSITION_INTENSITY_PROBABILITY.get(self.current_state, 0.5)
        weights = [p * r + (1 - p) * (rank_max + 1 - r) for r in ranks]
        return random.choices(couple_pool, weights=weights, k=1)[0]

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
        - wave_kick (entrata): stesso pool/meccanismo pesato del ciclo principale, durata reattiva all'energia
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

        # CAMBIO COPPIA: transizione "firma" dell'identita' appena assegnata
        # (vedi IDENTITY_SETS/_roll_new_identity), un'unica volta all'ingresso.
        # Controllato PRIMA del branch generico INTRO sotto, perche'
        # current_state e' gia' INTRO a questo punto (impostato da
        # decide_next_scene insieme al kind). Fallback al normale Fade se
        # l'identita' non ha una firma valida (transizione non disponibile
        # in OBS - gia' filtrato da validate_scenes).
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

        # FORCE: wave_kick in ENTRATA, bypassa tutto il resto.
        # 2026-07-?? (operatore): era SEMPRE "Stinger" - scoperto che le
        # impostazioni dello Stinger su questa installazione sono VUOTE
        # (nessun video caricato, verificato via get_current_scene_transition())
        # perche' Stinger accetta solo file video, non immagini (l'operatore
        # voleva caricare un logo .png) - quindi non stava mai davvero
        # facendo l'effetto "video flourish" per cui era stato scelto.
        # Sostituito con un pool a caso tra Fade e "Digital Gltch" (nome
        # esatto verificato via get_scene_transition_list - typo reale
        # nell'installazione, non un errore di battitura qui), entrambe
        # transizioni honeste su cosa fanno davvero.
        #
        # **Bug di durata trovato e corretto nello stesso momento**: i
        # 20000ms fissi avevano senso SOLO con lo Stinger (durata FISSA lato
        # OBS, quel valore probabilmente veniva ignorato) - con Fade/Digital
        # Gltch (entrambe NON a durata fissa) viene rispettato per davvero,
        # un crossfade di 20s REALI ad ogni ingresso in wave_kick, molto
        # oltre il range normale (FADE_DURATION_RANGE, 500-2800ms) -
        # osservato dal vivo: stati a bassa energia (dove wave_kick<->_A e'
        # il ciclo dominante) diventati statici, scena_B quasi scomparsa.
        # Corretto usando la stessa durata reattiva all'energia di ogni
        # altro Fade nel codice, invece di un numero fisso slegato.
        if self.temp_b_scene == "wave_kick":
            # 2026-07-?? (operatore): riusa lo stesso pool/meccanismo pesato
            # del ciclo principale (COUPLE_TRANSITIONS + _weighted_couple_
            # transition) invece di un pair fisso (Fade, Digital Gltch) a
            # parte - un solo posto dove il pool di transizioni e' definito.
            couple_pool = COUPLE_TRANSITIONS.get(self.current_couple_a, ["Burn", "Displace"])
            trans_type = self._weighted_couple_transition(couple_pool)
            fade_ms = self._get_fade_duration_ms()
            debug_log(f"[TRANS] wave_kick -> {trans_type} {fade_ms}ms")
            return {"type": trans_type, "duration_ms": fade_ms, "is_return": False, "kick_mode": "wave"}

        # BREAK: cut/fade/blur alternati, reattivi alla velocita' del crollo
        # bass (break brusco -> piu' probabile Taglio veloce; break lento ->
        # Fade o Blur, scelti a caso tra loro per varieta' - Taglio risultava
        # dominante anche nei break lenti perche' l'unica alternativa era
        # sempre Fade, mai qualcosa di diverso da un Taglio).
        if self.current_state == State.BREAK:
            drop_rate = max(0.0, self.last_bass_avg - self.last_bass)
            cut_prob = min(0.8, drop_rate / 30.0) * self._calm("cut")
            if random.random() < cut_prob:
                trans_type, duration_ms = "Taglio", 300
            else:
                trans_type = random.choice(["Fade", "Blur"])
                duration_ms = self._get_fade_duration_ms()
            debug_log(f"[TRANS] BREAK reattivo: {trans_type} {duration_ms}ms (drop_rate={drop_rate:.1f}, cut_prob={cut_prob:.2f})")
            return {"type": trans_type, "duration_ms": duration_ms, "is_return": is_return}

        # INTRO: ciclo wave_kick<->_A, per lo piu' Fade (l'ENTRATA in wave_kick
        # e' gia' gestita sopra, sempre Fade 20s), con una probabilita' di
        # Taglio al posto del Fade ("aumentiamo
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
        bass_avg_long = audio_data.get("bass_avg_long", 0)
        is_kick = audio_data.get("is_kick", False)
        is_drop = audio_data.get("is_drop", False)
        bpm = audio_data.get("bpm", 0.0)
        is_beat = audio_data.get("is_beat", False)
        beat_count = audio_data.get("beat_count", 0)

        self.last_bass = bass
        self.last_bass_avg = bass_avg
        self.last_bass_avg_long = bass_avg_long
        self.last_bpm = bpm
        self.last_is_beat = is_beat
        self.last_beat_count = beat_count
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

        # CAMBIO TRACCIA, ingresso in BREAK (vedi TRACK_CHANGE_* sopra):
        # salva subito il BPM stabile di QUESTO istante, prima che il break
        # stesso lo congeli (durante un break i kick si diradano/spariscono,
        # quindi audio_analyzer smette di aggiornare la stima e resta
        # fermo su questo valore per tutta la durata del break).
        if prev_state != State.BREAK and self.current_state == State.BREAK:
            self.break_entered_at = current_time
            self.bpm_at_break_start = self.last_bpm

        # Rileva uscita da BREAK: serve per estendere lo spazio di wave_kick
        # in BUILD/GROOVE durante una risalita rapida (vedi _wave_kick_eligible)
        if prev_state == State.BREAK and self.current_state != State.BREAK:
            self.break_exit_time = current_time
            self.bass_at_break_exit = bass
            # BUG TROVATO 2026-07-17: audio_analyzer.py azzera beat_count
            # all'uscita da BREAK (resync naturale, per design), ma
            # monitor_last_flip_bar restava al valore vecchio (es. bar 195)
            # - con current_bar ripartito da 0, il confronto
            # "(current_bar - monitor_last_flip_bar) >= bars_needed" diventa
            # sempre falso (negativo contro positivo) finche' beat_count non
            # ricresce oltre il vecchio valore, potenzialmente per minuti:
            # la configurazione (es. "both_off", entrambi i monitor neri)
            # resta bloccata tutto quel tempo invece di ririsolversi al
            # prossimo bivio. Azzerare qui, in sync con l'azzeramento di
            # beat_count, risolve alla radice.
            self.monitor_last_flip_bar = 0
            self.light_last_flip_bar = 0  # stesso motivo di monitor_last_flip_bar sopra (Step 3 piano luci)

            # CAMBIO TRACCIA: non decidiamo SUBITO (il BPM appena uscito dal
            # break non si e' ancora ristabilizzato sui nuovi kick, vedi
            # TRACK_CHANGE_CHECK_DELAY_S) - schedula un controllo differito,
            # eseguito piu' sotto ad ogni tick finche' non e' il momento.
            break_duration = current_time - self.break_entered_at
            if break_duration >= TRACK_CHANGE_MIN_BREAK_S and self.bpm_at_break_start > 0:
                self.break_duration_pending = break_duration
                self.track_change_pending_check_at = current_time + TRACK_CHANGE_CHECK_DELAY_S
            else:
                self.track_change_pending_check_at = 0.0

        # CAMBIO TRACCIA: controllo differito (vedi sopra) - eseguito una
        # sola volta, TRACK_CHANGE_CHECK_DELAY_S dopo l'uscita dal break
        # candidato, quando il BPM ha avuto il tempo di ristabilizzarsi sui
        # nuovi kick.
        if self.track_change_pending_check_at > 0 and current_time >= self.track_change_pending_check_at:
            bpm_delta = abs(self.last_bpm - self.bpm_at_break_start) if self.last_bpm > 0 else 0.0
            cooldown_ok = (current_time - self.last_track_change_time) >= TRACK_CHANGE_COOLDOWN_S
            if bpm_delta >= TRACK_CHANGE_BPM_DELTA and cooldown_ok:
                self.last_track_change_time = current_time
                self.track_change_intro_until = current_time + self._intro_window()
                msg = (f"[TRACK-CHANGE] rilevato: break={self.break_duration_pending:.1f}s "
                       f"bpm {self.bpm_at_break_start:.0f}->{self.last_bpm:.0f} (delta={bpm_delta:.0f}) "
                       f"- forzo INTRO per {self._intro_window():.0f}s")
                print(msg)
                debug_log(msg)
            else:
                debug_log(f"[TRACK-CHANGE] non rilevato: break={self.break_duration_pending:.1f}s "
                           f"bpm {self.bpm_at_break_start:.0f}->{self.last_bpm:.0f} (delta={bpm_delta:.0f}, "
                           f"soglia={TRACK_CHANGE_BPM_DELTA:.0f}) cooldown_ok={cooldown_ok}")
            self.track_change_pending_check_at = 0.0  # controllo consumato, una sola volta

        # FLASH NERO PRE-DROP (vedi RUNUP_* e _detect_runup): la risalita si
        # considera risolta (si puo' riarmare un nuovo flash) quando si entra
        # DAVVERO in DROP/PEAK (l'anticipazione ha "fatto centro") o quando
        # scade RUNUP_FLASH_MAX_ACTIVE_S senza che ci sia mai arrivata
        # (risalita stagnante/fallita - non deve restare bloccata per sempre).
        if self.runup_flash_active:
            resolved_by_arrival = self.current_state in (State.DROP, State.PEAK)
            resolved_by_timeout = (current_time - self.runup_flash_start_time) > RUNUP_FLASH_MAX_ACTIVE_S
            if resolved_by_arrival or resolved_by_timeout:
                self.runup_flash_active = False
        elif (self.current_state in RUNUP_ELIGIBLE_STATES
                and (current_time - self.last_runup_flash_time) > RUNUP_FLASH_COOLDOWN
                and self._detect_runup()):
            self.runup_flash_active = True
            self.runup_flash_start_time = current_time
            self.last_runup_flash_time = current_time
            self.pre_drop_flash_pending = True
            debug_log(f"[RUNUP] flash pre-drop innescato (stato={self.current_state.name})")

        # ====================================================================
        # 1. TIMER COPPIA SCADUTO? Finestra [MIN, MAX] invece di istante
        # fisso (vedi commento su COUPLE_DURATION_MIN/MAX in __init__): dopo
        # il MIN, aspetta il primo momento buono (ingresso in BREAK) per
        # scattare, invece di tagliare a caso in mezzo a un DROP/BUILD - il
        # MAX resta un tetto di sicurezza se la musica non scende mai.
        # Saltato se loop_scene attivo (hotkey OBS: "questa scena_A sta
        # funzionando, non portarmela via" - il ciclo audio-reattivo interno
        # kick->B/wave_kick/colore prosegue normalmente, resta congelato
        # solo IL CAMBIO di scena_A).
        # ====================================================================
        couple_good_moment = self.current_state == State.BREAK
        couple_ceiling_hit = couple_elapsed >= self.COUPLE_DURATION_MAX
        couple_due = couple_elapsed >= self.COUPLE_DURATION_MIN and couple_good_moment
        if (couple_ceiling_hit or couple_due) and not self.loop_scene:
            # Il timer META_COUPLE_DURATION vale SOLO dopo il giro iniziale
            # (vedi _select_new_couple/startup_tour_bag) - durante il giro,
            # il passaggio alla prima coppia fissa e' gia' gestito li'
            # (a copertura completa, non a tempo).
            if self.startup_tour_done:
                meta_elapsed = current_time - self.meta_couple_start_time
                meta_ceiling_hit = meta_elapsed >= META_COUPLE_DURATION_MAX
                meta_due = meta_elapsed >= META_COUPLE_DURATION_MIN and couple_good_moment
                if meta_ceiling_hit or meta_due:
                    self._select_new_meta_pair()
                    self.meta_couple_start_time = current_time
            tour_was_active = not self.startup_tour_done
            self.current_couple_a = self._select_new_couple()
            if tour_was_active and self.startup_tour_done:
                # Il giro e' appena finito dentro _select_new_couple() (che
                # ha gia' chiamato _select_new_meta_pair() per la prima
                # coppia fissa) - la finestra dei 15-25min di QUELLA coppia
                # parte da ora, non dall'avvio di pupa.py.
                self.meta_couple_start_time = current_time
            self.current_b_scene = self._roll_next_b_scene()
            self._roll_new_identity()
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

            timer_label = "tetto max" if couple_ceiling_hit and not couple_good_moment else "su break"
            log_decision(
                from_scene=current_scene,
                to_scene=self.current_couple_a,
                reason=f"TIMER coppia {timer_label} ({couple_elapsed:.0f}s) | State: {self.current_state.value}",
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

        couple_pct = (couple_elapsed / self.COUPLE_DURATION_MAX) * 100

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
                # Floor alzato 0.2->0.4 (operatore: "_kick appare all'inizio
                # di un cambio e poi basta") - decadeva troppo verso un
                # minimo basso, restava presente solo nei primissimi secondi.
                prob_wave = max(0.4, 1.0 - couple_elapsed / self._intro_window())
            elif self.current_state == State.BREAK:
                # Floor alzato 0.3->0.5 e finestra di decadimento allungata
                # 20s->40s (operatore: "_wave solo all'accenno di un break")
                # - stesso problema, decadeva troppo in fretta verso un
                # minimo basso.
                time_in_break = current_time - self.state_start_time
                prob_wave = max(0.5, 1.0 - time_in_break / 40.0)
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
                                      transition_choice="Taglio", interval=self._get_strobe_interval())
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
    """Variante wave_kick dell'identita' assegnata alla coppia corrente
    (vedi IDENTITY_SETS in scenes_config.yaml), o None se non definita/non
    disponibile in OBS -
    pupa.py ricade sulla selezione random generica in quel caso."""
    return model._get_identity().get("wave_kick")

def get_identity_waveform():
    """Scena waveform_color dell'identita' assegnata alla coppia corrente
    (vedi IDENTITY_SETS in scenes_config.yaml) - sostituisce ago_talk come
    scena alternativa a wave_kick (vedi WAVE_KICK_ALT_SCENES in pupa.py),
    deve comparire assieme al colore/kick della STESSA identita'. None se
    non definita/non disponibile in OBS."""
    return model._get_identity().get("waveform")

def get_identity_color_name():
    """Nome della scena _color (es. 'red_color') dell'identita'
    assegnata alla coppia corrente (vedi IDENTITY_SETS in scenes_config.yaml)
    - usato da pupa.py per pilotare l'overlay 'color_overlay' (2026-07-16,
    nidificato in scene_A/_B/kick) cosi' il tint segue sempre lo stesso
    colore del flash/waveform della stessa identita'. None se non definita."""
    return model._get_identity().get("color")

def get_black_pause_breath_phase(current_time):
    """Fase 0.0-1.0 della PAUSA NERA corrente (vedi overlap_is_black_pause
    in _maybe_trigger_overlap), o None se non ce n'e' una attiva ORA - usato
    da pupa.py per sintetizzare un respiro in-out-in-out sull'INTERA durata
    della pausa (non agganciato a battuta come il respiro continuo di
    black_overlay), "le pause nere sono sempre lunghe ed e' li' che ci
    vorrebbe il respiro"."""
    m = model
    if not (m.overlap_active and m.overlap_is_black_pause):
        return None
    total = m.overlap_hold_until - m.overlap_start_time
    if total <= 0:
        return None
    elapsed = current_time - m.overlap_start_time
    return max(0.0, min(1.0, elapsed / total))

def get_and_clear_pre_drop_flash():
    """Consuma (one-shot) il flag di flash nero pre-drop (vedi RUNUP_* e
    _detect_runup in HybridCouplesModel) - True al massimo una volta per
    risalita rilevata, poi torna False finche' non ne scatta una nuova.
    Usato da pupa.py per pilotare un picco temporaneo di 'black_overlay'."""
    pending = model.pre_drop_flash_pending
    model.pre_drop_flash_pending = False
    return pending

def is_strobe_burst_active():
    """True mentre una raffica strobo/lampo/cut e' in corso (vedi
    _trigger_strobe/_advance_burst) - usato da pupa.py per sincronizzare il
    canale Strobe dei fari fisici via qlc_controller (2026-07-24)."""
    return model.burst_active


def is_strobe_frame_on():
    """True SOLO durante il frame 'acceso' (scena/colore alternato, vedi
    _advance_burst: burst_step pari) di una raffica in corso - a differenza
    di is_strobe_burst_active() (True per l'intera raffica, ON+OFF), questa
    distingue i singoli frame per pilotare il Master del fixture fisico a
    tempo reale invece del canale Strobe autonomo (2026-07-29, Step 1 del
    piano luci - vedi wiggly-moseying-blum.md). False se nessuna raffica e'
    attiva o durante il frame 'spento'."""
    return model.burst_active and (model.burst_step % 2 == 0)


def get_strobe_burst_color():
    """Nome della scena colore (in STROBE_COLOR_POOL - white/red/blue/green)
    del burst strobo/lampo attivo ORA, o None se non c'e' nessun burst
    attivo o se e' un CUT burst (alterna scene di contenuto reali, non
    colori - vedi il controllo 'is_cut_burst' dentro _advance_burst).
    Usato da pupa.py per pilotare l'RGB dei fari fisici in sync col Master
    (Step 1) durante una vera raffica strobo/lampo - lo Step 1 gestiva solo
    l'on/off di luminosita', non il COLORE del flash (es. bianco), che
    restava quello dell'identita' corrente invece di quello scelto da
    _pick_strobe_color() - trovato dal vivo 2026-07-30 ("mancano le strobo
    bianche")."""
    if not model.burst_active:
        return None
    if model.burst_alt_scene not in STROBE_COLOR_POOL:
        return None
    return model.burst_alt_scene

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

def get_ambient_light(current_time):
    """Intensita' (0.0-1.0) del wash ambient per gli stati di quiete
    (INTRO/BREAK/RELAX) - None se lo stato corrente non e' di quiete, vedi
    HybridCouplesModel.get_ambient_light. Usato da pupa.py per sostituire il
    pulso a kick sui fari fisici durante questi stati (2026-07-29, Step 2)."""
    return model.get_ambient_light(current_time)

def get_light_outputs(current_time, screen_blackness_pct=0.0, wave_scene_showing=False):
    """Quale/i dei 2 fari fisici mostrare 'in vista' ora - vedi
    HybridCouplesModel.get_light_outputs. Chiamato da pupa.py ad ogni tick
    per attenuare il fixture non 'in vista' (2026-07-29, Step 3)."""
    return model.get_light_outputs(current_time, screen_blackness_pct, wave_scene_showing)


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
    global COUPLES, COUPLE_TRANSITIONS, DEGENERATE_MODE, STROBE_COLOR_POOL, BLACK_PAUSE_PROBABILITY, IDENTITY_SETS, ALL_B_SCENES, META_PAIR_DUOS

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
    ALL_B_SCENES = _compute_all_b_scenes()  # ricalcolato sul COUPLES appena filtrato, vedi commento sopra la sua definizione

    # META_PAIR_DUOS: toglie dai duo le scene_A rimosse sopra (non in
    # COUPLES) - un duo con 1 sola scena_A superstite resta cosi' com'e'
    # (caso degenere gia' gestito da _select_new_couple, nessun crash), un
    # duo svuotato del tutto viene tolto dalla lista.
    filtered_duos = []
    for duo in META_PAIR_DUOS:
        available_duo = [a for a in duo if a in COUPLES]
        if len(available_duo) < len(duo):
            debug_log(f"[VALIDATE] meta_pair_duo {duo} ridotto a {available_duo}")
        if available_duo:
            filtered_duos.append(available_duo)
    META_PAIR_DUOS = filtered_duos

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
    # Ultima rete di sicurezza: se non sopravvive NESSUN colore (nemmeno
    # white_color), ricadi su black_color - "flash colorati e strobo bianchi
    # sostituiti dal nero se non presenti in OBS" (scelta esplicita
    # dell'operatore, non solo un default tecnico). Se anche black_color
    # manca, STROBE_COLOR_POOL resta vuoto: nessuna scena su cui flashare,
    # gestito a valle come le altre liste vuote.
    STROBE_COLOR_POOL = available_colors or ([BLACK_PAUSE_SCENE] if BLACK_PAUSE_SCENE in available_scenes_set else [])

    if BLACK_PAUSE_SCENE not in available_scenes_set:
        debug_log(f"[VALIDATE] {BLACK_PAUSE_SCENE} non trovata, pausa nera disattivata")
        BLACK_PAUSE_PROBABILITY = 0.0

    # IDENTITA' (bundle indipendenti dalla scena_A, vedi IDENTITY_SETS): per
    # ciascun bundle toglie i singoli campi (transition/color/wave_kick/
    # waveform) non ancora presenti in OBS - _get_identity() ricade sui
    # meccanismi generici pre-esistenti quando un campo manca, invece di
    # tentare uno switch a vuoto verso una transizione/scena inesistente.
    filtered_sets = []
    for i, entry in enumerate(IDENTITY_SETS):
        fixed_entry = dict(entry)
        if fixed_entry.get("transition") not in available_transitions:
            debug_log(f"[VALIDATE] identity_sets[{i}].transition '{fixed_entry.get('transition')}' "
                      f"non disponibile, tolta (fallback generico)")
            fixed_entry.pop("transition", None)
        if fixed_entry.get("color") not in available_scenes_set:
            debug_log(f"[VALIDATE] identity_sets[{i}].color '{fixed_entry.get('color')}' "
                      f"non disponibile, tolta (fallback generico)")
            fixed_entry.pop("color", None)
        if fixed_entry.get("wave_kick") not in available_scenes_set:
            debug_log(f"[VALIDATE] identity_sets[{i}].wave_kick '{fixed_entry.get('wave_kick')}' "
                      f"non disponibile, tolta (fallback generico)")
            fixed_entry.pop("wave_kick", None)
        if fixed_entry.get("waveform") not in available_scenes_set:
            debug_log(f"[VALIDATE] identity_sets[{i}].waveform '{fixed_entry.get('waveform')}' "
                      f"non disponibile, tolta (fallback generico)")
            fixed_entry.pop("waveform", None)
        filtered_sets.append(fixed_entry)
    IDENTITY_SETS = filtered_sets

    return {"couples": COUPLES, "couple_transitions": COUPLE_TRANSITIONS, "degenerate": DEGENERATE_MODE,
            "strobe_color_pool": STROBE_COLOR_POOL, "black_pause_enabled": BLACK_PAUSE_PROBABILITY > 0}
