import datetime
import os

class PupaLogger:
    def __init__(self, log_dir="pupa/logs"):
        self.log_path = os.path.join(log_dir, "runtime.log")

    def log_decision(self, current_scene, next_scene, stats, reason):
        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        
        log_entry = (
            f"{timestamp}\n"
            f"  [Stato] Energia: {stats.get('energy_level')}% | Mood: {stats.get('mood')} | Peak: {stats.get('peak')}\n"
            f"  [Switch] {current_scene} ──> {next_scene}\n"
            f"  [Motivo] {reason}\n"
            f"{'-'*40}\n"
        )
        
        # Stampa a schermo per il VJ
        print(log_entry)
        
        # Salva su file per il giorno dopo
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(log_entry)
