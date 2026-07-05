"""
PUPA - VJ Brain Production
Hybrid Couples Model: 4min timer + Music Reactive A↔B
"""

import time
import sys
import os
import random

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from obs_controller import OBSController
from audio_analyzer import AudioAnalyzer
import brain
from logger import setup_logger

try:
    from secrets_local import OBS_HOST, OBS_PORT, OBS_PASSWORD
except ImportError:
    print("[ERROR] secrets_local.py mancante. Copia secrets_local.example.py")
    print("        in secrets_local.py e inserisci le credenziali reali di OBS.")
    sys.exit(1)

CONFIG = {
    "obs_host": OBS_HOST,
    "obs_port": OBS_PORT,
    "obs_password": OBS_PASSWORD,
    # ATTENZIONE PORTING: questo ID e' specifico della macchina Windows
    # attuale ("Microsoft Sound Mapper - Input"). Su Linux (o qualsiasi
    # altra macchina) gli ID dei device audio sono enumerati diversamente:
    # rieseguire list_audio_devices.py e aggiornare questo valore. Vedi
    # LINUX_PORTING.md per i dettagli (su Linux cercare un device "Monitor",
    # non "Stereo Mix" che non esiste li').
    "audio_device": 0,  # Microsoft Sound Mapper - Input (SOLO su questa macchina Windows)
}

TRANSITION_MS = 2500

# ============================================================================
# SCALE-TO-SOUND — DISATTIVATO (2026-07-03)
# ============================================================================
# Motivo: dopo vari fix (bug cache rendering OBS + refresh disable/enable,
# smoothing per-scena, throttle, ricalibrazione dB) wave_kick restava
# comunque fermo in Program (funzionava solo in Preview, causa non risolta),
# e strobo_B reagiva ma troppo lentamente. L'utente ha tolto strobo_B dallo
# scale-to-sound (la scena funziona bene visivamente anche senza scalare), e
# per ora anche wave_kick (dove "Immagine 2" e' stata sostituita con "Audio
# Shader Engine", una sorgente gia' reattiva di suo che non necessita del
# nostro scale-to-sound manuale).
#
# Codice tenuto per riferimento/eventuale riattivazione futura, non cancellato.
# Se si riattiva: verificare PRIMA il nome della sorgente in wave_kick (ora
# "Audio Shader Engine", non piu' "Immagine 2") e il bug "fermo in Program"
# (vedi PUPA_DEVELOPMENT_LOG.md "MISTERO RISOLTO" + "Aggiornamento 6") che
# NON era mai stato confermato risolto dal vivo nonostante il throttle.
#
# SCALE_TO_SOUND_TARGETS = {
#     "waveform_kick": ["Audio Shader Engine"],
#     "strobo_B": ["Colore"],
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
    #     "waveform_kick": 3,
    #     "strobo_B": 1,
    # }

    audio = AudioAnalyzer(device=CONFIG["audio_device"])
    try:
        audio.start()
    except Exception as e:
        print(f"[ERROR] AUDIO: Avvio fallito: {e}")
        import traceback
        traceback.print_exc()
        return
    
    print(f"[AUDIO] Device {CONFIG['audio_device']} avviato")
    
    current_scene = obs.get_current_scene()
    brain.initialize_model(current_scene, time.time())
    print(f"[BRAIN] Inizializzato su scena: {current_scene}")
    
    running = True
    
    try:
        while running:
            current_time = time.time()
            audio_data = audio.get_metrics()
            
            if audio_data:
                bass = audio_data.get("bass", 0)
                mid = audio_data.get("mid", 0)
                hi = audio_data.get("hi", 0)
                is_kick = audio_data.get("is_kick", False)
                is_drop = audio_data.get("is_drop", False)
                db_level = audio_data.get("db_level", -60.0)
                
                bass_bar = "#" * int(bass / 5)
                mid_bar = "#" * int(mid / 5)
                hi_bar = "#" * int(hi / 5)
                
                event_label = ""
                if is_kick:
                    event_label = " | KICK"
                elif is_drop:
                    event_label = " | DROP"
                
                print(f"[AUDIO] B: [{bass_bar:<20}] M: [{mid_bar:<20}] H: [{hi_bar:<20}] dB:{db_level:6.1f}{event_label}")

                # DECIDI SUBITO (ogni frame, senza delay)
                current_scene = obs.get_current_scene()

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
                
                # Switch se necessario
                if next_scene and next_scene != current_scene:
                    trans_info = brain.get_transition_info()
                    trans_type = trans_info.get("type", "Burn")
                    trans_ms = int(trans_info.get("duration_ms", TRANSITION_MS))
                    is_return = trans_info.get("is_return", False)
                    kick_mode = trans_info.get("kick_mode", "")

                    if kick_mode == "wave":
                        print(f"[WAVE_KICK] entrata -> {trans_type} {trans_ms}ms")
                    elif kick_mode == "crescendo":
                        print(f"[WAVE_KICK] ritorno a A -> {trans_type} {trans_ms}ms")
                    elif kick_mode == "strobe":
                        print(f"[STROBE] frame -> {trans_type} {trans_ms}ms")
                    elif kick_mode == "overlap":
                        print(f"[SOVRAPPOSIZIONE] -> {trans_type} {trans_ms}ms")

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