import os
import sys
import time
import yaml
from audio_analyzer import AudioAnalyzer
from obs_control import OBSController
from brain import PupaBrain

def load_config():
    cartella_script = os.path.dirname(os.path.abspath(__file__))
    percorso_yaml = os.path.join(cartella_script, "scenes.yaml")
    
    try:
        with open(percorso_yaml, "r") as f:
            return yaml.safe_load(f)
    except Exception as e:
        print(f"[⚠️ ERR] Impossibile caricare scenes.yaml da {percorso_yaml}: {e}")
        sys.exit(1)

def main():
    print("[🚀 PUPA] Inizializzazione del sistema di sintonizzazione audio-video...")
    
    config = load_config()
    audio_analyzer = AudioAnalyzer()
    obs_ctrl = OBSController()
    brain = PupaBrain(config)
    
    try:
        cached_scenes = obs_ctrl.get_scene_list()
        print(f"[🤖 OBS] Scene caricate in cache: {cached_scenes}")
    except Exception as e:
        print(f"[❌ ERR] Connessione a OBS fallita: {e}")
        sys.exit(1)
        
    current_scene = obs_ctrl.get_current_scene()
    print(f"[🎬 OBS] Scena iniziale rilevata su OBS: '{current_scene}'")
    
    tick_interval = 0.1  # Controllo a 10Hz
    current_scene_duration = 5.0  # Durata temporanea iniziale
    
    # FORZATURA: Impostiamo il timer uguale alla durata per forzare un cambio immediato al primo ciclo
    scene_timer = current_scene_duration  
    
    print("[🟢 RUN] Loop principale avviato a 10Hz. In ascolto...")
    ticks = 0
    
    try:
        while True:
            # Se lo script si blocca qui, significa che l'AudioAnalyzer non sta rispondendo
            audio_data = audio_analyzer.get_metrics()
            ticks += 1
            
            volume_rms = audio_data.get("rms", 0.0)
            
            # Telemetria live stampata ogni secondo (10 tick) sulla stessa riga
            if ticks % 10 == 0:
                print(f"[📊 DIRETTA] Audio RMS: {volume_rms:.1f} | Timer: {scene_timer:.1f}/{current_scene_duration}s", end="\r")
            
            # --- MILESTONE 3: KILL SWITCH SU VOLUME RMS ---
            if volume_rms < 5.0:
                if current_scene != "NERO_MASTER":
                    print(f"\n[🛑 KILL SWITCH] Silenzio rilevato (RMS: {volume_rms:.1f}). Forzatura su NERO_MASTER.")
                    try:
                        obs_ctrl.switch_scene("NERO_MASTER")
                        current_scene = "NERO_MASTER"
                    except Exception as e:
                        print(f"[ERR] Switch d'emergenza fallito: {e}")
                
                time.sleep(tick_interval)
                scene_timer = 0.0  
                continue  
            
            # Ripristino immediato se usciamo dal silenzio
            if current_scene == "NERO_MASTER" and volume_rms >= 5.0:
                print(f"\n[🎵 PUPA] Musica rilevata! (RMS: {volume_rms:.1f}). Ripristino delle scene.")
                scene_timer = current_scene_duration  
            
            # --- GESTIONE TIMER E CAMBIO SCENA ---
            scene_timer += tick_interval
            
            if scene_timer >= current_scene_duration:
                # Interroghiamo il modulo Brain
                next_scene, stats, energy_str, duration = brain.decide_next_scene(cached_scenes, audio_data)
                
                if next_scene is None:
                    print(f"\n[⚠️ WARN] Il Brain non ha trovato scene valide compatibili tra il file YAML e OBS.")
                elif next_scene != current_scene:
                    print(f"\n[🎬 CAMBIO] Stato: {energy_str} | Volume RMS: {volume_rms:.1f} | Mood: {stats.get('mood', 'Chill')}")
                    print(f" └─► Switch: {current_scene} ──> {next_scene} (Durata: {duration}s)")
                    
                    try:
                        obs_ctrl.switch_scene(next_scene)
                        current_scene = next_scene
                    except Exception as e:
                        print(f"[❌ ERR] Errore durante lo switch in OBS: {e}")
                else:
                    # Se il Brain sceglie la stessa identica scena, facciamo comunque un refresh log
                    print(f"\n[🔄 REFRESH] Il Brain mantiene '{current_scene}' per altri {duration}s (Stato Audio: {energy_str})")
                        
                scene_timer = 0.0
                current_scene_duration = duration
                
            time.sleep(tick_interval)
            
    except KeyboardInterrupt:
        print("\n[🛑 PUPA] Script interrotto dall'utente. Uscita pulita.")
    finally:
        if hasattr(audio_analyzer, 'close'):
            audio_analyzer.close()
        if hasattr(obs_ctrl, 'close'):
            obs_ctrl.close()

if __name__ == "__main__":
    main()