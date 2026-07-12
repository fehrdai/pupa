"""
PUPA - VJ Brain Production
Hybrid Couples Model: 4min timer + Music Reactive A↔B
"""

import time
import sys
import os
import random
import sounddevice as sd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from obs_controller import OBSController
from audio_analyzer import AudioAnalyzer
import brain
from logger import setup_logger
from debug_logger import debug as debug_log

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
# stessa immagine - "un po' troppo ripetitivo". Filtrate a quelle REALMENTE
# presenti in OBS subito dopo la connessione (vedi piu' sotto); se nessuna
# esiste ancora, fallback alla singola "wave_kick" originale.
WAVE_KICK_VARIANTS = ["wave_kick1", "wave_kick2", "wave_kick3", "wave_kick4"]

# SPERIMENTALE (2026-07-06): scene alternative a wave_kick, per vedere se
# stanno bene alternate in modo randomico nel ciclo INTRO/BREAK. "ago_talk"
# e' la cattura finestra del terminale che esegue pupa.py stesso.
WAVE_KICK_ALT_SCENES = ["ago_talk"]
WAVE_KICK_ALT_PROBABILITY = 0.3  # 30% delle volte al posto di wave_kick

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
    
    scenes = obs.cache_scenes()
    print(f"[PUPA] Mappate {len(scenes)} scene hardware stabili.")

    # Valida coppie/transizioni configurate (scenes_config.yaml) contro
    # quello che esiste DAVVERO in questa installazione OBS - scene_B/coppie/
    # transizioni mancanti vengono tolte/sostituite invece di far crashare o
    # bloccare tutto. Se resta una sola scena, PUPA lampeggia su quella
    # invece di alternare A/B (vedi brain.validate_scenes).
    transitions = obs.get_transition_list()
    validation = brain.validate_scenes(scenes, transitions)
    print(f"[PUPA] Validazione: {len(validation['couples'])} coppie valide"
          f"{' | MODALITA DEGENERATA (1 sola scena)' if validation['degenerate'] else ''}")

    # Varianti wave_kick REALMENTE presenti in OBS (vedi WAVE_KICK_VARIANTS
    # sopra) - fallback alla singola "wave_kick" se nessuna esiste ancora.
    available_wave_kick_variants = [s for s in WAVE_KICK_VARIANTS if s in scenes]
    if not available_wave_kick_variants:
        available_wave_kick_variants = ["wave_kick"]
    print(f"[PUPA] Varianti wave_kick disponibili: {available_wave_kick_variants}")
    last_wave_kick_variant = [None]  # lista per mutabilita' dentro il loop

    # CALM MODE: risolvi gli scene_item_id delle 4 source di controllo (vedi
    # CALM_LEVEL_SOURCES sopra), una volta sola all'avvio. Se la scena di
    # servizio non esiste ancora (utente non l'ha ancora creata in OBS), il
    # polling nel loop principale si disattiva da solo - nessun crash.
    calm_item_ids = {}
    if CALM_CONTROL_SCENE in scenes:
        for level, source_name in CALM_LEVEL_SOURCES.items():
            item_id = obs.get_source_item_id(CALM_CONTROL_SCENE, source_name)
            if item_id is not None:
                calm_item_ids[level] = item_id
        if calm_item_ids:
            print(f"[PUPA] Calm mode: {len(calm_item_ids)}/4 source di controllo trovate in '{CALM_CONTROL_SCENE}'")
        else:
            print(f"[PUPA] Calm mode: scena '{CALM_CONTROL_SCENE}' trovata ma nessuna source CALM_0-3 al suo interno")
    else:
        print(f"[PUPA] Calm mode: scena '{CALM_CONTROL_SCENE}' non trovata, hotkey disattivati")

    # Indicatore a video del livello (CALM_LEVEL_TEXT dentro PUPA_Control,
    # mai in onda - "verifica a video di quale stato sia attivo?", visibile
    # solo aprendo l'Anteprima di quella scena in OBS, non sul programma).
    calm_text_available = (
        CALM_CONTROL_SCENE in scenes
        and obs.get_source_item_id(CALM_CONTROL_SCENE, CALM_LEVEL_TEXT_SOURCE) is not None
    )
    if calm_text_available:
        obs.set_input_text(CALM_LEVEL_TEXT_SOURCE, "CALM: 0")

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

    audio = AudioAnalyzer(device=audio_device)
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

    running = True
    calm_poll_tick = 0

    try:
        while running:
            current_time = time.time()

            # CALM MODE: polling leggero (non ad ogni frame, vedi
            # CALM_POLL_EVERY_N_TICKS) delle 4 source di controllo - se piu'
            # di una risulta accesa, vince il livello piu' alto.
            if calm_item_ids:
                calm_poll_tick += 1
                if calm_poll_tick >= CALM_POLL_EVERY_N_TICKS:
                    calm_poll_tick = 0
                    enabled_levels = [lvl for lvl in sorted(calm_item_ids.keys())
                                       if obs.get_scene_item_enabled(CALM_CONTROL_SCENE, calm_item_ids[lvl])]
                    active_level = max(enabled_levels) if enabled_levels else 0
                    if active_level != brain.get_calm_level():
                        brain.set_calm_level(active_level)
                        print(f"[CALM MODE] livello -> {active_level}")
                        debug_log(f"[CALM MODE] livello -> {active_level}")
                        if calm_text_available:
                            obs.set_input_text(CALM_LEVEL_TEXT_SOURCE, f"CALM: {active_level}")

                    # Autopulizia: spegne le altre source rimaste accese -
                    # osservato dal vivo che con soli hotkey "Mostra"
                    # assegnati (nessun "Nascondi"), ogni pressione si
                    # limitava ad ACCENDERE senza mai spegnere la precedente
                    # (0/1/3 accese insieme) - "vince il piu' alto" mascherava
                    # il problema ma non si poteva mai SCENDERE di livello.
                    # Ora basta il solo hotkey "Mostra" per livello.
                    for lvl in enabled_levels:
                        if lvl != active_level:
                            obs.set_scene_item_enabled(CALM_CONTROL_SCENE, calm_item_ids[lvl], False)

            audio_data = audio.get_metrics()
            
            if audio_data:
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

                    # SPERIMENTALE: ogni tanto, al posto di wave_kick, mostra
                    # una scena alternativa (es. "ago_talk") - per vedere se
                    # ci sta bene nel ciclo. Sostituzione solo qui (la scena
                    # OBS effettiva), la logica interna di brain.py resta
                    # invariata (pensa sempre "wave_kick" per dwell/timeout/
                    # transizioni) - facile da togliere se non convince.
                    #
                    # Basato su next_scene (non su kick_mode=="wave"): molti
                    # ingressi in wave_kick arrivano anche via SOVRAPPOSIZIONE
                    # (kick_mode="overlap"), che kick_mode=="wave" da solo non
                    # intercettava - osservato dal vivo (solo 2/20 sostituiti
                    # invece del ~30% atteso).
                    if next_scene == "wave_kick":
                        # Variante wave_kick dedicata alla scena_A corrente (vedi
                        # IDENTITY in scenes_config.yaml) - stessa ogni volta che
                        # appare durante il turno di QUELLA scena_A, per rinforzarne
                        # l'identita'. Fallback alla scelta random anti-repeat se
                        # la scena_A non ha una variante assegnata (o non ancora
                        # disponibile in OBS - gia' filtrato da validate_scenes).
                        identity_variant = brain.get_identity_wave_kick_variant()
                        if identity_variant and identity_variant in available_wave_kick_variants:
                            next_scene = identity_variant
                        else:
                            choices = [v for v in available_wave_kick_variants if v != last_wave_kick_variant[0]] \
                                or available_wave_kick_variants
                            next_scene = random.choice(choices)
                        last_wave_kick_variant[0] = next_scene

                        # SPERIMENTALE: ogni tanto ago_talk al posto della variante
                        if WAVE_KICK_ALT_SCENES and random.random() < WAVE_KICK_ALT_PROBABILITY:
                            next_scene = random.choice(WAVE_KICK_ALT_SCENES)

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
        audio.stop()
        obs.disconnect()


if __name__ == "__main__":
    main()