import obsws_python as obs

class OBSController:
    def __init__(self, host, port, password):
        self.host = host
        self.port = port
        self.password = password
        self.client = None

    def connect(self):
        try:
            self.client = obs.ReqClient(host=self.host, port=self.port, password=self.password)
            v = self.client.get_version()
            print(f"[OBS] Connesso! OBS v{v.obs_version} (WS v{v.obs_web_socket_version})")
            return True
        except Exception as e:
            print(f"[OBS] Errore connessione: {e}")
            return False

    def get_available_scenes(self):
        if not self.client: return []
        try:
            response = self.client.get_scene_list()
            return [s['sceneName'] for s in response.scenes]
        except Exception as e:
            print(f"[OBS] Errore lettura scene: {e}")
            return []

    def get_current_scene(self):
        if not self.client: return None
        try:
            response = self.client.get_current_program_scene()
            return response.current_program_scene_name
        except Exception as e:
            print(f"[OBS] Errore lettura scena attiva: {e}")
            return None

    def switch_scene(self, scene_name):
        if not self.client: return False
        try:
            self.client.set_current_program_scene(scene_name)
            return True
        except Exception as e:
            print(f"[OBS] Errore switch '{scene_name}': {e}")
            return False

    def disconnect(self):
        pass