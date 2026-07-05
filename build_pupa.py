#!/usr/bin/env python3
"""
PUPA VJ Brain v0.1 'Heartbeat'
Setup script con fix per nomi scene e dipendenze
Esegui: python3 pupa_setup_fixed.py
"""

import os
import sys

def create_structure():
    folders = ["pupa", "pupa/plugins", "pupa/logs", "pupa/assets"]
    for folder in folders:
        os.makedirs(folder, exist_ok=True)
        print(f"✓ Cartella creata: {folder}")

def create_files():
    files = {
        # 1. requirements.txt
        "pupa/requirements.txt": """obsws-python==1.5.3
pyyaml>=6.0
""",

        # 2. config.yaml
        "pupa/config.yaml": """obs:
  host: "localhost"
  port: 4455
  password: "INSERISCI_LA_TUA_PASSWORD_WEBSOCKET_QUI"

project:
  name: "PUPA"
  version: "0.1-Heartbeat"
  loop_interval: 3  # secondi tra un cambio scena (può essere sovrascritto da aubio BPM)
  
audio:
  bpm_detection: true
  aubio_path: null  # se hai lo script Python aubio, punta qui
""",

        # 3. scenes.yaml — CORRETTO CON NOMI REALI DAL LOG
        "pupa/scenes.yaml": """scenes:
  - name: "Scena 0"
    energy: "low"
    tags: ["immagine", "chroma-key", "scroll", "shadertastic"]
  
  - name: "futureflash"
    energy: "high"
    tags: ["media", "retro", "scale-to-sound", "shadertastic"]

  - name: "montezuma"
    energy: "high"
    tags: ["media", "scale-to-sound", "shadertastic"]

  - name: "kusanagi"
    energy: "medium"
    tags: ["cyberanime", "retro", "false-color"]

  - name: "psicodance"
    energy: "high"
    tags: ["media", "retro", "shadertastic"]

  - name: "segnali"
    energy: "medium"
    tags: ["media", "scale-to-sound", "shadertastic"]

  - name: "urbanfree"
    energy: "medium"
    tags: ["media", "retro", "shadertastic"]

  - name: "spectrumbar"
    energy: "high"
    tags: ["audio-shader", "generative", "reactive"]

  - name: "radialspike"
    energy: "high"
    tags: ["audio-shader", "generative", "retro"]

  - name: "roundedbar"
    energy: "medium"
    tags: ["audio-shader", "generative", "retro"]

  - name: "dust"
    energy: "high"
    tags: ["audio-shader", "generative", "color-correct"]

  - name: "stormlightning"
    energy: "high"
    tags: ["audio-shader", "generative", "blur"]

  - name: "tunnelwave"
    energy: "medium"
    tags: ["audio-shader", "generative", "stroke"]

  - name: "ring"
    energy: "medium"
    tags: ["audio-shader", "generative", "reactive"]

  - name: "Scena 13"
    energy: "medium"
    tags: ["audio-shader", "generative", "reactive"]

  - name: "Scena 14"
    energy: "low"
    tags: ["waveform", "visualizer"]

  - name: "Scena 15"
    energy: "low"
    tags: ["waveform", "visualizer"]

  - name: "strobo"
    energy: "high"
    tags: ["strobo", "audio-reactive", "waveform", "media"]

  - name: "mri"
    energy: "low"
    tags: ["visualizer", "tetris", "game"]

  - name: "projectx"
    energy: "high"
    tags: ["milkdrop", "audio-reactive", "generative"]
""",

        # 4. obs_controller.py — CORRETTO
        "pupa/obs_controller.py": """import sys

try:
    from obsws_python import ReqClient
except ImportError:
    print("[ERRORE] obsws_python non installato. Esegui: pip install obsws-python")
    sys.exit(1)

class OBSController:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.client = None

    def connect(self):
        try:
            self.client = ReqClient(
                host=self.host, 
                port=self.port, 
                password=self.password,
                timeout=5
            )
            v = self.client.get_version()
            print(f"[OBS] Connesso! OBS v{v.obs_version} (WS v{v.obs_web_socket_version})")
            return True
        except Exception as e:
            print(f"[OBS] Errore connessione: {e}")
            print(f"      Verifica che OBS WebSocket sia abilitato (Tools → WebSocket Server)")
            return False

    def get_available_scenes(self):
        if not self.client:
            return []
        try:
            response = self.client.get_scene_list()
            scene_names = [s['sceneName'] for s in response.scenes]
            return scene_names
        except Exception as e:
            print(f"[OBS] Errore lettura scene: {e}")
            return []

    def get_current_scene(self):
        if not self.client:
            return None
        try:
            response = self.client.get_current_program_scene()
            return response.current_program_scene_name
        except Exception as e:
            print(f"[OBS] Errore lettura scena attiva: {e}")
            return None

    def switch_scene(self, scene_name):
        if not self.client:
            return False
        try:
            self.client.set_current_program_scene(scene_name)
            return True
        except Exception as e:
            print(f"[OBS] Errore switch '{scene_name}': {e}")
            return False

    def disconnect(self):
        # obsws_python auto-closes quando il client viene distrutto
        # Non è necessario fare nulla esplicitamente
        pass
""",

        # 5. logger.py
        "pupa/logger.py": """import datetime
import os

class PupaLogger:
    def __init__(self, log_dir="logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self.log_path = os.path.join(log_dir, "runtime.log")

    def log_decision(self, current_scene, next_scene, stats, reason):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        log_entry = (
            f"{timestamp}\\n"
            f"  [Stato] Energia: {stats.get('energy_level')}% | Mood: {stats.get('mood')} | Peak: {stats.get('peak')}\\n"
            f"  [Switch] {current_scene} ──> {next_scene}\\n"
            f"  [Motivo] {reason}\\n"
            f"{'-'*50}\\n"
        )
        
        # Stampa a schermo per il VJ
        print(log_entry)
        
        # Salva su file per dopo
        try:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(log_entry)
        except IOError as e:
            print(f"[LOG] Errore scrittura su file: {e}")
""",

        # 6. brain.py
        "pupa/brain.py": """import random

class PupaBrain:
    def __init__(self, scenes_config):
        self.scenes_pool = scenes_config.get('scenes', [])
        self.history = []
        self.config_scene_names = {s['name'] for s in self.scenes_pool}

    def decide_next_scene(self, active_obs_scenes):
        # Filtra solo le scene della config che esistono davvero in OBS
        valid_scenes = [
            s for s in self.scenes_pool 
            if s['name'] in active_obs_scenes
        ]
        
        if not valid_scenes:
            missing = self.config_scene_names - set(active_obs_scenes)
            return None, {}, f"Scene della config non trovate in OBS: {missing}"

        # v0.1: Generiamo stati mock — in v0.2 saranno da aubio
        mock_energy = random.choice([20, 50, 75, 95])
        mock_mood = "Acid" if mock_energy > 70 else "Peak" if mock_energy > 50 else "Chill"
        mock_peak = "SI" if mock_energy > 85 else "NO"
        
        stats = {
            "energy_level": mock_energy,
            "mood": mock_mood,
            "peak": mock_peak
        }

        # Logica di selezione basata su energia
        if mock_energy > 80:
            target_energy = "high"
        elif mock_energy > 50:
            target_energy = "medium"
        else:
            target_energy = "low"

        candidates = [s for s in valid_scenes if s['energy'] == target_energy]
        
        # Fallback
        if not candidates:
            candidates = valid_scenes

        # Anti-ripetizione immediata
        if len(candidates) > 1 and self.history:
            candidates = [s for s in candidates if s['name'] != self.history[-1]]

        chosen_scene = random.choice(candidates)
        name = chosen_scene['name']
        
        reason = f"Energia {target_energy.upper()} ({mock_energy}% rilevato). Scene: {chosen_scene['tags']}"
        
        self.history.append(name)
        return name, stats, reason
""",

        # 7. pupa.py — ORCHESTRATORE PRINCIPALE
        "pupa/pupa.py": """#!/usr/bin/env python3
import time
import sys
import os
import yaml
from obs_controller import OBSController
from brain import PupaBrain
from logger import PupaLogger

# Calcola la directory dello script per i path relativi
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def load_yaml(filepath):
    filepath = os.path.join(SCRIPT_DIR, filepath)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"[ERRORE] File non trovato: {filepath}")
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f"[ERRORE] YAML malformato: {e}")
        sys.exit(1)

def main():
    print("\\n" + "="*60)
    print("  PUPA VJ BRAIN v0.1 'Heartbeat'")
    print("  Real-time AI for beat-synchronized scene transitions")
    print("="*60 + "\\n")
    
    # Caricamento configurazioni
    config = load_yaml("config.yaml")
    scenes_cfg = load_yaml("scenes.yaml")
    
    # Inizializzazione moduli
    obs_ctrl = OBSController(
        host=config['obs']['host'], 
        port=config['obs']['port'], 
        password=config['obs']['password']
    )
    brain = PupaBrain(scenes_cfg)
    logger = PupaLogger(log_dir=os.path.join(SCRIPT_DIR, "logs"))

    if not obs_ctrl.connect():
        print("[CRITICO] Impossibile avviare PUPA senza connessione a OBS.")
        print("           Verifica: Tools → WebSocket Server (enabled)")
        print("           Password corretta in config.yaml")
        return 1

    loop_interval = config['project']['loop_interval']
    print(f"[PUPA] Loop interval: {loop_interval}s")
    print(f"[PUPA] Scene disponibili nel config: {len(scenes_cfg['scenes'])}")
    print("[PUPA] Engine avviato. Premere CTRL+C per fermare.\\n")

    current_scene = obs_ctrl.get_current_scene() or "Unknown"
    iteration = 0

    try:
        while True:
            iteration += 1
            
            # 1. Chiedi a OBS quale scene ha DAVVERO
            active_scenes = obs_ctrl.get_available_scenes()
            
            # 2. Brain decide la prossima
            next_scene, stats, reason = brain.decide_next_scene(active_scenes)
            
            if next_scene:
                # 3. Switch fisico su OBS
                if obs_ctrl.switch_scene(next_scene):
                    # 4. Log spiegato
                    logger.log_decision(current_scene, next_scene, stats, reason)
                    current_scene = next_scene
                else:
                    print(f"[WARN] Switch a '{next_scene}' fallito (scene non trovata?)")
            else:
                print(f"[WARN] Brain non ha trovato scene valide. Motivo: {reason}")
            
            # Attesa prima del prossimo ciclo
            time.sleep(loop_interval)
            
    except KeyboardInterrupt:
        print("\\n[PUPA] Arresto controllato dello show.")
        return 0
    except Exception as e:
        print(f"\\n[CRITICO] Errore durante l'esecuzione: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        obs_ctrl.disconnect()

if __name__ == "__main__":
    sys.exit(main())
""",

        # 8. README
        "pupa/README.md": """# PUPA v0.1 'Heartbeat'

AI-powered VJ brain che automatizza transizioni tra scene OBS in tempo reale.

## Setup

1. **Installa dipendenze:**
```bash
pip install -r requirements.txt
```

2. **Configura OBS:**
   - Apri OBS → Tools → WebSocket Server
   - Abilita il server WebSocket
   - Nota la password (se presente)

3. **Modifica config.yaml:**
```yaml
obs:
  password: "TUA_PASSWORD_QUI"  # se OBS la richiede
```

4. **Verifica scenes.yaml:**
   - I nomi scene in OBS devono corrispondere ESATTAMENTE
   - Usa `Scena 0` se non l'hai rinominato

5. **Lancia:**
```bash
python3 pupa.py
```

## Log

I log sono salvati in `logs/runtime.log` con:
- Timestamp
- Livello di energia rilevato
- Motivo della transizione
- Tag delle scene

## Roadmap

- **v0.2**: Integrazione aubio per BPM-sync real-time
- **v0.3**: Feedback audio (Waveform, Scale To Sound)
- **v0.4**: MIDI controller input per override manuale
- **v0.5**: Machine learning per "preferenze VJ"
"""
    }

    for path, content in files.items():
        full_path = os.path.join("pupa", path.replace("pupa/", ""))
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"✓ File creato: {path}")

if __name__ == "__main__":
    print("[PUPA] Setup in corso...\n")
    create_structure()
    print()
    create_files()
    print(f"\n{'='*50}")
    print("✓ Setup completato!")
    print(f"{'='*50}")
    print("\nProssimi step:")
    print("1. cd pupa")
    print("2. pip install -r requirements.txt")
    print("3. Modifica config.yaml con la tua password OBS WebSocket")
    print("4. python3 pupa.py")