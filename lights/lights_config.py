"""
lights_config.py - mappa hardware QLC+ e TUTTI i parametri di tuning delle
luci (PU.luci, 2026-09-19). Nessuna logica qui: si ritocca questo file per
"tarare a occhio", mai lights_engine.py.

Le luci sono INDIPENDENTI dal video: nulla qui dipende da brain.py, dalle
scene OBS, dall'identita' colore o dai monitor. Sorgente dei dati: solo
l'audio (AudioAnalyzer.get_metrics()).
"""

# ---------------------------------------------------------------------------
# Hardware: id OS2L "cmd" del progetto QLC+ (vedi LIGHTS_CONFIG.md, tabella
# slider). Stessi identici valori di pupa.py (QLC_CHANNEL_F1_* / F2_*).
# ---------------------------------------------------------------------------
CH = {
    "f1": {"master": 13, "r": 10, "g": 11, "b": 12, "strobe": 14},
    "f2": {"master": 18, "r": 15, "g": 16, "b": 17, "strobe": 19},
}
# Tutti i canali che il runner spegne allo shutdown (Strobe incluso, come pupa.py).
ALL_CHANNELS = tuple(ch for side in CH.values() for ch in side.values())

# ---------------------------------------------------------------------------
# Loop / invio
# ---------------------------------------------------------------------------
TICK_HZ = 30                  # frequenza del loop luci (l'audio arriva a blocchi da ~46ms)
MIN_SEND_STEP = 3             # un canale RGB si rimanda solo se cambia di almeno questo (0/255 sempre inviati) - limita il traffico OS2L
OS2L_MIN_GAP_S = 0.002        # pausa minima tra due messaggi OS2L consecutivi. MISURATO 2026-09-19 su QLC+ 4.12.7 (Linux): messaggi JSON mandati uno dietro l'altro senza pausa -> 47% dei canali PERSI (113/240; il plugin parsa una lettura TCP per volta e scarta i JSON concatenati); con 1 ms di pausa 0/240 persi (idem 3 e 10 ms).
SUMMARY_EVERY_S = 10.0        # riga riassuntiva di metriche in lights.log

# ---------------------------------------------------------------------------
# Hotkey OBS (stesso meccanismo "source fittizia in PUPA_Control" di
# hotkey_controller.py). NUOVI: LUCI (on/off) e LUCI_LIVELLO_1..3.
# Condivisi con pupa.py (che oggi li gestisce ancora per i monitor): F8 strobo
# bianco manuale, F11 blackout, F12 shutdown.
# ---------------------------------------------------------------------------
CONTROL_SCENE = "PUPA_Control"
SRC_LIGHTS_ONOFF = "PUPA_LUCI"                       # Mostra = luci accese, Nascondi = spente
SRC_LIGHTS_LEVEL = {1: "PUPA_LUCI_LIVELLO_1",         # 3 livelli esclusivi (solo "Mostra", autopulizia)
                    2: "PUPA_LUCI_LIVELLO_2",
                    3: "PUPA_LUCI_LIVELLO_3"}
SRC_STROBE_WHITE = "PUPA_STROBE_WHITE"                # F8 (esistente): strobo bianco manuale a toggle
SRC_BLACKOUT = "PUPA_BLACKOUT"                        # F11 (esistente)
SRC_SHUTDOWN = "PUPA_SHUTDOWN"                        # F12 (esistente)
CONTROL_POLL_S = 0.25                                 # polling OBS, in un thread a parte (mai nel loop luci)
CONTROL_RECONNECT_S = 10.0                            # ritenta OBS se non era raggiungibile / e' caduto

DEFAULT_ON = True             # stato se la source SRC_LIGHTS_ONOFF non esiste in OBS
DEFAULT_LEVEL = 2             # livello se nessuna source livello e' accesa

# Tetto di intensita' per livello (moltiplica il colore, non tocca lo strobo di Master).
LEVEL_SCALE = {1: 0.35, 2: 0.70, 3: 1.00}

# ---------------------------------------------------------------------------
# Accensione / spegnimento (dissolvenza del gain globale)
# ---------------------------------------------------------------------------
FADE_IN_S = 0.8
FADE_OUT_S = 0.5

# ---------------------------------------------------------------------------
# Segnale / silenzio
# ---------------------------------------------------------------------------
SILENCE_DB = -50.0            # sotto questo dBFS (EMA) il segnale e' considerato assente
SILENCE_HOLD_S = 1.5          # da quanto e' sotto soglia prima di dissolvere a nero

# ---------------------------------------------------------------------------
# Palette PROPRIA (indipendente dall'identita' colore del video).
# Solo primari RGB puri come da scelta storica dell'operatore (niente giallo:
# su questo fixture legge come verde; bianco riservato allo strobo).
# I 2 fari hanno colori DIVERSI (coppia) e si alternano sui kick.
# ---------------------------------------------------------------------------
COLORS = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255)}
WHITE = (255, 255, 255)
PHRASE_BEATS = 32             # ogni quanti beat (~una frase) ruota la coppia colore
PHRASE_S_MIN = 10.0           # limiti in secondi (BPM assente/strano)
PHRASE_S_MAX = 40.0
PHRASE_S_NO_BPM = 16.0
COLOR_SWAP_DARK = 0.08        # il nuovo colore entra su un faro quando la sua intensita' e' sotto questa soglia (cambio invisibile)...
COLOR_SWAP_MAX_WAIT_S = 2.0   # ...o dopo questo tempo comunque

# ---------------------------------------------------------------------------
# Reattivita' al BASS = intensita'
# ---------------------------------------------------------------------------
KICK_STRENGTH_MIN = 0.65      # ampiezza minima del polso su un kick debole (0..1)
KICK_DECAY_BEAT_FRAC = 0.30   # costante di decadimento del polso = frazione del periodo di beat...
KICK_DECAY_MIN_S = 0.08       # ...con questi limiti
KICK_DECAY_MAX_S = 0.35
KICK_DECAY_NO_BPM_S = 0.15
FOLLOW_ATTACK_S = 0.03        # inseguitore del bass continuo (stessa reattivita' anche se i kick non vengono rilevati)
FOLLOW_RELEASE_S = 0.22
FOLLOW_GAIN = 0.30            # peso dell'inseguitore (mirror su entrambi i fari)
FOLLOW_EXPONENT = 1.6         # >1: enfatizza i picchi, i livelli medi restano bassi
MID_FLOOR_MIN = 0.05          # pavimento acceso quando c'e' musica...
MID_FLOOR_RANGE = 0.12        # ...piu' fino a questo, proporzionale ai medi (tiene vivo il colore nei passaggi senza kick)

# ---------------------------------------------------------------------------
# Break (assenza di bass): respiro lento, i kick vengono ignorati
# ---------------------------------------------------------------------------
BREAK_ENTER_S = 0.8           # is_break deve reggere cosi' a lungo prima di entrare
BREAK_EXIT_S = 0.4
BREAK_BREATH_PERIOD_S = 8.0
BREAK_BREATH_MIN = 0.05
BREAK_BREATH_MAX = 0.35
BREAK_MIX_TAU_S = 0.5         # dissolvenza tra comportamento normale e respiro

# ---------------------------------------------------------------------------
# Strobo sul DROP (proprio, non legato al video) + strobo manuale F8
# ---------------------------------------------------------------------------
DROP_STROBE_FLASHES = 6       # lampi bianchi (ognuno on+off)
DROP_STROBE_INTERVAL_BEAT_DIV = 4   # mezzo periodo = beat/4 (un sedicesimo), con questi limiti
DROP_STROBE_INTERVAL_MIN_S = 0.08   # >= 2-3 tick del loop (TICK_HZ) per restare frame-accurate
DROP_STROBE_INTERVAL_MAX_S = 0.12
DROP_STROBE_COOLDOWN_S = 8.0
STROBE_MANUAL_HALF_PERIOD_S = 0.1   # F8: on 0.1s / off 0.1s, come pupa.py
