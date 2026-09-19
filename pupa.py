"""
PUPA - VJ Brain Production
Hybrid Couples Model: 4min timer + Music Reactive A↔B
"""

import time
import sys
import os
import random
import math
import json
import sounddevice as sd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from obs_controller import OBSController
from audio_analyzer import AudioAnalyzer
import brain
import scene_discovery
from logger import setup_logger
from debug_logger import debug as debug_log, setup_debug_logger

from runtime_monitor import RuntimeMonitor
from window_manager import get_window_manager
from hotkey_controller import MultiLevelControl, BinaryControl
from shutdown_helpers import shutdown_step, obs_a_nero

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


# COLORE PER LE LUCI (2026-09-19, PU.luci): il processo separato lights/pupa_luci.py
# fa seguire alle luci il colore d'identita' corrente di PUPA live. Legame a senso
# unico via file (best effort: un errore qui non deve MAI toccare il loop video):
# {"color": "red_color", "t": <time.time()>}, riscritto al cambio identita' e ogni
# 2 s come "battito" (le luci lo considerano scaduto dopo 10 s = pupa.py fermo).
IDENTITY_COLOR_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "identity_color.json")
IDENTITY_COLOR_PUBLISH_EVERY_S = 2.0


def _publish_identity_color(name):
    try:
        os.makedirs(os.path.dirname(IDENTITY_COLOR_FILE), exist_ok=True)
        tmp = IDENTITY_COLOR_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"color": name, "t": time.time()}, f)
        os.replace(tmp, IDENTITY_COLOR_FILE)
    except Exception:
        pass


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

# LUCI (QLC+): dal 2026-09-19 NON piu' pilotate da questo file - vivono nel processo
# separato lights/pupa_luci.py (reattive al suono, indipendenti dal video, hotkey
# proprie). Vedi LIGHTS_CONFIG.md. Il vecchio codice (specchio del polso colore,
# modalita' F5/F6/F7, wash ambient, strobo condiviso, enfasi wave) e' stato rimosso.

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
# = OBS forzato su BLACK_PAUSE_SCENE (le luci fanno blackout da sole in pupa_luci.py), bypassando
# la logica normale; Nascondi = ripristina, la prossima tick ricalcola tutto
# da zero (gate/colore/scena) come se il blackout non ci fosse mai stato.
# Stessa scena di servizio, stesso schema di risoluzione/polling.
BLACKOUT_SOURCE = "PUPA_BLACKOUT"

# OVERRIDE MANUALE MONITOR/LUCI: 2 hotkey binari indipendenti (F9/F10),
# ciascuno un toggle persistente come BLACKOUT - non un 3-way esclusivo,
# per scelta esplicita dell'operatore. Se entrambi risultassero attivi
# insieme (non dovrebbe succedere in uso normale), SOLO_MONITOR vince per
# precedenza fissa nel codice, vedi il blocco di dispatch sotto.
SOLO_MONITOR_SOURCE = "PUPA_SOLO_MONITOR"  # F9: monitor SEMPRE accesi, luci spente
SOLO_LUCI_SOURCE = "PUPA_SOLO_LUCI"  # F10: luci SEMPRE accese, monitor spenti

# SHUTDOWN (F12, 2026-08-01): hotkey per fermare PUPA senza bisogno del
# terminale - operatore dal vivo ha trovato che la finestra del terminale
# finisce sempre in secondo piano dietro i Proiettori dell'alternanza
# monitor, rendendo Ctrl+C irraggiungibile senza spostare fisicamente le
# finestre. "Mostra" solleva SystemExit dal loop principale - eredita da
# BaseException quindi il blocco finally (stessa cascata di spegnimento di
# un Ctrl+C: luci spente, OBS a nero, disconnessione) parte identico,
# nessuna duplicazione di logica.
SHUTDOWN_SOURCE = "PUPA_SHUTDOWN"

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

# CONTROLLO DI AVVIO DEI MONITOR (2026-09-20): in un test dal vivo un proiettore Programma e'
# risultato "sopra" nella pila delle finestre ma con lo schermo 100% nero (avvio del 2026-09-19
# 23:50, riavviare PUPA ha risolto): il log del brain mostra solo l'intenzione, non i pixel.
# Dopo aver aperto i proiettori e portato OBS sulla prima scena, per ogni uscita porta sopra il
# Programma e misura se disegna qualcosa; se e' buio forza per un attimo una scena luminosa
# (distingue "scena scura" da "proiettore rotto") e, se resta nero, riapre la coppia (max
# MONITOR_CHECK_RETRIES volte). Solo Linux (xwd): altrove region_stats() torna None e si salta.
MONITOR_STARTUP_CHECK = True
MONITOR_CHECK_MIN_LUM = 2.0      # luminanza media (0-255) sopra cui il proiettore "disegna"
MONITOR_CHECK_MIN_STD = 3.0      # oppure deviazione sopra cui c'e' contenuto (scena scura ma non vuota)
MONITOR_CHECK_BRIGHT_LUM = 20.0  # con scena luminosa forzata, sotto questo valore il proiettore e' rotto
MONITOR_CHECK_RETRIES = 2


def _monitor_output_renders(window_manager, obs, on_id, off_id, bright_scene, restore_scene):
    """True = il proiettore Programma disegna, False = resta nero anche con una scena luminosa,
    None = controllo non possibile (piattaforma senza misura)."""
    window_manager.activate(on_id, off_id)
    time.sleep(0.4)
    stats = window_manager.region_stats(on_id)
    if stats is None:
        return None
    mean, std = stats
    if mean >= MONITOR_CHECK_MIN_LUM or std >= MONITOR_CHECK_MIN_STD:
        return True
    if not bright_scene:
        return None  # buio ma niente scena luminosa con cui distinguere: non decido
    obs.switch_scene(bright_scene, transition_ms=50, transition_type="Taglio")
    time.sleep(0.6)
    stats = window_manager.region_stats(on_id)
    obs.switch_scene(restore_scene, transition_ms=50, transition_type="Taglio")
    if stats is None:
        return None
    return stats[0] >= MONITOR_CHECK_BRIGHT_LUM


def ensure_monitor_outputs(obs, window_manager, outputs, black_scene, bright_scene, restore_scene):
    """`outputs`: lista di dict {name, index, x, on, off}. Verifica che ogni uscita disegni e
    riapre la coppia di proiettori se no. Ritorna la lista (con gli ID eventualmente rinnovati).
    Non solleva mai: un problema qui non deve bloccare l'avvio."""
    try:
        for out in outputs:
            for attempt in range(MONITOR_CHECK_RETRIES + 1):
                ok = _monitor_output_renders(window_manager, obs, out["on"], out["off"], bright_scene, restore_scene)
                if ok is None:
                    print(f"[MONITOR] controllo di avvio non disponibile ({out['name']}), salto")
                    return outputs
                if ok:
                    msg = f"[MONITOR] controllo di avvio: {out['name']} OK" + (f" (dopo {attempt} riapertura/e)" if attempt else "")
                    print(msg)
                    debug_log(msg)
                    break
                if attempt >= MONITOR_CHECK_RETRIES:
                    msg = f"[MONITOR] ATTENZIONE: {out['name']} resta NERO dopo {MONITOR_CHECK_RETRIES} riaperture - controlla il monitor"
                    print(msg)
                    debug_log(msg)
                    break
                msg = f"[MONITOR] controllo di avvio: {out['name']} NERO, riapro la coppia (tentativo {attempt + 1}/{MONITOR_CHECK_RETRIES})"
                print(msg)
                debug_log(msg)
                new_on, new_off = window_manager.open_stacked_pair(obs, out["index"], black_scene, position_key=out["x"])
                if new_on is None or new_off is None:
                    print(f"[MONITOR] riapertura di {out['name']} fallita")
                    break
                out["on"], out["off"] = new_on, new_off
        for out in outputs:  # stato di partenza: nero sopra, come prima del controllo
            window_manager.activate(out["off"], out["on"])
    except Exception as e:
        print(f"[MONITOR] controllo di avvio interrotto ({e}), continuo")
        debug_log(f"[MONITOR] controllo di avvio: eccezione {e}")
    return outputs


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

    # Valida coppie/transizioni (ora comprensive di quanto scoperto sopra)
    # contro quello che esiste DAVVERO in questa installazione OBS - scene_B/
    # coppie/transizioni mancanti vengono tolte/sostituite invece di far
    # crashare o bloccare tutto. Se resta una sola scena, PUPA lampeggia su
    # quella invece di alternare A/B (vedi brain.validate_scenes).
    transitions = obs.get_transition_list()
    validation = brain.validate_scenes(scenes, transitions)

    # REGOLA "MAI IMMAGINI CON IMMAGINI" (2026-09-20): riconosce per CONTENUTO quali scene
    # (A e B, comprese le _B senza suffisso scritte a mano nel config come "slide" e le scene
    # annidate) mostrano immagini, e le passa al brain che non le accoppia tra loro.
    try:
        image_names = scene_discovery.image_input_names(all_inputs)
        pair_scenes = set(candidate_scenes) | set(brain.ALL_B_SCENES) | set(brain.COUPLES.keys())
        image_scenes = scene_discovery.scenes_with_images(pair_scenes, obs.get_scene_item_source_names, image_names, set(scenes))
        brain.set_image_scenes(image_scenes)
        print(f"[PUPA] Scene con immagini (mai accoppiate tra loro): {sorted(image_scenes) or 'nessuna'}")
    except Exception as e:
        print(f"[PUPA] Riconoscimento scene con immagini fallito ({e}): nessun vincolo immagini")
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
    identity_color_published_at = [0.0]  # ultimo momento in cui il colore e' stato scritto per le luci
    overlay_rgb = [None]           # colore attivo per l'identita' corrente, None se spento per questa coppia (roll off)
    overlay_peak_pct = [COLOR_OVERLAY_PEAK_PCT]  # picco per QUESTO colore (vedi COLOR_OVERLAY_PEAK_PCT_OVERRIDES)
    overlay_pulse_end_time = [0.0]  # 0.0 = nessun polso in corso
    black_overlay_pulse_end_time = [0.0]  # 0.0 = nessun polso nero (respiro a battuta) in corso
    pre_drop_flash_end_time = [0.0]  # 0.0 = nessun flash pre-drop in corso
    black_overlay_last_sent = [None]  # ultima opacita' INVIATA (non calcolata) - evita set_overlay_color a vuoto ogni frame

    # HOTKEY (2026-07-30, refactor): il meccanismo di polling/edge-detection
    # e' ora in hotkey_controller.py (MultiLevelControl/BinaryControl) - qui
    # restano solo la semantica (quali source, cosa fare quando cambiano) e
    # l'istanziazione. Vedi hotkey_controller.py per il "perche'" del design.
    calm_control = MultiLevelControl("Calm mode", CALM_CONTROL_SCENE, CALM_LEVEL_SOURCES)
    calm_control.resolve(obs, scenes)
    # 2026-09-18: stessa fix gia' fatta per light_mode (vedi resolved_level in
    # hotkey_controller.py) - senza questa riga, un CALM_N lasciato acceso in
    # OBS da una sessione precedente veniva ignorato in silenzio, il modello
    # ripartiva sempre a calm_level=0 finche' l'operatore non ripremeva
    # l'hotkey. Bug reale, trovato dal vivo il 2026-09-18 (OBS mostrava
    # ancora CALM_3 da un test precedente).
    if calm_control.active:
        brain.set_calm_level(calm_control.resolved_level)
        print(f"[PUPA] Calm mode ripristinato da OBS: {brain.get_calm_level()}")

    # Indicatore a video del livello (CALM_LEVEL_TEXT dentro PUPA_Control,
    # mai in onda - "verifica a video di quale stato sia attivo?", visibile
    # solo aprendo l'Anteprima di quella scena in OBS, non sul programma).
    calm_text_available = (
        CALM_CONTROL_SCENE in scenes
        and obs.get_source_item_id(CALM_CONTROL_SCENE, CALM_LEVEL_TEXT_SOURCE) is not None
    )
    if calm_text_available:
        obs.set_input_text(CALM_LEVEL_TEXT_SOURCE, f"CALM: {brain.get_calm_level()}")

    loop_scene_control = BinaryControl("Loop scena", CALM_CONTROL_SCENE, LOOP_SCENE_SOURCE)
    loop_scene_control.resolve(obs, scenes)

    blackout_control = BinaryControl("Blackout", CALM_CONTROL_SCENE, BLACKOUT_SOURCE)
    blackout_control.resolve(obs, scenes)
    blackout_active = [False]  # stato corrente, letto anche fuori dal blocco di poll (monitor/loop principale)

    solo_monitor_control = BinaryControl("Solo monitor", CALM_CONTROL_SCENE, SOLO_MONITOR_SOURCE)
    solo_monitor_control.resolve(obs, scenes)
    solo_luci_control = BinaryControl("Solo luci", CALM_CONTROL_SCENE, SOLO_LUCI_SOURCE)
    solo_luci_control.resolve(obs, scenes)
    solo_monitor_active = [False]
    solo_luci_active = [False]

    shutdown_control = BinaryControl("Shutdown", CALM_CONTROL_SCENE, SHUTDOWN_SOURCE)
    shutdown_control.resolve(obs, scenes)

    # RESET DI SICUREZZA ALL'AVVIO: questi override "momentanei" non devono
    # MAI ereditare uno stato "attivo" residuo da una sessione precedente
    # finita male (crash, taskkill, o semplicemente il processo morto prima
    # di poter riabbassare la propria stessa source - il caso di SHUTDOWN,
    # trovato dal vivo 2026-08-01: dopo un F12 riuscito la source restava
    # 'Mostra', e il prossimo avvio la leggeva gia' "premuta" - il comando
    # successivo non generava un fronte, F12 sembrava non rispondere piu').
    # CALM_LEVEL_SOURCES/LOOP_SCENE_SOURCE ne restano fuori apposta - per
    # quelli lo stato persistente tra riavvii e' quello desiderato.
    for ctrl in (blackout_control, solo_monitor_control, solo_luci_control, shutdown_control):
        if ctrl.active and obs.get_scene_item_enabled(CALM_CONTROL_SCENE, ctrl.item_id):
            ctrl.force(obs, False)
            print(f"[HOTKEY] {ctrl.name}: resettata a spenta all'avvio (era rimasta attiva)")

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

    # Controllo di avvio dei monitor (vedi MONITOR_STARTUP_CHECK sopra): la prima scena di
    # contenuto e' gia' in onda (transizione da 800ms in corso), quindi un proiettore sano
    # disegna qualcosa.
    if MONITOR_STARTUP_CHECK and monitor_alternation_enabled and window_manager is not None:
        time.sleep(1.0)  # lascia finire la transizione iniziale
        bright_scene = next((s for s in ["white_color"] + list(brain.STROBE_COLOR_POOL) if s in scenes), None)
        checked = ensure_monitor_outputs(
            obs, window_manager,
            [{"name": "show1", "index": MONITOR_SHOW1_INDEX, "x": monitor_show1_x, "on": monitor_show1_on_id, "off": monitor_show1_off_id},
             {"name": "show2", "index": MONITOR_SHOW2_INDEX, "x": monitor_show2_x, "on": monitor_show2_on_id, "off": monitor_show2_off_id}],
            MONITOR_BLACK_SCENE, bright_scene, current_scene)
        monitor_show1_on_id, monitor_show1_off_id = checked[0]["on"], checked[0]["off"]
        monitor_show2_on_id, monitor_show2_off_id = checked[1]["on"], checked[1]["off"]

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
                    or solo_monitor_control.active or solo_luci_control.active
                    or shutdown_control.active):
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
                            print("[BLACKOUT] attivo - monitor a nero (le luci: pupa_luci.py ascolta la stessa source), PUPA resta in ascolto")
                            debug_log("[BLACKOUT] attivo")
                        else:
                            print("[BLACKOUT] disattivato - ripristino normale dal prossimo tick")
                            debug_log("[BLACKOUT] disattivato")

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

                    # SHUTDOWN (F12): solleva SystemExit dal loop principale -
                    # eredita da BaseException, quindi il blocco finally piu'
                    # sotto (la stessa cascata di spegnimento di un Ctrl+C)
                    # parte identico, nessuna logica duplicata qui.
                    new_shutdown_trigger = shutdown_control.poll(obs)
                    if new_shutdown_trigger:
                        print("[SHUTDOWN] hotkey premuto - arresto in corso...")
                        debug_log("[SHUTDOWN] hotkey premuto")
                        raise SystemExit(0)

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

                # OVERLAY COLORE: al cambio identita' (rotazione coppia),
                # decide il colore attivo e se il pulsare e' abilitato per
                # questa coppia (COLOR_OVERLAY_OFF_PROBABILITY). Il pulsare
                # vero e proprio avviene sotto, ad ogni kick.
                identity_color = brain.get_identity_color_name()
                if identity_color != last_identity_color[0] or current_time - identity_color_published_at[0] >= IDENTITY_COLOR_PUBLISH_EVERY_S:
                    _publish_identity_color(identity_color)
                    identity_color_published_at[0] = current_time
                if identity_color != last_identity_color[0]:
                    rgb = identity_overlay_rgb.get(identity_color)
                    if rgb and random.random() >= COLOR_OVERLAY_OFF_PROBABILITY:
                        overlay_rgb[0] = rgb
                        overlay_peak_pct[0] = COLOR_OVERLAY_PEAK_PCT_OVERRIDES.get(identity_color, COLOR_OVERLAY_PEAK_PCT)
                    else:
                        overlay_rgb[0] = None
                        obs.set_overlay_color(COLOR_OVERLAY_SOURCE, rgb or (0, 0, 0), 0)
                        debug_log(f"[OVERLAY] pulso disabilitato per identita' '{identity_color}' (roll off fino al prossimo cambio identita')")
                    overlay_pulse_end_time[0] = 0.0
                    last_identity_color[0] = identity_color

                # POLSO SUL KICK (overlay OBS): accende al picco (per-colore,
                # vedi overlay_peak_pct) sul frame del kick, poi dissolvenza
                # lineare a 0 in COLOR_OVERLAY_DECAY_S secondi - tra un kick
                # e l'altro resta a 0 (vedi commento sopra le costanti).
                # Resta SEMPRE kick-reattivo, in ogni stato - solo il lato
                # luci fisiche (QLC+, sotto) cambia comportamento negli stati
                # di quiete (2026-07-29, Step 2 del piano luci).
                # CALM (2026-09-18): scala/spegne il polso a SCHERMO - a CALM 3 e' 0,
                # niente flash colorato a ogni kick. Vedi CALM_MULTIPLIERS["overlay_pulse"].
                overlay_scale = brain.get_calm_value("overlay_pulse")
                if overlay_rgb[0]:
                    if is_kick:
                        if overlay_scale > 0:
                            obs.set_overlay_color(COLOR_OVERLAY_SOURCE, overlay_rgb[0], overlay_peak_pct[0] * overlay_scale)
                        overlay_pulse_end_time[0] = current_time + COLOR_OVERLAY_DECAY_S
                    elif overlay_pulse_end_time[0] > 0:
                        remaining = overlay_pulse_end_time[0] - current_time
                        if remaining > 0:
                            frac = remaining / COLOR_OVERLAY_DECAY_S
                            if overlay_scale > 0:
                                obs.set_overlay_color(COLOR_OVERLAY_SOURCE, overlay_rgb[0], overlay_peak_pct[0] * frac * overlay_scale)
                        else:
                            obs.set_overlay_color(COLOR_OVERLAY_SOURCE, overlay_rgb[0], 0)
                            overlay_pulse_end_time[0] = 0.0

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
                        twin = monitor_show1_off_id if monitor_show1_state else monitor_show1_on_id
                        attempted = True
                        ok = window_manager.activate(target, twin) and ok
                    if desired["show2"] != monitor_show2_state:
                        monitor_show2_state = desired["show2"]
                        target = monitor_show2_on_id if monitor_show2_state else monitor_show2_off_id
                        twin = monitor_show2_off_id if monitor_show2_state else monitor_show2_on_id
                        attempted = True
                        ok = window_manager.activate(target, twin) and ok

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

        # Cascata di arresto condivisa con gli script gemelli (exhibition/,
        # slideshow/) - vedi shutdown_helpers.py. Estrazione meccanica del
        # 2026-08-14 dalle funzioni annidate che vivevano qui prima (stesso
        # comportamento, stessa storia/commenti di debug dal vivo - vedi
        # quel file per il "perche'" di ogni dettaglio).
        shutdown_step("OBS a nero", lambda: obs_a_nero(
            obs, brain.BLACK_PAUSE_SCENE,
            (monitor_show1_off_id, monitor_show2_off_id),
            window_manager, debug_log
        ))
        shutdown_step("stop audio", audio.stop)
        shutdown_step("disconnetti OBS", obs.disconnect)


if __name__ == "__main__":
    main()