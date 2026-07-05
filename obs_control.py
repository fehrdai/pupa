import obsws_python as obs

class OBSController:
    def __init__(self, host="localhost", port=4455, password="REDACTED-vedi-secrets_local.py"):
        """
        Gestisce la connessione WebSocket v5 con OBS Studio.
        Modifica la password se ne hai impostata una in OBS (Strumenti -> Impostazioni server WebSockets).
        """
        try:
            self.client = obs.ReqClient(host=host, port=port, password=password)
            print("[🤖 OBS] Connesso al server WebSocket di OBS.")
        except Exception as e:
            print(f"[❌ OBS] Connessione fallita. Controlla se OBS è aperto e il WebSocket è attivo: {e}")
            raise e

    def get_scene_list(self):
        """Recupera la lista di tutte le scene disponibili su OBS."""
        try:
            response = self.client.get_scene_list()
            # Estrae solo i nomi delle scene dalla risposta dell'API
            return [scene['sceneName'] for scene in response.scenes]
        except Exception as e:
            print(f"[❌ OBS] Errore nel recupero della lista scene: {e}")
            return []

    def get_current_scene(self):
        """Recupera il nome della scena attualmente in onda (Program)."""
        try:
            response = self.client.get_current_program_scene()
            return response.current_program_scene_name
        except Exception as e:
            print(f"[❌ OBS] Errore nel recupero della scena corrente: {e}")
            return "NERO_MASTER"

    def switch_scene(self, scene_name):
        """Cambia la scena attiva su OBS."""
        try:
            self.client.set_current_program_scene(scene_name)
        except Exception as e:
            print(f"[❌ OBS] Impossibile cambiare scena su '{scene_name}': {e}")

    def close(self):
        """Chiusura formale della sessione (gestita in automatico da obsws-python)."""
        pass