"""
PUPA - VJ Brain Production
Hybrid Couples Model: 4min timer + Music Reactive A↔B
"""

import time
import sys
import os
import random
import math
import sounddevice as sd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from obs_controller import OBSController
from qlc_controller import QLCController
from audio_analyzer import AudioAnalyzer
import brain
import scene_discovery
from logger import setup_logger
from debug_logger import debug as debug_log
from runtime_monitor import RuntimeMonitor
from window_manager import get_window_manager
from hotkey_controller import MultiLevelControl, BinaryControl

try:
    from secrets_local import OBS_HOST, OBS_PORT, OBS_PASSWORD, AUDIO_DEVICE_NAME
except ImportError:
    print("[ERROR] secrets_local.py mancante o incompleto. Copia secrets_local.example.py")
    print("        in secrets_local.py e inserisci le credenziali OBS e il nome del")
    print("        device audio di questa macchina (vedi list_audio_devices.py).")
    sys.exit(1)

# PULSE_SOURCE: opzionale, solo Linux/PipeWire - pinna la sorgente pulse a
# un device specifico invece del default di sistema (che puo' cambiare e
# rompere silenziosamente la cattura). Non serve su Windows.
try:
    from secrets_local import PULSE_SOURCE
except ImportError:
    PULSE_SOURCE = None

if PULSE_SOURCE:
    os.environ.setdefault("PULSE_SOURCE", PULSE_SOURCE)

# AUDIO_INPUT_GAIN_PCT: opzionale, solo Linux/PipeWire - gain di cattura (%)
# impostato ESPLICITAMENTE ad ogni avvio invece di fidarsi di un settaggio
# di sistema che puo' non persistere (o essere alterato da altri). Scoperto
# dal vivo: un gain lasciato a 130% (sopra l'unita') su un segnale gia' di
# livello linea produceva clipping pesante (picco 2.6 su 1.0), che a valle
# schiacciava bass/mid/high sempre al tetto indipendentemente dal brano.
try:
    from secrets_local import AUDIO_INPUT_GAIN_PCT
except ImportError:
    AUDIO_INPUT_GAIN_PCT = None

# KICK_THRESHOLD_BASS_MIN/DELTA: opzionali, per-macchina - vedi il commento
# su questi stessi campi in AudioAnalyzer.__init__. Scoperto dal vivo
# 2026-07-22 che il valore condiviso (60) non e' adatto a un ponte audio con
# dinamica di segnale piu' bassa (Linux, ricevente da Windows) - nessun
# override qui, resta il default di AudioAnalyzer.
try:
    from secrets_local import KICK_THRESHOLD_BASS_MIN
except ImportError:
    KICK_THRESHOLD_BASS_MIN = None
try:
    from secrets_local import KICK_THRESHOLD_BASS_DELTA
except ImportError:
    KICK_THRESHOLD_BASS_DELTA = None

# ALTERNANZA 2 USCITE MONITOR: opzionale, solo sul rig Linux con le 2 uscite
# show fisiche (vedi brain.get_monitor_outputs). monitorIndex e' quello
# ritornato da OBS get_monitor_list() - va verificato via WebSocket, non
# indovinato. Assente su Windows (secrets_local.py li non li definisce).
try:
    from secrets_local import MONITOR_SHOW1_INDEX, MONITOR_SHOW2_INDEX, MONITOR_BLACK_SCENE
except ImportError:
    MONITOR_SHOW1_INDEX = None
    MONITOR_SHOW2_INDEX = None
    MONITOR_BLACK_SCENE = None


# STACKING (2026-07-14, riscrittura dopo diversi crash live nello stesso
# giorno con l'approccio precedente): aprire/chiudere un proiettore nuovo ad
# ogni flip faceva ripetutamente fallire wmctrl sotto carico sostenuto
# (timeout 3s), lasciando finestre orfane che si accumulavano fino a far
# collassare OBS (vedi memoria "video_and_monitor_alternation_todo" per la
# cronologia completa dei tentativi precedenti - pausa-respiro e circuit
# breaker con pulizia, entrambi rimossi qui perche' non piu' necessari).
#
# Sostituito con l'apertura di 2 proiettori sovrapposti per uscita
# (Programma + black_master, stessa posizione fisica) UNA VOLTA SOLA
# all'avvio (vedi window_manager.py:open_stacked_pair) - l'alternanza vera e
# propria e' solo "porta in primo piano quella giusta" (window_manager.py:
# activate su un ID gia' noto), nessuna apertura/chiusura durante la
# sessione. Verificato con test_stacking.py sotto carico reale: 2249 flip in
# 900s, 0 falliti, latenza media 28.7ms - inoltre, siccome le finestre non
# vengono mai chiuse, anche nel caso peggiore (window manager che smette di
# rispondere del tutto) i monitor restano fermi sull'ultimo stato mostrato
# invece di andare senza segnale, a differenza del vecchio "cleanup" che
# poteva lasciarli neri.
#
# 2026-07-22: la logica di apertura/attivazione delle finestre e' stata
# spostata in window_manager.py (get_window_manager(), astrazione Linux/
# Windows) - qui restano solo le costanti di comportamento (quando mettere
# in pausa l'alternanza dopo troppi fallimenti), che non dipendono dalla
# piattaforma.
MONITOR_ACTIVATE_FAIL_THRESHOLD = 5  # fallimenti consecutivi di activate() prima di mettere in pausa
MONITOR_ACTIVATE_COOLDOWN = 30.0  # secondi di pausa (le finestre restano comunque aperte/ferme) prima di riprovare


def _set_capture_gain(pulse_source, gain_pct):
    """Imposta il gain di cattura via pactl (PipeWire/PulseAudio). Non
    fatale se fallisce (es. 'pactl' assente su Windows): logga solo un
    warning, pupa continua con qualunque gain sia gia' impostato."""
    import subprocess
    try:
        subprocess.run(
            ["pactl", "set-source-volume", pulse_source, f"{gain_pct}%"],
            check=True, capture_output=True, timeout=5
        )
        print(f"[AUDIO] Gain di cattura impostato a {gain_pct}% su '{pulse_source}'")
    except FileNotFoundError:
        print("[AUDIO] WARN: comando 'pactl' non trovato, gain di cattura NON impostato automaticamente")
    except Exception as e:
        print(f"[AUDIO] WARN: impossibile impostare il gain di cattura: {e}")

CONFIG = {
    "obs_host": OBS_HOST,
    "obs_port": OBS_PORT,
    "obs_password": OBS_PASSWORD,
    "audio_device_name": AUDIO_DEVICE_NAME,
}


def _resolve_audio_device(name):
    """L'indice numerico PortAudio di un device NON e' garantito stabile tra
    riavvii (osservato su Linux/PipeWire spostarsi 22 -> 19 -> 22 -> 18 nella
    stessa sessione, a seconda di quali sorgenti risultano attive al
    momento) - risolverlo per nome ad ogni avvio invece di hardcodare un
    indice fisso in config."""
    for i, d in enumerate(sd.query_devices()):
        if d["name"] == name and d["max_input_channels"] > 0:
            return i
    raise RuntimeError(
        f"Device audio '{name}' non trovato. Rilancia list_audio_devices.py "
        f"e aggiorna AUDIO_DEVICE_NAME in secrets_local.py."
    )


TRANSITION_MS = 2500

# Varianti di wave_kick: ogni ingresso ne sceglie una a caso (mai la stessa
# due volte di fila, vedi anti-repeat sotto) invece di mostrare sempre la
# stessa immagine - "un po' troppo ripetitivo". SCOPERTE per convenzione di
# denominazione (desinenza "_kick", vedi scene_discovery.py) invece di una
# lista letterale fissa - qualunque numero di varianti, chiamate come si
# vuole, funziona finche' finiscono per "_kick".

# ago_talk (2026-07-06, sperimentale: cattura finestra del terminale che
# esegue pupa.py stesso) sostituito il 2026-07-15 dalla serie waveform_color
# (waveform_red/blue/yellow/green) - non piu' una lista fissa: la scena
# giusta e' quella dell'IDENTITA' correntemente assegnata alla coppia (vedi
# brain.get_identity_waveform), cosi' il colore del "waveform" mostrato
# combacia sempre con quello di color_master/kick della stessa identita'
# invece di una scelta scollegata.
WAVE_KICK_ALT_PROBABILITY = 0.5  # 2026-07-15: alzata da 0.3 - misurata dal vivo vicina al target (~28%) ma percepita come rara (spesso "assorbita" da uno strobe burst subito dopo)

# SLIDESHOW A TEMPO (2026-07-16, generalizzato 2026-07-?? - vedi
# scene_discovery.py): una slide non e' una scena con un nome speciale, e'
# una scena_A/_B QUALSIASI che contiene una sorgente di kind 'slideshow'
# (riconoscimento per CONTENUTO, non per nome - scelta esplicita
# dell'operatore: le slide sono video intercambiabili come gli altri).
# Se una scena ha piu' sorgenti slideshow nidificate, avanza sui kick solo
# la PRIMA (stesso comportamento di prima - avanzamento multiplo non ancora
# deciso).

# SLIDESHOW A BATTUTA (2026-07-22): prima avanzava ad OGNI kick - a batteria
# fitta un cambio quasi ad ogni colpo, slegato dalla struttura musicale. Ora
# avanza sul beat (is_beat/beat_count, la stessa griglia gia' usata per il
# respiro nero e l'alternanza monitor) con una cadenza che scala con
# l'energia - un bar intero (4 beat) negli stati calmi, meta' bar in GROOVE/
# BUILD, ogni singolo beat in DROP/PEAK. Stessa filosofia gia' usata per
# cut-burst/monitor alternation (piu' energia = piu' veloce), non un nuovo
# principio.
SLIDESHOW_ADVANCE_BEATS = {
    brain.State.INTRO:  4, brain.State.BREAK: 4, brain.State.RELAX: 4,
    brain.State.GROOVE: 2, brain.State.BUILD: 2,
    brain.State.DROP:   1, brain.State.PEAK:  1,
}
# Transizione slideshow: tipo fisso (quello gia' configurato in OBS, es.
# "slide"), solo la DURATA scala con l'energia - stessa filosofia di
# _get_fade_duration_ms() in brain.py (musica che spinge = transizioni
# corte/veloci, musica calma = transizioni lunghe).
# Range accorciato 2026-07-23 (era 350-900ms) - operatore: "accorcia le
# soglie max/min 600ms/300ms".
SLIDESHOW_TRANSITION_SPEED_MS = {
    brain.State.INTRO:  600, brain.State.BREAK: 600, brain.State.RELAX: 550,
    brain.State.GROOVE: 450, brain.State.BUILD: 400,
    brain.State.DROP:   300, brain.State.PEAK:  300,
}

# OVERLAY COLORE (2026-07-16): sorgente condivisa 'color_overlay' nidificata
# in ogni scena_A/_B/kick (creata via script una tantum su OBS, non da
# pupa.py), che tinge del colore dell'identita' corrente per rinforzare la
# percezione visiva (vedi brain.get_identity_color_name()). RGB inizialmente
# letti dalle sorgenti colore reali (Colore/Colore N dentro
# red_master/blue_master/yellow_master/green_master) - 2026-07-17: verde e
# giallo poi ritoccati SOLO per l'overlay (verde piu' chiaro, giallo piu'
# scuro - "si vede poco" dal vivo), quindi non piu' garantiti identici al
# colore del flash/waveform della stessa identita' come all'inizio.
#
# 2026-07-17: passato da "sempre acceso fisso" a PULSANTE SUL KICK - "si puo'
# fare che si accende a tempo di musica?". COLOR_OVERLAY_PEAK_PCT e' il picco
# raggiunto sul frame del kick (default, sovrascrivibile per colore - vedi
# COLOR_OVERLAY_PEAK_PCT_OVERRIDES, giallo "si vede poco" anche a parita' di
# tono), poi decade linearmente a 0 in COLOR_OVERLAY_DECAY_S secondi
# (dissolvenza, non spegnimento istantaneo) - tra un kick e l'altro resta a
# 0, mai un livello base residuo.
COLOR_OVERLAY_SOURCE = "color_overlay"
COLOR_OVERLAY_PEAK_PCT = 20  # 2026-07-17: era il livello fisso (post fix da 75), ora e' il picco del polso
COLOR_OVERLAY_PEAK_PCT_OVERRIDES = {}  # 2026-07-29: era solo per yellow_color (eliminato) - RGB puri non hanno bisogno di override
COLOR_OVERLAY_DECAY_S = 0.15
# "si puo' fare che random si disattiva?" - ad ogni cambio identita'
# (rotazione coppia), invece di pulsare SEMPRE sul kick, con questa
# probabilita' il pulsare resta spento per l'intera durata di quella coppia
# (stessa cadenza della rotazione identita', non un timer indipendente).
COLOR_OVERLAY_OFF_PROBABILITY = 0.35
# Colori tarati A MANO (scostati dal colore REALE della sorgente OBS per
# motivi di leggibilita' - vedi note sotto). Qualunque scena _color scoperta
# che NON compare qui viene letta dal vivo da OBS (obs.get_scene_color(),
# vedi resolve_identity_overlay_rgb() in main()) invece di richiedere una
# voce hardcoded per ogni nuovo colore - "il file/il codice non deve avere
# nomi di scena fissi".
IDENTITY_OVERLAY_RGB = {
    # 2026-07-29: RGB puri (niente magenta/tinte miste) - giallo eliminato
    # dalla rotazione, resta solo rosso/verde/blu. Bianco riservato allo
    # strobo (vedi STROBE_COLOR_POOL/STROBE_COLOR_WEIGHTS), non e' un colore
    # identita' che ruota.
    "red_color":    (255, 0, 0),
    "blue_color":   (0, 0, 255),
    "green_color":  (0, 255, 0),
}

# SYNC LUCI QLC+ (2026-07-24): mirroring del pulso color_overlay/strobo sui
# fari fisici via qlc_controller.py (OS2L, vedi PUPA_DEVELOPMENT_LOG.md per
# il percorso pieno di insidie per arrivarci). Questi sono gli id OS2L "cmd"
# (NON i numeri "Canale" mostrati nell'interfaccia di QLC+ - c'e' un offset
# di 1, Canale N dell'interfaccia = id N-1, verificato empiricamente).
#
# 2026-07-29 (Step 0 del piano luci): i 2 fari sono ora indirizzabili
# separatamente in QLC+ (pupa.qxw ricostruito con una Pagina 1 unica, 10
# slider invece dei 4 condivisi originali - vedi wiggly-moseying-blum.md).
# Finche' l'alternanza (Step 3) non e' costruita, PUPA pilota ENTRAMBI i
# fari identicamente (stesso comportamento di prima, solo su id diversi).
# Master e' ora raggiungibile via OS2L per la prima volta (prima fisso a
# 255 via Vista DMX, mai toccato da PUPA) - va impostato esplicitamente
# all'avvio o i fari restano scuri di default (i nuovi slider Master
# partono da Value=0).
QLC_CHANNEL_F1_MASTER = 13
QLC_CHANNEL_F1_R = 10
QLC_CHANNEL_F1_G = 11
QLC_CHANNEL_F1_B = 12
QLC_CHANNEL_F1_STROBE = 14
QLC_CHANNEL_F2_MASTER = 18
QLC_CHANNEL_F2_R = 15
QLC_CHANNEL_F2_G = 16
QLC_CHANNEL_F2_B = 17
QLC_CHANNEL_F2_STROBE = 19
# QLC_CHANNEL_F1_STROBE/F2_STROBE (sopra) non sono piu' pilotati da PUPA dallo
# Step 1 in poi - il canale Strobe autonomo del fixture non era mai a tempo,
# sostituito dal pilotaggio diretto di Master per-frame (vedi sotto). Gli id
# restano documentati/assegnati in QLC+ per eventuale uso manuale.
ALL_LIGHT_CHANNELS = (QLC_CHANNEL_F1_MASTER, QLC_CHANNEL_F1_R, QLC_CHANNEL_F1_G, QLC_CHANNEL_F1_B, QLC_CHANNEL_F1_STROBE,
                      QLC_CHANNEL_F2_MASTER, QLC_CHANNEL_F2_R, QLC_CHANNEL_F2_G, QLC_CHANNEL_F2_B, QLC_CHANNEL_F2_STROBE)
QLC_AMBIENT_SEND_INTERVAL_S = 0.1  # throttle del wash ambient (Step 2) - 10Hz basta per un respiro di AMBIENT_BREATH_PERIOD_S secondi
QLC_LIGHT_ATTENUATED_SCALE = 0.0  # 2026-07-30: era 0.15 (pensato per "alternate" - il fixture non "in vista" attenuato invece di spento del tutto). Con "inverse" attiva ora, un 15% su colori saturi restava visibile ("perche' sono spesso accese entrambe le luci con un solo monitor acceso" - trovato dal vivo, zero {True,True} nei log del gate quindi la logica era corretta, il problema era qui) - lo spec dell'operatore per "inverse" dice esplicitamente "spento", non "attenuato". Rimettere a 0.15 se si torna ad "alternate".
_qlc_last_logical_rgb = [(0, 0, 0)]  # ultimo RGB "logico" richiesto (pre-gate) - per ri-applicare subito quando il gate cambia (vedi loop principale), non solo al prossimo kick/tick ambient


_qlc_last_combined_pct = [0.0]  # ultimo nero-schermo (combined_pct) conosciuto - aggiornato nel loop principale DOPO che e' calcolato; usato qui perche' questa funzione viene chiamata anche PRIMA di quel calcolo nello stesso tick (kick pulse, off-roll) - un valore di un tick fa (~50ms) e' trascurabile
_qlc_last_wave_scene_showing = [False]  # come sopra, ma per l'enfasi colore_wave (2026-07-30)


def _qlc_set_rgb_both(qlc, r, g, b, current_time):
    """Manda RGB ai 2 fari, scalati dal gate di alternanza corrente
    (brain.get_light_outputs()): il fixture 'in vista' riceve il colore
    pieno, l'altro un'attenuazione invece di uno spegnimento secco.
    Registra anche il colore richiesto (pre-gate) in _qlc_last_logical_rgb -
    il loop principale lo riusa per ri-applicare immediatamente il gate
    quando cambia, senza aspettare il prossimo kick/tick ambient (altrimenti
    con la modalita' 'inverse', legata ai cambi rapidi dei monitor, le luci
    sembrano 'in ritardo' - trovato dal vivo 2026-07-29)."""
    _qlc_last_logical_rgb[0] = (r, g, b)
    gate = brain.get_light_outputs(current_time, screen_blackness_pct=_qlc_last_combined_pct[0],
                                    wave_scene_showing=_qlc_last_wave_scene_showing[0])
    scale_1 = 1.0 if gate["fixture1"] else QLC_LIGHT_ATTENUATED_SCALE
    scale_2 = 1.0 if gate["fixture2"] else QLC_LIGHT_ATTENUATED_SCALE
    qlc.set_channel(QLC_CHANNEL_F1_R, int(r * scale_1))
    qlc.set_channel(QLC_CHANNEL_F1_G, int(g * scale_1))
    qlc.set_channel(QLC_CHANNEL_F1_B, int(b * scale_1))
    qlc.set_channel(QLC_CHANNEL_F2_R, int(r * scale_2))
    qlc.set_channel(QLC_CHANNEL_F2_G, int(g * scale_2))
    qlc.set_channel(QLC_CHANNEL_F2_B, int(b * scale_2))

# OVERLAY NERO (2026-07-17): "stessa logica del colore ma piu' lenta" -
# stessa sorgente condivisa nidificata (black_overlay, sopra color_overlay
# nello z-order - vedi script di creazione), ma agganciata a BATTUTA
# (ogni brain.BEATS_PER_BAR beat, stesso segnale gia' usato per l'alternanza
# monitor) invece che al kick, con una dissolvenza piu' lunga. A differenza
# del colore, non dipende dall'identita' - sempre nero, sempre attivo, nessun
# roll di spegnimento per coppia.
BLACK_OVERLAY_SOURCE = "black_overlay"
BLACK_OVERLAY_PEAK_PCT = 25
BLACK_OVERLAY_DECAY_S = 1.0

# FLASH NERO PRE-DROP (2026-07-17, V2 - vedi brain.RUNUP_*/_detect_runup):
# picco piu' marcato e piu' lungo del respiro a battuta sopra - un accento
# occasionale, non un ciclo continuo. Stessa sorgente condivisa: ogni frame
# si manda il MASSIMO tra il respiro a battuta e questo flash (mai la somma,
# altrimenti supererebbero 100% se coincidono), cosi' non si accavallano
# ne' si spengono a vicenda.
PRE_DROP_FLASH_PEAK_PCT = 85
PRE_DROP_FLASH_DECAY_S = 0.6

# RESPIRO PAUSA NERA (2026-07-17): "le pause sono sempre lunghe ed e' li'
# che ci vorrebbe il respiro: in-out-in-out" - il respiro a BATTUTA sopra
# scatta al massimo 1 volta durante una pausa breve (1.5-4s), troppo poco
# per leggersi come un vero respiro. Qui invece si usa la fase 0-1 esposta
# da brain.get_black_pause_breath_phase() per sintetizzare un'onda coseno
# che copre l'INTERA durata della pausa, non agganciata a battuta - inizia
# e finisce vicino al minimo (mai a 0 secco, cosi' non sparisce mai del
# tutto), con BLACK_PAUSE_BREATH_CYCLES cicli completi in-out.
BLACK_PAUSE_BREATH_CYCLES = 2
BLACK_PAUSE_BREATH_MIN_PCT = 10
BLACK_PAUSE_BREATH_MAX_PCT = 35

# CALM MODE: 4 hotkey OBS (Show/Hide di 4 source dedicate, una per livello),
# per generi a bassa energia (dub techno, minimal, intro lunghe) dove PUPA
# non puo' riconoscere il genere da solo - vedi CALM_MULTIPLIERS in brain.py.
# Le 4 source vivono in una scena di servizio mai mostrata sul programma
# (CALM_CONTROL_SCENE) - se la scena/source non esistono ancora, il polling
# si disattiva da solo (nessun crash, nessun calm mode finche' non le crei).
CALM_CONTROL_SCENE = "PUPA_Control"
CALM_LEVEL_SOURCES = {0: "PUPA_CALM_0", 1: "PUPA_CALM_1", 2: "PUPA_CALM_2", 3: "PUPA_CALM_3"}
CALM_LEVEL_TEXT_SOURCE = "CALM_LEVEL_TEXT"  # indicatore a video, stessa scena, mai in onda
CALM_POLL_EVERY_N_TICKS = 10  # ~0.5s a 20Hz - un hotkey premuto a mano non serve reattivita' audio-frame

# LOOP SCENA: hotkey OBS Mostra/Nascondi (binario, non 4 livelli come calm
# mode) per congelare il timer 4min sulla scena_A corrente ("questa sta
# funzionando, non portarmela via") - vedi brain.set_loop_scene. Stessa
# scena di servizio di calm mode, stesso schema di risoluzione/polling.
LOOP_SCENE_SOURCE = "PUPA_LOOP_SCENE"

# BLACKOUT: hotkey OBS Mostra/Nascondi (binario) per portare monitor e luci
# a nero SENZA fermare PUPA (2026-07-30, operatore - "serve poter spegnere
# monitor/luci comandabile da hotkey senza arrestarsi sempre", per pause
# tecniche/annunci al microfono senza perdere timer/stato interno). Mostra
# = OBS forzato su BLACK_PAUSE_SCENE + tutti i canali QLC+ a 0, bypassando
# la logica normale; Nascondi = ripristina, la prossima tick ricalcola tutto
# da zero (gate/colore/scena) come se il blackout non ci fosse mai stato.
# Stessa scena di servizio, stesso schema di risoluzione/polling.
BLACKOUT_SOURCE = "PUPA_BLACKOUT"

# MODALITA' LUCI: hotkey OBS "Mostra"-only, 3 livelli esclusivi (stesso
# schema di CALM_LEVEL_SOURCES: vince la source appena mostrata, autopulizia
# delle altre) - seleziona brain.model.light_mode a runtime (2026-07-30).
LIGHT_MODE_SOURCES = {0: "PUPA_LIGHTMODE_SYNC", 1: "PUPA_LIGHTMODE_ALTERNATE", 2: "PUPA_LIGHTMODE_INVERSE"}
LIGHT_MODE_NAMES = {0: "sync", 1: "alternate", 2: "inverse"}

# OVERRIDE MANUALE MONITOR/LUCI: 2 hotkey binari indipendenti (F9/F10),
# ciascuno un toggle persistente come BLACKOUT - non un 3-way esclusivo,
# per scelta esplicita dell'operatore. Se entrambi risultassero attivi
# insieme (non dovrebbe succedere in uso normale), SOLO_MONITOR vince per
# precedenza fissa nel codice, vedi il blocco di dispatch sotto.
SOLO_MONITOR_SOURCE = "PUPA_SOLO_MONITOR"  # F9: monitor SEMPRE accesi, luci spente
SOLO_LUCI_SOURCE = "PUPA_SOLO_LUCI"  # F10: luci SEMPRE accese, monitor spenti

# STROBO BIANCO MANUALE: hotkey F8, "scarica" una raffica bianca subito,
# bypassando STROBE_BURST_PROBABILITY - one-shot che si riarma da solo
# (vedi hotkey_controller.BinaryControl.force), quindi basta legare "Mostra"
# in OBS, nessun "Nascondi" necessario.
STROBE_WHITE_SOURCE = "PUPA_STROBE_WHITE"

# ============================================================================
# SCALE-TO-SOUND — DISATTIVATO DI NUOVO (2026-07-06)
# ============================================================================
# Ri-testato oggi con audio finalmente pulito (VB-Cable, niente piu' clipping/
# mic debole) per verificare se i due bug storici fossero in realta' causati
# dal segnale audio scadente di allora. Risultato: NO, sono ricomparsi
# IDENTICI anche con audio corretto:
#   1. Posizione che scivola in basso-sx invece di restare centrata
#      (nonostante il ricalcolo del centro in set_source_scale())
#   2. Invisibile/fermo in Program, funziona solo in Preview - il mistero
#      originale, ora confermato NON dipendere dall'audio
# Conclusione: e' un comportamento di basso livello di OBS (Program/Preview
# sembrano avere cache di rendering separate per lo stesso scene item), non
# risolvibile con chiamate WebSocket generiche come il nostro toggle
# disable->enable. Non vale lo sforzo di continuare a rincorrerlo - si e'
# passati a testare il filtro Shadertastic nativo sulla sorgente invece.
#
# Codice tenuto per riferimento, non cancellato.
#
# SCALE_TO_SOUND_TARGETS = {
#     "wave_kick": ["Immagine 2"],
# }
# SCALE_MIN_SIZE = 0.0     # 0% -> invisibile sotto soglia
# SCALE_MAX_SIZE = 1.0     # 100% -> dimensione originale al tetto
# SCALE_AUDIO_THRESHOLD_DB = -34.0
# SCALE_AUDIO_CEILING_DB = -19.0
# SCALE_SMOOTHING = 0.9
#
#
# def _db_to_scale(db_level):
#     """Mappa un livello audio in dBFS a uno scale factor, replicando la
#     logica soglia/tetto del vecchio plugin Scale to Sound."""
#     if db_level <= SCALE_AUDIO_THRESHOLD_DB:
#         return SCALE_MIN_SIZE
#     if db_level >= SCALE_AUDIO_CEILING_DB:
#         return SCALE_MAX_SIZE
#     frac = (db_level - SCALE_AUDIO_THRESHOLD_DB) / (SCALE_AUDIO_CEILING_DB - SCALE_AUDIO_THRESHOLD_DB)
#     return SCALE_MIN_SIZE + frac * (SCALE_MAX_SIZE - SCALE_MIN_SIZE)

def main():
    print("=" * 70)
    print("  PUPA VJ BRAIN - Production")
    print("  4min Couples + Music Reactive")
    print("=" * 70)
    
    logger = setup_logger("pupa.log")
    
    obs = OBSController(
        host=CONFIG["obs_host"],
        port=CONFIG["obs_port"],
        password=CONFIG["obs_password"]
    )
    
    if not obs.connect():
        print("[ERROR] OBS connessione fallita.")
        return

    print(f"[OBS] Connesso! OBS v{obs.version}")

    # SYNC LUCI QLC+: opzionale, non blocca l'avvio se QLC+ non e' raggiungibile
    # (qlc.connect() gestisce l'eccezione internamente, sock resta None e
    # qlc.set_channel()/trigger_button() diventano no-op silenziosi finche' non
    # si riconnette da sola - vedi QLCController._ensure_connected()).
    qlc = QLCController()
    qlc.connect()
    if qlc.sock is not None:
        print("[QLC] Connesso (OS2L)")
        # Master ora sotto controllo OS2L (era fisso a 255 via Vista DMX,
        # mai toccato da PUPA) - va portato a piena intensita' esplicitamente
        # all'avvio, altrimenti i nuovi slider Master (default Value=0)
        # lascerebbero i fari scuri. Placeholder fino allo Step 1 (strobo
        # a tempo reale via Master) - vedi wiggly-moseying-blum.md.
        qlc.set_channel(QLC_CHANNEL_F1_MASTER, 255)
        qlc.set_channel(QLC_CHANNEL_F2_MASTER, 255)
    else:
        print("[QLC] Non raggiungibile - sync luci disattivato per questa sessione")
    
    scenes = obs.cache_scenes()
    print(f"[PUPA] Mappate {len(scenes)} scene hardware stabili.")

    # SCOPERTA PER CONVENZIONE (2026-07-24, vedi scene_discovery.py): riempie
    # le sezioni mancanti di scenes_config.yaml (couples/strobe_color_pool/
    # identity_sets) leggendo la convenzione di denominazione OBS (_A/_B/
    # _kick/_color/_wave) invece di richiedere nomi hardcoded - "PUPA deve
    # leggere cosa c'e' in OBS e importarlo nel suo funzionamento". Le slide
    # sono riconosciute per CONTENUTO (sorgente di kind 'slideshow'), non per
    # nome - una scena_A/_B qualsiasi puo' essere una slide.
    all_inputs = obs.get_all_inputs()
    candidate_scenes = scene_discovery.discover_a_scenes(scenes) + scene_discovery.discover_b_scenes(scenes)
    scene_item_names = {s: obs.get_scene_item_source_names(s) for s in candidate_scenes}
    slide_scenes = brain.discover_and_merge_config(scenes, all_inputs, scene_item_names)
    slide_input_names = scene_discovery.slideshow_input_names(all_inputs)
    slideshow_sources = {
        s: [n for n in scene_item_names.get(s, []) if n in slide_input_names]
        for s in slide_scenes
    }
    print(f"[PUPA] Slide riconosciute per contenuto: {slide_scenes}")

    # Set delle scene _wave (2026-07-30, enfasi luci): la scena Program e'
    # condivisa da entrambe le uscite monitor, quindi basta sapere SE quella
    # corrente e' una _wave, non "quale lato" - vedi brain.get_light_outputs.
    wave_scenes_set = set(scene_discovery.discover_wave_scenes(scenes))

    # Valida coppie/transizioni (ora comprensive di quanto scoperto sopra)
    # contro quello che esiste DAVVERO in questa installazione OBS - scene_B/
    # coppie/transizioni mancanti vengono tolte/sostituite invece di far
    # crashare o bloccare tutto. Se resta una sola scena, PUPA lampeggia su
    # quella invece di alternare A/B (vedi brain.validate_scenes).
    transitions = obs.get_transition_list()
    validation = brain.validate_scenes(scenes, transitions)
    print(f"[PUPA] Validazione: {len(validation['couples'])} coppie valide"
          f"{' | MODALITA DEGENERATA (1 sola scena)' if validation['degenerate'] else ''}")

    # COLORE IDENTITA': IDENTITY_OVERLAY_RGB vince se un colore e' gia'
    # tarato a mano (vedi commento sopra); per ogni scena _color scoperta
    # ma MAI tarata, legge il colore REALE dalla sorgente OBS invece di
    # richiedere una voce hardcoded - cosi' un colore nuovo aggiunto in OBS
    # (es. "purple_color") funziona a costo zero di codice.
    color_source_names_set = scene_discovery.color_source_names(all_inputs)
    identity_overlay_rgb = dict(IDENTITY_OVERLAY_RGB)
    for color_scene in scene_discovery.discover_color_scenes(scenes):
        if color_scene in identity_overlay_rgb:
            continue
        items = obs.get_scene_item_source_names(color_scene)
        own_source = scene_discovery.find_own_color_source(items, color_source_names_set)
        if own_source:
            rgb = obs.get_scene_color(own_source)
            if rgb:
                identity_overlay_rgb[color_scene] = rgb
                debug_log(f"[DISCOVERY] colore letto dal vivo per {color_scene}: {rgb}")

    # Varianti wave_kick: scoperte per desinenza "_kick" (vedi sopra),
    # fallback alla singola "wave_kick" se nessuna esiste ancora.
    available_wave_kick_variants = scene_discovery.discover_kick_scenes(scenes)
    if not available_wave_kick_variants:
        available_wave_kick_variants = ["wave_kick"]
    print(f"[PUPA] Varianti wave_kick disponibili: {available_wave_kick_variants}")
    last_wave_kick_variant = [None]  # lista per mutabilita' dentro il loop
    slideshow_last_speed = [None]  # ultima transition_speed INVIATA (non calcolata) - evita un set_input_settings a vuoto se lo stato non e' cambiato

    # OVERLAY COLORE: traccia l'ultimo colore gia' inviato (per il roll
    # on/off ad ogni cambio identita') e lo stato del polso corrente (per la
    # dissolvenza sul kick) - set_overlay_color ingoia da sola eventuali
    # errori se la sorgente non esiste ancora in questa installazione OBS.
    last_identity_color = [None]
    overlay_rgb = [None]           # colore attivo per l'identita' corrente, None se spento per questa coppia (roll off)
    identity_rgb_raw = [None]      # colore raw dell'identita', indipendente dal roll-off - usato dal wash ambient (Step 2)
    overlay_peak_pct = [COLOR_OVERLAY_PEAK_PCT]  # picco per QUESTO colore (vedi COLOR_OVERLAY_PEAK_PCT_OVERRIDES)
    overlay_pulse_end_time = [0.0]  # 0.0 = nessun polso in corso
    black_overlay_pulse_end_time = [0.0]  # 0.0 = nessun polso nero (respiro a battuta) in corso
    pre_drop_flash_end_time = [0.0]  # 0.0 = nessun flash pre-drop in corso
    black_overlay_last_sent = [None]  # ultima opacita' INVIATA (non calcolata) - evita set_overlay_color a vuoto ogni frame
    qlc_master_strobe_last = [255]  # ultimo valore Master INVIATO per lo strobo - manda solo sui fronti, non ogni frame (255=baseline gia' impostato alla connessione)
    qlc_ambient_last_send_time = [0.0]  # ultimo invio del wash ambient - throttle a tempo, vedi QLC_AMBIENT_SEND_INTERVAL_S
    qlc_ambient_active_last = [False]  # per loggare solo sui fronti ATTIVO/disattivato del wash ambient, non ad ogni tick
    qlc_light_gate_last = [{"fixture1": False, "fixture2": False}]  # ultimo gate alternanza APPLICATO - ri-applica subito il colore corrente quando cambia (non aspetta il prossimo kick/tick ambient)
    qlc_strobe_rgb_last = [(-1, -1, -1)]  # ultimo RGB raffica strobo INVIATO - sentinella invalida per forzare il primo invio, edge-detected sui cambi successivi

    # HOTKEY (2026-07-30, refactor): il meccanismo di polling/edge-detection
    # e' ora in hotkey_controller.py (MultiLevelControl/BinaryControl) - qui
    # restano solo la semantica (quali source, cosa fare quando cambiano) e
    # l'istanziazione. Vedi hotkey_controller.py per il "perche'" del design.
    calm_control = MultiLevelControl("Calm mode", CALM_CONTROL_SCENE, CALM_LEVEL_SOURCES)
    calm_control.resolve(obs, scenes)

    # Indicatore a video del livello (CALM_LEVEL_TEXT dentro PUPA_Control,
    # mai in onda - "verifica a video di quale stato sia attivo?", visibile
    # solo aprendo l'Anteprima di quella scena in OBS, non sul programma).
    calm_text_available = (
        CALM_CONTROL_SCENE in scenes
        and obs.get_source_item_id(CALM_CONTROL_SCENE, CALM_LEVEL_TEXT_SOURCE) is not None
    )
    if calm_text_available:
        obs.set_input_text(CALM_LEVEL_TEXT_SOURCE, "CALM: 0")

    loop_scene_control = BinaryControl("Loop scena", CALM_CONTROL_SCENE, LOOP_SCENE_SOURCE)
    loop_scene_control.resolve(obs, scenes)

    blackout_control = BinaryControl("Blackout", CALM_CONTROL_SCENE, BLACKOUT_SOURCE)
    blackout_control.resolve(obs, scenes)
    blackout_active = [False]  # stato corrente, letto anche fuori dal blocco di poll (gate luci/monitor)

    light_mode_control = MultiLevelControl("Modalita luci", CALM_CONTROL_SCENE, LIGHT_MODE_SOURCES)
    light_mode_control.resolve(obs, scenes)

    solo_monitor_control = BinaryControl("Solo monitor", CALM_CONTROL_SCENE, SOLO_MONITOR_SOURCE)
    solo_monitor_control.resolve(obs, scenes)
    solo_luci_control = BinaryControl("Solo luci", CALM_CONTROL_SCENE, SOLO_LUCI_SOURCE)
    solo_luci_control.resolve(obs, scenes)
    solo_monitor_active = [False]
    solo_luci_active = [False]

    strobe_white_control = BinaryControl("Strobo bianco manuale", CALM_CONTROL_SCENE, STROBE_WHITE_SOURCE)
    strobe_white_control.resolve(obs, scenes)

    # ALTERNANZA 2 USCITE MONITOR: attiva solo se configurata in
    # secrets_local.py. window_manager.get_window_manager() sceglie
    # l'implementazione giusta per la piattaforma (Linux: wmctrl/xprop,
    # Windows: pywin32 - vedi window_manager.py per l'architettura a
    # stacking, comune a entrambe).
    #
    # monitor_feature_available: la macchina ha la configurazione giusta,
    # la piattaforma e' supportata, E le 4 finestre (2 per uscita) si sono
    # aperte correttamente all'avvio - fisso per tutta la sessione.
    # monitor_alternation_enabled: se l'alternanza sta girando ADESSO - puo'
    # passare a False (troppi fallimenti di activate()) e tornare True da
    # sola dopo MONITOR_ACTIVATE_COOLDOWN, vedi nel loop principale.
    monitor_feature_available = MONITOR_SHOW1_INDEX is not None and MONITOR_SHOW2_INDEX is not None
    monitor_show1_on_id = monitor_show1_off_id = None
    monitor_show2_on_id = monitor_show2_off_id = None
    window_manager = None
    if monitor_feature_available:
        try:
            window_manager = get_window_manager()
        except Exception as e:
            print(f"[PUPA] Alternanza monitor: {e}, disattivata")
            monitor_feature_available = False

    if monitor_feature_available:
        # Posizione X reale dei monitor - hint opzionale per l'implementazione
        # Linux (identifica le finestre proiettore per posizione fisica, non
        # per timing), ignorato da quella Windows (non ne ha bisogno, vedi
        # window_manager.py). Se non si trova l'indice, disattiva
        # l'alternanza invece di rischiare comportamenti indefiniti.
        monitor_positions = {m.get("monitorIndex"): m.get("monitorPositionX") for m in obs.get_monitor_list()}
        monitor_show1_x = monitor_positions.get(MONITOR_SHOW1_INDEX)
        monitor_show2_x = monitor_positions.get(MONITOR_SHOW2_INDEX)
        if monitor_show1_x is None or monitor_show2_x is None:
            print(f"[PUPA] Alternanza monitor: indici {MONITOR_SHOW1_INDEX}/{MONITOR_SHOW2_INDEX} non trovati in get_monitor_list(), disattivata")
            monitor_feature_available = False
        elif monitor_show1_x == monitor_show2_x:
            # I 2 monitor risultano alla stessa posizione X: probabilmente
            # clonati/sovrapposti anziche' in modalita estesa (es. cavi
            # riconnessi senza rifare il layout xrandr) - window_manager.py
            # (Linux) identifica le finestre per posizione, quindi con la
            # stessa X non potrebbe distinguerle. Vedi monitor_align.py per
            # diagnosticare e correggere il layout manualmente.
            print(f"[PUPA] Alternanza monitor: monitor {MONITOR_SHOW1_INDEX} e {MONITOR_SHOW2_INDEX} "
                  f"hanno la stessa posizione X ({monitor_show1_x}) - probabilmente non estesi, "
                  f"disattivata. Esegui monitor_align.py per verificare/correggere il layout.")
            monitor_feature_available = False
        else:
            print(f"[PUPA] Alternanza monitor: apro le 4 finestre sovrapposte "
                  f"(show1=monitor {MONITOR_SHOW1_INDEX} x={monitor_show1_x}, show2=monitor {MONITOR_SHOW2_INDEX} x={monitor_show2_x})...")
            monitor_show1_on_id, monitor_show1_off_id = window_manager.open_stacked_pair(
                obs, MONITOR_SHOW1_INDEX, MONITOR_BLACK_SCENE, position_key=monitor_show1_x
            )
            monitor_show2_on_id, monitor_show2_off_id = window_manager.open_stacked_pair(
                obs, MONITOR_SHOW2_INDEX, MONITOR_BLACK_SCENE, position_key=monitor_show2_x
            )
            if None in (monitor_show1_on_id, monitor_show1_off_id, monitor_show2_on_id, monitor_show2_off_id):
                print("[PUPA] Alternanza monitor: apertura iniziale delle finestre fallita, disattivata")
                monitor_feature_available = False
            else:
                print(f"[PUPA] Alternanza monitor: attiva (show1 on={monitor_show1_on_id} off={monitor_show1_off_id}, "
                      f"show2 on={monitor_show2_on_id} off={monitor_show2_off_id})")
    monitor_alternation_enabled = monitor_feature_available
    monitor_show1_state = None
    monitor_show2_state = None
    monitor_fail_count = 0
    monitor_pause_until = 0.0

    # Risolvi gli scene_item_id delle sorgenti da scalare a ritmo di musica,
    # e la loro dimensione base (per le sorgenti con "bounds" fisso, es.
    # OBS_BOUNDS_SCALE_INNER: scaleX/scaleY vengono ignorati da OBS in quel
    # caso, serve invece ridimensionare boundsWidth/boundsHeight).
    #
    # Per boundsWidth/boundsHeight/posizione NON ci fidiamo di una lettura
    # live (potrebbero essere gia' rimpiccioliti da un avvio precedente non
    # terminato pulitamente): usiamo la risoluzione del canvas OBS come
    # riferimento "100%" affidabile, assumendo pos=(0,0) — coerente con la
    # configurazione osservata per queste sorgenti (copertura intero canvas).
    #
    # SCALE-TO-SOUND DISATTIVATO (vedi commento in cima al file) — inizializzazione
    # commentata di conseguenza.
    # canvas_w, canvas_h = obs.get_canvas_size()
    # print(f"[SCALE] Canvas OBS: {canvas_w}x{canvas_h}")
    #
    # scale_targets = {}
    # scale_bounds = {}
    # for scene_name, source_names in SCALE_TO_SOUND_TARGETS.items():
    #     ids = []
    #     for source_name in source_names:
    #         item_id = obs.get_source_item_id(scene_name, source_name)
    #         if item_id is not None:
    #             ids.append(item_id)
    #             bounds_type = obs.get_source_base_size(scene_name, item_id)["bounds_type"]
    #             base = {
    #                 "bounds_type": bounds_type,
    #                 "bounds_width": float(canvas_w),
    #                 "bounds_height": float(canvas_h),
    #                 "position_x": 0.0,
    #                 "position_y": 0.0,
    #             }
    #             scale_bounds[(scene_name, item_id)] = base
    #             print(f"[SCALE] {scene_name}/{source_name}: boundsType={bounds_type}")
    #         else:
    #             print(f"[SCALE] WARN: sorgente '{source_name}' non trovata in '{scene_name}'")
    #     scale_targets[scene_name] = ids
    #     print(f"[SCALE] {scene_name}: {len(ids)}/{len(source_names)} sorgenti mappate per scale-to-sound")
    #
    # smoothed_scale_by_scene = {scene_name: SCALE_MIN_SIZE for scene_name in scale_targets}
    # scale_tick_counter_by_scene = {scene_name: 0 for scene_name in scale_targets}
    # SCALE_PUSH_EVERY_N_TICKS = {
    #     "wave_kick": 3,
    # }

    if PULSE_SOURCE and AUDIO_INPUT_GAIN_PCT is not None:
        _set_capture_gain(PULSE_SOURCE, AUDIO_INPUT_GAIN_PCT)

    audio_device = _resolve_audio_device(CONFIG["audio_device_name"])
    print(f"[AUDIO] Device '{CONFIG['audio_device_name']}' risolto a index {audio_device}")

    audio = AudioAnalyzer(
        device=audio_device,
        kick_threshold_bass_min=KICK_THRESHOLD_BASS_MIN,
        kick_threshold_bass_delta=KICK_THRESHOLD_BASS_DELTA,
    )
    try:
        audio.start()
    except Exception as e:
        print(f"[ERROR] AUDIO: Avvio fallito: {e}")
        import traceback
        traceback.print_exc()
        return

    print(f"[AUDIO] Device {audio_device} avviato")

    # PREFLIGHT: un paio di secondi per lasciar assestare l'AGC/lo stream,
    # poi un controllo esplicito di picco - clipping o silenzio vanno
    # segnalati SUBITO, prima di iniziare il set, non scoperti a posteriori.
    time.sleep(2)
    preflight = audio.get_metrics()
    preflight_peak = preflight.get("peak", 0.0)
    if preflight.get("clipping"):
        print(f"[AUDIO] ALERT: segnale in CLIPPING gia' in preflight (picco={preflight_peak:.2f}) - abbassa il gain prima di iniziare")
    elif preflight_peak < AudioAnalyzer.SILENCE_PEAK_THRESHOLD:
        print(f"[AUDIO] ALERT: nessun segnale rilevato in preflight (picco={preflight_peak:.3f}) - verifica device/cavo/sorgente")
    else:
        print(f"[AUDIO] Preflight OK: picco={preflight_peak:.2f} (0.98+ = clipping)")

    current_scene = obs.get_current_scene()
    brain.initialize_model(current_scene, time.time())

    # Scena_A di partenza randomizzata (vedi HybridCouplesModel.initialize) -
    # forza lo switch reale in OBS subito, altrimenti lo schermo resterebbe
    # sulla scena su cui OBS era gia' fermo (spesso la stessa tra un test e
    # l'altro, "vedo sempre urbanfree_A") finche' non arriva il primo switch
    # organico. Nessun cambio se per puro caso e' gia' la stessa.
    starting_couple_a = brain.get_current_couple_a()
    if starting_couple_a != current_scene:
        obs.switch_scene(starting_couple_a, transition_ms=800, transition_type="Fade")
        current_scene = starting_couple_a
    print(f"[BRAIN] Inizializzato su scena: {current_scene}")

    # MONITORAGGIO STABILITA' RUNTIME (2026-07-21): GetStats di OBS +
    # latenza del loop di PUPA, incrociati con l'audio/stato corrente ad
    # ogni alert - vedi runtime_monitor.py. Prima solo uno script Linux a
    # parte (resource_monitor.py) raccoglieva GetStats, e solo durante un
    # test lanciato a mano: qui gira SEMPRE, su entrambe le macchine.
    runtime_monitor = RuntimeMonitor(obs)

    running = True
    calm_poll_tick = 0
    last_tick_time = None  # per la latenza del loop sotto - misura diretta di eventuali rallentamenti del ciclo di PUPA stesso

    try:
        while running:
            current_time = time.time()

            # LOOP LATENCY: misura diretta, non dedotta, di quanto rallenta
            # il ciclo di PUPA stesso - atteso ~50ms (time.sleep(0.05)).
            # Calcolato qui (prima di qualunque altro lavoro nel ciclo), ma
            # valutato/allertato piu' sotto da runtime_monitor.tick(), che lo
            # incrocia con audio/stato corrente (vedi runtime_monitor.py).
            tick_gap = (current_time - last_tick_time) if last_tick_time is not None else None
            last_tick_time = current_time

            # HOTKEY: polling leggero (non ad ogni frame, vedi
            # CALM_POLL_EVERY_N_TICKS) - il meccanismo (edge-detection,
            # "vince la source appena accesa", autopulizia) e' in
            # hotkey_controller.py, qui resta solo la dispatch semantica.
            if (calm_control.active or loop_scene_control.active or blackout_control.active
                    or light_mode_control.active or solo_monitor_control.active
                    or solo_luci_control.active or strobe_white_control.active):
                calm_poll_tick += 1
                if calm_poll_tick >= CALM_POLL_EVERY_N_TICKS:
                    calm_poll_tick = 0

                    new_calm_level = calm_control.poll(obs)
                    if new_calm_level is not None:
                        brain.set_calm_level(new_calm_level)
                        print(f"[CALM MODE] livello -> {new_calm_level}")
                        debug_log(f"[CALM MODE] livello -> {new_calm_level}")
                        if calm_text_available:
                            obs.set_input_text(CALM_LEVEL_TEXT_SOURCE, f"CALM: {new_calm_level}")

                    new_loop_state = loop_scene_control.poll(obs)
                    if new_loop_state is not None:
                        brain.set_loop_scene(new_loop_state, current_time)
                        print(f"[LOOP SCENA] {'attivo' if new_loop_state else 'disattivato'}")
                        debug_log(f"[LOOP SCENA] {'attivo' if new_loop_state else 'disattivato'}")

                    # BLACKOUT: attivazione/disattivazione one-shot (vedi
                    # BLACKOUT_SOURCE sopra) - il vero "congelamento" del
                    # resto del loop e' il guard "not blackout_active[0]" su
                    # "if audio_data" poco sotto, non qui.
                    new_blackout_state = blackout_control.poll(obs)
                    if new_blackout_state is not None:
                        blackout_active[0] = new_blackout_state
                        if new_blackout_state:
                            obs.switch_scene(brain.BLACK_PAUSE_SCENE, transition_ms=50, transition_type="Taglio")
                            if qlc.sock is None:
                                qlc.connect()
                            if qlc.sock is not None:
                                for ch in ALL_LIGHT_CHANNELS:
                                    qlc.set_channel(ch, 0)
                            print("[BLACKOUT] attivo - monitor e luci a nero, PUPA resta in ascolto")
                            debug_log("[BLACKOUT] attivo")
                        else:
                            print("[BLACKOUT] disattivato - ripristino normale dal prossimo tick")
                            debug_log("[BLACKOUT] disattivato")

                    # MODALITA' LUCI (F5/F6/F7): stesso schema esclusivo di CALM MODE.
                    new_light_mode_level = light_mode_control.poll(obs)
                    if new_light_mode_level is not None:
                        mode_name = LIGHT_MODE_NAMES.get(new_light_mode_level, "inverse")
                        brain.set_light_mode(mode_name)
                        print(f"[MODALITA LUCI] -> {mode_name}")
                        debug_log(f"[MODALITA LUCI] -> {mode_name}")

                    # SOLO MONITOR / SOLO LUCI (F9/F10): 2 toggle indipendenti,
                    # ricomposti in un unico brain.forced_mode con SOLO_MONITOR
                    # a vincere per precedenza fissa se risultassero attivi
                    # entrambi insieme (non dovrebbe succedere in uso normale).
                    forced_mode_changed = False
                    new_solo_monitor = solo_monitor_control.poll(obs)
                    if new_solo_monitor is not None:
                        solo_monitor_active[0] = new_solo_monitor
                        forced_mode_changed = True
                    new_solo_luci = solo_luci_control.poll(obs)
                    if new_solo_luci is not None:
                        solo_luci_active[0] = new_solo_luci
                        forced_mode_changed = True
                    if forced_mode_changed:
                        if solo_monitor_active[0]:
                            forced_mode = "solo_monitor"
                        elif solo_luci_active[0]:
                            forced_mode = "solo_luci"
                        else:
                            forced_mode = None
                        brain.set_forced_mode(forced_mode)
                        print(f"[OVERRIDE MANUALE] -> {forced_mode}")
                        debug_log(f"[OVERRIDE MANUALE] -> {forced_mode}")

                    # STROBO BIANCO MANUALE (F8): one-shot, si riarma da solo
                    # (force(False) subito dopo) - niente effetto durante il
                    # blackout, dato che decide_next_scene() non gira in quel
                    # momento (vedi guard "not blackout_active[0]" sotto) e la
                    # raffica resterebbe innescata ma congelata a meta'.
                    new_strobe_trigger = strobe_white_control.poll(obs)
                    if new_strobe_trigger and not blackout_active[0]:
                        brain.trigger_white_strobe(current_scene)
                        strobe_white_control.force(obs, False)
                        print("[STROBO MANUALE] raffica bianca innescata")
                        debug_log("[STROBO MANUALE] raffica bianca innescata")

            audio_data = audio.get_metrics()

            # BLACKOUT: mentre attivo, tutto il resto del tick (transizioni,
            # QLC+, monitor-seq, slideshow...) e' sospeso - lo stato interno
            # di brain.py (timer coppie, energy tracking) resta congelato
            # esattamente dov'era, riprende identico alla disattivazione,
            # nessuna logica di "resume" dedicata necessaria.
            if audio_data and not blackout_active[0]:
                bass = audio_data.get("bass", 0)
                mid = audio_data.get("mid", 0)
                hi = audio_data.get("hi", 0)
                is_kick = audio_data.get("is_kick", False)
                is_drop = audio_data.get("is_drop", False)
                db_level = audio_data.get("db_level", -60.0)
                clipping = audio_data.get("clipping", False)
                bpm = audio_data.get("bpm", 0.0)

                bass_bar = "#" * int(bass / 5)
                mid_bar = "#" * int(mid / 5)
                hi_bar = "#" * int(hi / 5)

                event_label = ""
                if is_kick:
                    event_label = " | KICK"
                elif is_drop:
                    event_label = " | DROP"
                if clipping:
                    event_label += " | CLIP!"

                bpm_label = f" | BPM:{bpm:5.1f}" if bpm > 0 else ""
                calm_label = f" | CALM:{brain.get_calm_level()}" if brain.get_calm_level() > 0 else ""
                print(f"[AUDIO] B: [{bass_bar:<20}] M: [{mid_bar:<20}] H: [{hi_bar:<20}] dB:{db_level:6.1f}{event_label}{bpm_label}{calm_label}")

                # DECIDI SUBITO (ogni frame, senza delay)
                current_scene = obs.get_current_scene()

                # MONITORAGGIO STABILITA' RUNTIME: si autolimita internamente
                # (poll costoso a OBS solo ogni 2s, vedi RuntimeMonitor) - va
                # bene chiamarlo ad ogni frame.
                runtime_monitor.tick(current_time, tick_gap, audio_data, brain.model.current_state.name, current_scene)

                # SLIDESHOW A BATTUTA: quando la scena in onda e' stata
                # riconosciuta come slide per CONTENUTO (vedi slide_scenes/
                # slideshow_sources sopra, non un nome fisso), avanza sul
                # beat (non piu' su ogni kick) con cadenza e durata
                # transizione che scalano con l'energia corrente - vedi
                # SLIDESHOW_ADVANCE_BEATS/SLIDESHOW_TRANSITION_SPEED_MS.
                slideshow_source = (slideshow_sources.get(current_scene) or [None])[0]
                if slideshow_source:
                    is_beat = audio_data.get("is_beat", False)
                    beat_count = audio_data.get("beat_count", 0)
                    current_state = brain.model.current_state
                    advance_beats = SLIDESHOW_ADVANCE_BEATS.get(current_state, 4)
                    if is_beat and beat_count % advance_beats == 0:
                        target_speed = SLIDESHOW_TRANSITION_SPEED_MS.get(current_state, 700)
                        if target_speed != slideshow_last_speed[0]:
                            try:
                                obs.client.set_input_settings(slideshow_source, {"transition_speed": target_speed}, overlay=True)
                                slideshow_last_speed[0] = target_speed
                            except Exception as e:
                                debug_log(f"[SLIDESHOW] set transition_speed fallito: {e}")
                        try:
                            obs.client.trigger_hotkey_by_name("SlideShow.NextSlide", contextName=slideshow_source)
                        except Exception as e:
                            debug_log(f"[SLIDESHOW] NextSlide fallito: {e}")

                # SWITCH SCENE MANUALE: un hotkey OBS nativo ("Passa a
                # [scena_A]") ha gia' cambiato la scena mostrata - qui
                # rileviamo lo scarto tra quello che OBS mostra DAVVERO e
                # quello che brain.py crede attivo, e risincronizziamo invece
                # di lasciare che PUPA "torni indietro" da solo al prossimo
                # kick. Solo per vere scene_A (mai per _B/wave_kick/colori,
                # che PUPA gestisce gia' come parte del proprio ciclo interno).
                if (current_scene.endswith("_A") and current_scene in brain.COUPLES
                        and current_scene != brain.get_current_couple_a()):
                    brain.force_couple(current_scene, current_time)
                    print(f"[SWITCH SCENE] forzato manualmente -> {current_scene} (timer coppia riavviato)")
                    debug_log(f"[SWITCH SCENE] forzato manualmente -> {current_scene}")

                # SCALE-TO-SOUND: DISATTIVATO (vedi commento in cima al file).
                # if current_scene in scale_targets and scale_targets[current_scene]:
                #     target_scale = _db_to_scale(db_level)
                #     prev = smoothed_scale_by_scene[current_scene]
                #     smoothed = prev + (target_scale - prev) * SCALE_SMOOTHING
                #     smoothed_scale_by_scene[current_scene] = smoothed
                #
                #     scale_tick_counter_by_scene[current_scene] += 1
                #     push_every = SCALE_PUSH_EVERY_N_TICKS.get(current_scene, 1)
                #     if scale_tick_counter_by_scene[current_scene] % push_every == 0:
                #         for item_id in scale_targets[current_scene]:
                #             base = scale_bounds.get((current_scene, item_id))
                #             obs.set_source_scale(current_scene, item_id, smoothed, base_bounds=base)
                #         debug_log(f"[SCALE] {current_scene}: dB={db_level:.1f} target={target_scale:.2f} smoothed={smoothed:.2f}")

                next_scene = brain.decide_next_scene(
                    audio_data=audio_data,
                    current_time=current_time,
                    current_scene=current_scene,
                    logger=logger
                )

                # SYNC STROBO QLC+ (2026-07-29, Step 1 del piano luci):
                # pilota Master direttamente per-frame invece del canale
                # Strobe autonomo (mai stato a tempo - vedi
                # wiggly-moseying-blum.md) - Master ON (255) sul frame
                # "acceso" della raffica, OFF (0) sul frame alternato,
                # torna al baseline (255, fisso) fuori da ogni raffica cosi'
                # il fixture resta illuminato normalmente. Manda solo sui
                # fronti (cambio di valore), non ad ogni tick.
                if brain.is_strobe_burst_active():
                    target_master = 255 if brain.is_strobe_frame_on() else 0
                else:
                    target_master = 255
                if target_master != qlc_master_strobe_last[0]:
                    qlc.set_channel(QLC_CHANNEL_F1_MASTER, target_master)
                    qlc.set_channel(QLC_CHANNEL_F2_MASTER, target_master)
                    qlc_master_strobe_last[0] = target_master

                # OVERLAY COLORE: al cambio identita' (rotazione coppia),
                # decide il colore attivo e se il pulsare e' abilitato per
                # questa coppia (COLOR_OVERLAY_OFF_PROBABILITY). Il pulsare
                # vero e proprio avviene sotto, ad ogni kick.
                identity_color = brain.get_identity_color_name()
                if identity_color != last_identity_color[0]:
                    rgb = identity_overlay_rgb.get(identity_color)
                    # colore raw dell'identita', indipendente dal roll-off del
                    # polso a kick (COLOR_OVERLAY_OFF_PROBABILITY) - usato dal
                    # wash ambient sotto. Sono 2 decisioni creative separate:
                    # "questa identita' non lampeggia sui kick" non deve
                    # implicare "questa identita' non ha nessun colore mai",
                    # altrimenti negli stati di quiete le luci restano a 0 per
                    # l'intera durata della coppia (bug reale osservato dal
                    # vivo 2026-07-29 - vedi PUPA_DEVELOPMENT_LOG.md).
                    identity_rgb_raw[0] = rgb
                    if rgb and random.random() >= COLOR_OVERLAY_OFF_PROBABILITY:
                        overlay_rgb[0] = rgb
                        overlay_peak_pct[0] = COLOR_OVERLAY_PEAK_PCT_OVERRIDES.get(identity_color, COLOR_OVERLAY_PEAK_PCT)
                    else:
                        overlay_rgb[0] = None
                        obs.set_overlay_color(COLOR_OVERLAY_SOURCE, rgb or (0, 0, 0), 0)
                        _qlc_set_rgb_both(qlc, 0, 0, 0, current_time)
                        debug_log(f"[QLC] pulso disabilitato per identita' '{identity_color}' (roll off, luci a 0 fino al prossimo cambio identita')")
                    overlay_pulse_end_time[0] = 0.0
                    last_identity_color[0] = identity_color

                # POLSO SUL KICK (overlay OBS): accende al picco (per-colore,
                # vedi overlay_peak_pct) sul frame del kick, poi dissolvenza
                # lineare a 0 in COLOR_OVERLAY_DECAY_S secondi - tra un kick
                # e l'altro resta a 0 (vedi commento sopra le costanti).
                # Resta SEMPRE kick-reattivo, in ogni stato - solo il lato
                # luci fisiche (QLC+, sotto) cambia comportamento negli stati
                # di quiete (2026-07-29, Step 2 del piano luci).
                ambient_intensity = brain.get_ambient_light(current_time)
                ambient_now = ambient_intensity is not None
                if ambient_now != qlc_ambient_active_last[0]:
                    debug_log(f"[QLC] wash ambient {'ATTIVO' if ambient_now else 'disattivato'} (stato={brain.model.current_state.value})")
                    qlc_ambient_active_last[0] = ambient_now
                if overlay_rgb[0]:
                    if is_kick:
                        obs.set_overlay_color(COLOR_OVERLAY_SOURCE, overlay_rgb[0], overlay_peak_pct[0])
                        overlay_pulse_end_time[0] = current_time + COLOR_OVERLAY_DECAY_S
                        if ambient_intensity is None:
                            r, g, b = overlay_rgb[0]
                            scale = min(1.0, overlay_peak_pct[0] / 100.0)
                            _qlc_set_rgb_both(qlc, int(r * scale), int(g * scale), int(b * scale), current_time)
                    elif overlay_pulse_end_time[0] > 0:
                        remaining = overlay_pulse_end_time[0] - current_time
                        if remaining > 0:
                            frac = remaining / COLOR_OVERLAY_DECAY_S
                            obs.set_overlay_color(COLOR_OVERLAY_SOURCE, overlay_rgb[0], overlay_peak_pct[0] * frac)
                            if ambient_intensity is None:
                                r, g, b = overlay_rgb[0]
                                scale = min(1.0, overlay_peak_pct[0] * frac / 100.0)
                                _qlc_set_rgb_both(qlc, int(r * scale), int(g * scale), int(b * scale), current_time)
                        else:
                            obs.set_overlay_color(COLOR_OVERLAY_SOURCE, overlay_rgb[0], 0)
                            overlay_pulse_end_time[0] = 0.0
                            if ambient_intensity is None:
                                _qlc_set_rgb_both(qlc, 0, 0, 0, current_time)

                # WASH AMBIENT LUCI (QLC+, Step 2): stati di quiete
                # (INTRO/BREAK/RELAX) - SOSTITUISCE del tutto il polso a kick
                # sui fari fisici (conferma operatore: sostituzione, non
                # convivenza), indipendente da is_kick. Throttle a tempo
                # (non ad ogni tick) - basta per un respiro di 10s, evita di
                # inondare la connessione OS2L di invii quasi identici.
                if ambient_intensity is not None and identity_rgb_raw[0]:
                    if current_time - qlc_ambient_last_send_time[0] >= QLC_AMBIENT_SEND_INTERVAL_S:
                        r, g, b = identity_rgb_raw[0]
                        _qlc_set_rgb_both(qlc, int(r * ambient_intensity), int(g * ambient_intensity), int(b * ambient_intensity), current_time)
                        qlc_ambient_last_send_time[0] = current_time

                # COLORE RAFFICA STROBO (QLC+, 2026-07-30): lo Step 1 pilota
                # solo l'on/off (Master) - durante una VERA raffica strobo/
                # lampo (non un CUT burst, che alterna scene di contenuto,
                # non colori) l'RGB deve mostrare il colore scelto da
                # _pick_strobe_color() (es. bianco), non restare quello
                # dell'identita' corrente - "mancano le strobo bianche".
                # Messo DOPO polso-a-kick/ambient apposta: durante un burst
                # ha l'ultima parola e sovrascrive quello che quei blocchi
                # avessero gia' mandato nello stesso tick (un burst nasce
                # quasi sempre da un kick, che altrimenti vincerebbe).
                # Edge-detected sul valore effettivo (colore*on/off), non ad
                # ogni tick.
                strobe_burst_color = brain.get_strobe_burst_color()
                if strobe_burst_color is not None:
                    strobe_rgb = identity_overlay_rgb.get(strobe_burst_color, (0, 0, 0))
                    target_strobe_rgb = strobe_rgb if brain.is_strobe_frame_on() else (0, 0, 0)
                    if target_strobe_rgb != qlc_strobe_rgb_last[0]:
                        _qlc_set_rgb_both(qlc, *target_strobe_rgb, current_time)
                        qlc_strobe_rgb_last[0] = target_strobe_rgb
                        debug_log(f"[QLC] strobo colore -> {strobe_burst_color} rgb={target_strobe_rgb}")

                # OVERLAY NERO: due sorgenti di polso sulla STESSA source
                # condivisa - il respiro a BATTUTA (continuo, vedi
                # BLACK_OVERLAY_* sopra) e il flash pre-drop (occasionale,
                # PRE_DROP_FLASH_* sopra, innescato da brain.RUNUP_*). Ognuno
                # calcola la propria opacita' desiderata SENZA inviarla
                # subito; si invia una sola volta il MASSIMO dei due, cosi'
                # non si accavallano ne' si spengono a vicenda.
                beat_count = audio_data.get("beat_count", 0)
                is_beat = audio_data.get("is_beat", False)
                if is_beat and beat_count % brain.BEATS_PER_BAR == 0:
                    black_overlay_pulse_end_time[0] = current_time + BLACK_OVERLAY_DECAY_S
                bar_pct = 0.0
                if black_overlay_pulse_end_time[0] > 0:
                    remaining = black_overlay_pulse_end_time[0] - current_time
                    if remaining > 0:
                        bar_pct = BLACK_OVERLAY_PEAK_PCT * (remaining / BLACK_OVERLAY_DECAY_S)
                    else:
                        black_overlay_pulse_end_time[0] = 0.0

                if brain.get_and_clear_pre_drop_flash():
                    pre_drop_flash_end_time[0] = current_time + PRE_DROP_FLASH_DECAY_S
                    print("[PRE-DROP] flash nero innescato")
                flash_pct = 0.0
                if pre_drop_flash_end_time[0] > 0:
                    remaining = pre_drop_flash_end_time[0] - current_time
                    if remaining > 0:
                        flash_pct = PRE_DROP_FLASH_PEAK_PCT * (remaining / PRE_DROP_FLASH_DECAY_S)
                    else:
                        pre_drop_flash_end_time[0] = 0.0

                # RESPIRO PAUSA NERA: se attiva, SOSTITUISCE del tutto il
                # respiro a battuta/flash per la sua durata (non li' compone
                # via max, altrimenti un polso a battuta piu' alto del
                # respiro in quel momento vincerebbe e romperebbe la curva
                # in-out) - vedi BLACK_PAUSE_BREATH_* sopra e
                # brain.get_black_pause_breath_phase.
                breath_phase = brain.get_black_pause_breath_phase(current_time)
                if breath_phase is not None:
                    wave = 0.5 - 0.5 * math.cos(2 * math.pi * BLACK_PAUSE_BREATH_CYCLES * breath_phase)
                    combined_pct = BLACK_PAUSE_BREATH_MIN_PCT + (BLACK_PAUSE_BREATH_MAX_PCT - BLACK_PAUSE_BREATH_MIN_PCT) * wave
                else:
                    combined_pct = max(bar_pct, flash_pct)

                # Invia solo se c'e' davvero qualcosa da mostrare o se questo
                # e' l'ultimo frame di decadimento (serve il click a 0 finale)
                # - non un set_overlay_color a vuoto ad ogni frame quando
                # nulla e' attivo.
                if combined_pct > 0 or black_overlay_last_sent[0] != 0.0:
                    obs.set_overlay_color(BLACK_OVERLAY_SOURCE, (0, 0, 0), combined_pct)
                    black_overlay_last_sent[0] = combined_pct
                _qlc_last_combined_pct[0] = combined_pct

                # GATE ALTERNANZA LUCI: controllato ad OGNI tick (non solo su
                # kick/ambient) - se cambia, ri-applica SUBITO l'ultimo colore
                # "logico" richiesto invece di aspettare il prossimo evento
                # kick/ambient, altrimenti le luci restano ferme al valore
                # vecchio e sembrano "in ritardo" (trovato dal vivo 2026-07-29
                # testando 'inverse' - i monitor cambiavano molto piu' spesso
                # di quanto i kick/il respiro ambient aggiornassero le luci).
                # Spostato QUI (dopo combined_pct, non prima) perche' Step
                # 'inverse' ora usa anche il nero unificato dello schermo
                # (overlay a battito + flash pre-drop + pausa nera, non solo
                # la fase grezza del sequencer monitor - trovato dal vivo
                # 2026-07-30: "quando i 2 monitor fanno intermittenza sul
                # nero le luci dovrebbero seguire, invece rimangono spente").
                # ENFASI colore_wave (2026-07-30): se la scena Program
                # corrente e' una _wave, il lato "acceso" del sequencer
                # monitor riceve enfasi (luce accesa comunque) - vedi
                # _get_light_outputs_inverse(). Tracciato anche qui (non solo
                # passato alla chiamata sotto) perche' _qlc_set_rgb_both()
                # internamente richiama get_light_outputs() da altri punti
                # del tick (kick/off-roll) dove current_scene potrebbe non
                # essere ancora quella "fresca" di questo giro - stesso
                # motivo di _qlc_last_combined_pct sopra.
                _qlc_last_wave_scene_showing[0] = current_scene in wave_scenes_set

                light_gate_now = brain.get_light_outputs(current_time, screen_blackness_pct=combined_pct,
                                                          wave_scene_showing=_qlc_last_wave_scene_showing[0])
                if light_gate_now != qlc_light_gate_last[0]:
                    qlc_light_gate_last[0] = light_gate_now
                    r, g, b = _qlc_last_logical_rgb[0]
                    _qlc_set_rgb_both(qlc, r, g, b, current_time)
                    debug_log(f"[QLC] gate cambiato -> {light_gate_now} (combined_pct={combined_pct:.1f}, "
                              f"wave={_qlc_last_wave_scene_showing[0]}, ri-applicato subito, rgb logico={r,g,b})")

                # ALTERNANZA 2 USCITE MONITOR: porta in primo piano la
                # finestra gia' aperta giusta per ciascuna uscita (stacking,
                # vedi window_manager.py) - nessuna apertura/chiusura durante
                # la sessione.
                if monitor_alternation_enabled:
                    desired = brain.get_monitor_outputs(current_time)
                    attempted = False
                    ok = True
                    if desired["show1"] != monitor_show1_state:
                        monitor_show1_state = desired["show1"]
                        target = monitor_show1_on_id if monitor_show1_state else monitor_show1_off_id
                        attempted = True
                        ok = window_manager.activate(target) and ok
                    if desired["show2"] != monitor_show2_state:
                        monitor_show2_state = desired["show2"]
                        target = monitor_show2_on_id if monitor_show2_state else monitor_show2_off_id
                        attempted = True
                        ok = window_manager.activate(target) and ok

                    if attempted:
                        if ok:
                            monitor_fail_count = 0
                        else:
                            monitor_fail_count += 1
                            if monitor_fail_count >= MONITOR_ACTIVATE_FAIL_THRESHOLD:
                                # Le finestre restano comunque aperte e ferme
                                # sull'ultimo stato mostrato (nessuna verra'
                                # mai chiusa) - a differenza del vecchio
                                # approccio, qui non c'e' rischio di monitor
                                # senza segnale, solo di alternanza ferma.
                                monitor_alternation_enabled = False
                                monitor_pause_until = current_time + MONITOR_ACTIVATE_COOLDOWN
                                monitor_fail_count = 0
                                msg = (f"[MONITOR] {MONITOR_ACTIVATE_FAIL_THRESHOLD} fallimenti consecutivi - "
                                       f"alternanza in pausa {MONITOR_ACTIVATE_COOLDOWN:.0f}s")
                                print(msg)
                                debug_log(msg)

                elif monitor_feature_available and monitor_pause_until > 0 and current_time >= monitor_pause_until:
                    monitor_alternation_enabled = True
                    monitor_pause_until = 0.0
                    monitor_show1_state = None
                    monitor_show2_state = None
                    msg = "[MONITOR] pausa conclusa - alternanza RIATTIVATA"
                    print(msg)
                    debug_log(msg)

                # Switch (o lampeggio, in MODALITA' DEGENERATA) se necessario.
                # next_scene is not None (non "next_scene truthy and diverso
                # da current_scene"): in modalita' degenerata next_scene E'
                # current_scene di proposito (nessun vero switch, solo un
                # lampeggio), altrimenti questo blocco verrebbe saltato del tutto.
                if next_scene is not None:
                    trans_info = brain.get_transition_info()
                    trans_type = trans_info.get("type", "Burn")
                    trans_ms = int(trans_info.get("duration_ms", TRANSITION_MS))
                    is_return = trans_info.get("is_return", False)
                    kick_mode = trans_info.get("kick_mode", "")

                    if kick_mode == "flash_single":
                        print(f"[LAMPEGGIO] modalita' degenerata -> {current_scene}")
                        obs.flash_scene(current_scene)
                        continue

                    # Basato su next_scene (non su kick_mode=="wave"): molti
                    # ingressi in wave_kick arrivano anche via SOVRAPPOSIZIONE
                    # (kick_mode="overlap"), che kick_mode=="wave" da solo non
                    # intercettava - osservato dal vivo (solo 2/20 sostituiti
                    # invece del ~30% atteso).
                    if next_scene == "wave_kick":
                        # Variante kick dell'identita' assegnata alla coppia
                        # corrente (vedi IDENTITY_SETS in scenes_config.yaml,
                        # ristrutturazione 2026-07-15: identita' slegata dalla
                        # scena_A, ruota in modo indipendente) - stessa finche'
                        # l'identita' non cambia, per rinforzarla visivamente.
                        # Fallback alla scelta random anti-repeat se l'identita'
                        # non ha una variante assegnata (o non ancora
                        # disponibile in OBS - gia' filtrato da validate_scenes).
                        identity_variant = brain.get_identity_wave_kick_variant()
                        if identity_variant and identity_variant in available_wave_kick_variants:
                            next_scene = identity_variant
                        else:
                            choices = [v for v in available_wave_kick_variants if v != last_wave_kick_variant[0]] \
                                or available_wave_kick_variants
                            next_scene = random.choice(choices)
                        last_wave_kick_variant[0] = next_scene

                        # Ogni tanto, al posto della variante kick, la scena
                        # waveform_color della STESSA identita' (sostituisce
                        # ago_talk dal 2026-07-15 - vedi WAVE_KICK_ALT_PROBABILITY)
                        # - mai un colore scollegato da quello gia' scelto sopra.
                        identity_waveform = brain.get_identity_waveform()
                        if (identity_waveform and identity_waveform in scenes
                                and random.random() < WAVE_KICK_ALT_PROBABILITY):
                            next_scene = identity_waveform

                    if kick_mode == "wave":
                        print(f"[WAVE_KICK] entrata -> {trans_type} {trans_ms}ms ({next_scene})")
                    elif kick_mode == "crescendo":
                        print(f"[WAVE_KICK] ritorno a A -> {trans_type} {trans_ms}ms")
                    elif kick_mode == "strobe":
                        print(f"[STROBE] frame -> {trans_type} {trans_ms}ms")
                    elif kick_mode == "cutburst":
                        print(f"[CUT BURST] frame -> {trans_type} {trans_ms}ms")
                    elif kick_mode == "overlap":
                        print(f"[SOVRAPPOSIZIONE] -> {trans_type} {trans_ms}ms")
                    elif kick_mode == "couple_start":
                        print(f"[CAMBIO COPPIA] firma -> {trans_type} {trans_ms}ms ({next_scene})")

                    direction = "B->A" if is_return else "A->B"
                    obs.switch_scene(
                        next_scene,
                        transition_ms=trans_ms,
                        transition_type=trans_type
                    )

                    print(f"[SWITCH] {current_scene} -> {next_scene} | {direction} {trans_type} {trans_ms}ms")
            
            time.sleep(0.05)  # ~20 Hz
    
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C ricevuto")
    except Exception as e:
        print(f"[ERROR] {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("[PUPA] Arresto pulito.")

        def _shutdown_step(description, fn):
            """Ogni step di arresto e' protetto A SE' - un secondo Ctrl+C per
            l'impazienza durante la pulizia (es. mentre la chiamata di rete a
            OBS e' in corso) non deve bloccare gli step successivi.
            'except Exception' da solo NON basta: KeyboardInterrupt eredita
            da BaseException, non da Exception, quindi un nuovo Ctrl+C durante
            uno step sfuggirebbe e interromperebbe tutto il resto - trovato
            dal vivo 2026-07-30 (OBS non passava a nero: il log si fermava a
            meta' della chiamata di switch_scene, il resto del finally non
            veniva mai raggiunto)."""
            try:
                fn()
            except BaseException as e:
                print(f"[PUPA] Step di arresto '{description}' interrotto/fallito: {e}")

        def _spegni_luci():
            # Senza questo i fari restano accesi/a meta' polso con l'ultimo
            # valore inviato, dato che QLC+ non ha un default "torna a 0" da
            # solo. Riconnette attivamente se il socket e' caduto (non solo
            # "se gia' connesso") - trovato dal vivo 2026-07-29: un disconnect
            # transitorio proprio nel momento dello stop faceva saltare lo
            # spegnimento in silenzio.
            if qlc.sock is None:
                qlc.connect()
            if qlc.sock is not None:
                for ch in ALL_LIGHT_CHANNELS:
                    qlc.set_channel(ch, 0)
                print("[QLC] Fari spenti.")
            else:
                print("[QLC] Impossibile spegnere i fari (QLC+ non raggiungibile).")

        def _obs_a_nero():
            # Monitor a nero all'arresto (richiesta operatore: fermare PUPA
            # deve fermare anche i software collegati). transition_ms=50 (non
            # 1: OBS rifiuta SetCurrentSceneTransitionDuration sotto i 50ms,
            # errore codice 402 - trovato dal vivo 2026-07-30 leggendo
            # debug.log, non a intuito) - resta comunque quasi istantaneo.
            #
            # 2026-07-30 (stessa sera, dopo un test dubstep): l'operatore ha
            # visto OBS restare sull'ultima scena viva nonostante il log
            # mostrasse "[OBS] APPLICATA: Taglio 50ms -> black_color" -
            # scoperto rileggendo obs_controller.py che quella riga di log
            # scatta subito dopo aver impostato tipo/durata transizione, PRIMA
            # della vera chiamata set_current_program_scene() - "APPLICATA"
            # non ha MAI confermato che lo switch sia arrivato a destinazione,
            # solo che la transizione era stata configurata. Stessa lezione
            # di sempre in questo file: un log che conferma che il codice e'
            # girato come scritto non e' la stessa cosa di una conferma reale.
            # Fix: verificare DAVVERO con get_current_scene() dopo il sleep,
            # e ritentare una volta se non e' quella attesa, invece di fidarsi
            # del solo "nessuna eccezione sollevata".
            obs.switch_scene(brain.BLACK_PAUSE_SCENE, transition_ms=50, transition_type="Taglio")
            # Margine prima di verificare: la richiesta WebSocket non blocca
            # fino alla conferma di OBS - senza pausa la lettura successiva
            # arriverebbe troppo presto anche a switch riuscito.
            time.sleep(0.3)
            actual_scene = obs.get_current_scene()
            if actual_scene != brain.BLACK_PAUSE_SCENE:
                debug_log(f"[OBS] switch a nero non confermato (scena reale='{actual_scene}') - ritento")
                obs.switch_scene(brain.BLACK_PAUSE_SCENE, transition_ms=50, transition_type="Taglio")
                time.sleep(0.3)
                actual_scene = obs.get_current_scene()

            if actual_scene == brain.BLACK_PAUSE_SCENE:
                print(f"[OBS] {brain.BLACK_PAUSE_SCENE} in programma (confermato).")
                debug_log(f"[OBS] {brain.BLACK_PAUSE_SCENE} confermato in programma dopo switch")
            else:
                print(f"[OBS] ATTENZIONE: switch a nero NON confermato - scena reale rimasta '{actual_scene}'")
                debug_log(f"[OBS] switch a nero NON confermato dopo retry - scena reale='{actual_scene}'")

            # 2026-07-30 (stesso giorno, dopo la conferma via get_current_scene
            # sopra): l'operatore ha visto lo switch "confermato" nel log ma
            # il monitor fisico non e' andato a nero comunque - root cause
            # reale: sull'alternanza monitor a stacking (vedi window_manager.py)
            # ogni uscita ha 2 Proiettori GIA' APERTI sovrapposti (uno segue
            # il Programma, l'altro e' bloccato in permanenza su MONITOR_BLACK_
            # SCENE) - switchare il Programma cambia solo cosa mostra la
            # finestra "on", ma se al momento dello stop era in primo piano
            # quella finestra e non si ridisegna in tempo (o resta davanti per
            # qualunque motivo), il monitor fisico resta sull'ultimo frame
            # invece di andare a nero, indipendentemente da quanto sopra sia
            # davvero riuscito. Fix: forza ESPLICITAMENTE in primo piano la
            # finestra "off" (gia' bloccata su nero, stesso meccanismo gia'
            # usato per il resto dello show) per ENTRAMBE le uscite, invece di
            # fidarsi che la finestra giusta sia gia' quella davanti.
            if monitor_show1_off_id is not None:
                window_manager.activate(monitor_show1_off_id)
            if monitor_show2_off_id is not None:
                window_manager.activate(monitor_show2_off_id)

        _shutdown_step("spegni luci QLC+", _spegni_luci)
        _shutdown_step("OBS a nero", _obs_a_nero)
        _shutdown_step("stop audio", audio.stop)
        _shutdown_step("disconnetti OBS", obs.disconnect)


if __name__ == "__main__":
    main()