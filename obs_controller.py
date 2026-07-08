"""
OBS Controller - WebSocket v5.7.3 wrapper
"""

from obsws_python import ReqClient
import time
from debug_logger import debug as debug_log


class OBSController:
    def __init__(self, host="localhost", port=4455, password=""):
        self.host = host
        self.port = port
        self.password = password
        self.client = None
        self.version = None
        self.ws_version = None
        self.scenes = {}
        self.current_scene = None
    
    def connect(self):
        """Connect to OBS WebSocket"""
        self.client = ReqClient(
            host=self.host,
            port=self.port,
            password=self.password,
            timeout=5
        )

        # Fetch OBS version
        info = self.client.get_version()
        self.version = info.obs_version
        self.ws_version = info.rpc_version
        return True
    
    def disconnect(self):
        """Disconnect from OBS"""
        if self.client:
            self.client.disconnect()
    
    def cache_scenes(self):
        """Cache all scene names for fast lookup"""
        response = self.client.get_scene_list()
        
        # Handle obsws_python response structure
        if hasattr(response, 'scenes'):
            scenes_list = response.scenes
        else:
            scenes_list = response.get('scenes', [])
        
        # Build scene dict
        self.scenes = {}
        for scene in scenes_list:
            if isinstance(scene, dict):
                scene_name = scene.get('sceneName') or scene.get('name')
            else:
                scene_name = getattr(scene, 'sceneName', None) or getattr(scene, 'name', None)
            
            if scene_name:
                self.scenes[scene_name] = scene
        
        # Get current scene
        if hasattr(response, 'currentProgramSceneName'):
            self.current_scene = response.currentProgramSceneName
        elif hasattr(response, 'current_program_scene_name'):
            self.current_scene = response.current_program_scene_name
        else:
            self.current_scene = response.get('currentProgramSceneName')
        
        return list(self.scenes.keys())
    
    def get_current_scene(self):
        """Get current program scene name"""
        response = self.client.get_scene_list()
        
        # Handle obsws_python response structure
        if hasattr(response, 'currentProgramSceneName'):
            self.current_scene = response.currentProgramSceneName
        elif hasattr(response, 'current_program_scene_name'):
            self.current_scene = response.current_program_scene_name
        else:
            self.current_scene = response.get('currentProgramSceneName', 'Unknown')
        
        return self.current_scene
    
    def switch_scene(self, scene_name, transition_ms=None, transition_type="Fade"):
        """Switch to scene with custom transition type and duration

        transition_type: deve essere un nome REALMENTE registrato in OBS.
        Verificato via get_scene_transition_list() su questa installazione OBS
        (localizzata in italiano): Stinger, Fade, Taglio, Scivola, Dissolvenza,
        Burn, Displace, Luma Wipe, Move, Blur. "Cut"/"Flash"/"Strobe" NON esistono.

        NOTA STORICA: la versione precedente chiamava
        `set_current_scene_transition_override(...)`, un metodo che NON ESISTE
        in obsws_python (verificato con dir(ReqClient)). Ogni chiamata lanciava
        AttributeError, veniva ingoiata da un except silenzioso, e OBS continuava
        a usare la transizione globale gia' impostata manualmente nell'interfaccia
        (da cui "si vede sempre solo Fade"). Il metodo corretto e' impostare
        la transizione GLOBALE corrente prima di switchare la scena.

        NOTA: transition_ms viene applicato come durata NATIVA della transizione OBS.
        OBS esegue la transizione in modo asincrono (non blocca questo processo).
        """
        if transition_ms is None:
            transition_ms = 0

        # Imposta tipo+durata PRIMA di switchare, cosi' si applica
        # alla transizione che stiamo per innescare (non alla successiva)
        if transition_ms > 0:
            try:
                self.client.set_current_scene_transition(transition_type)
                self.client.set_current_scene_transition_duration(transition_ms)
                debug_log(f"[OBS] APPLICATA: {transition_type} {transition_ms}ms -> {scene_name}")
            except Exception as e:
                print(f"[OBS WARN] Transizione '{transition_type}' non applicabile ({e}); fallback a Fade")
                debug_log(f"[OBS] FALLITA: {transition_type} ({e}) -> fallback Fade")
                try:
                    self.client.set_current_scene_transition("Fade")
                    self.client.set_current_scene_transition_duration(transition_ms)
                except Exception as e2:
                    print(f"[OBS ERROR] Anche il fallback a Fade e' fallito: {e2}")

        try:
            self.client.set_current_program_scene(scene_name)
        except Exception as e:
            # Non fatale: uno switch fallito (es. nome scena inesistente,
            # scollegamento momentaneo da OBS) non deve mai far crashare
            # l'intero processo durante un live - meglio saltare questo
            # switch e continuare a reagire alla musica sul frame successivo.
            print(f"[OBS ERROR] switch a '{scene_name}' fallito: {e}")
            debug_log(f"[OBS] switch_scene fallito ({scene_name}): {e}")
            return False

        self.current_scene = scene_name
        return True
    
    def get_scene_names(self):
        """Return list of all cached scene names"""
        return list(self.scenes.keys())

    def get_transition_list(self):
        """Lista dei nomi di transizione REALMENTE registrati in questa
        installazione OBS - dipende da lingua e plugin installati (es.
        Shadertastic per Burn/Displace/Blur). Usata da validate_scenes() in
        brain.py per adattare la configurazione a cosa esiste davvero."""
        try:
            resp = self.client.get_scene_transition_list()
            names = []
            for t in resp.transitions:
                if isinstance(t, dict):
                    names.append(t.get('transitionName'))
                else:
                    names.append(getattr(t, 'transition_name', None))
            return [n for n in names if n]
        except Exception as e:
            debug_log(f"[OBS] get_transition_list fallito: {e}")
            return []

    def flash_scene(self, scene_name):
        """Lampeggia una scena disabilitando e riabilitando tutti i suoi
        scene item - usato in MODALITA' DEGENERATA (vedi brain.validate_scenes)
        quando OBS ha una sola scena disponibile e non esiste un vero A/B da
        alternare: da' comunque un accento visivo sui kick invece di restare
        completamente statico. Bloccante per ~80ms (accettabile: e' un caso
        limite raro, non il funzionamento normale)."""
        try:
            resp = self.client.get_scene_item_list(scene_name)
            items = resp.scene_items
            item_ids = []
            for it in items:
                if isinstance(it, dict):
                    item_ids.append(it.get('sceneItemId'))
                else:
                    item_ids.append(getattr(it, 'scene_item_id', None))
            item_ids = [i for i in item_ids if i is not None]

            for item_id in item_ids:
                self.client.set_scene_item_enabled(scene_name, item_id, False)
            time.sleep(0.08)
            for item_id in item_ids:
                self.client.set_scene_item_enabled(scene_name, item_id, True)
        except Exception as e:
            debug_log(f"[OBS] flash_scene fallito ({scene_name}): {e}")

    def get_canvas_size(self):
        """Risoluzione del canvas OBS (base_width/base_height), usata come
        riferimento affidabile per il "100%" delle sorgenti con bounds fisso —
        piu' robusto di rileggere boundsWidth/boundsHeight dal vivo, che
        potrebbero essere gia' rimpiccioliti da un avvio precedente non
        terminato pulitamente (es. crash a meta' di uno scale-to-sound)."""
        try:
            v = self.client.get_video_settings()
            return (v.base_width, v.base_height)
        except Exception as e:
            debug_log(f"[OBS] get_canvas_size fallito: {e}")
            return (1920, 1080)

    def get_source_item_id(self, scene_name, source_name):
        """Risolve lo sceneItemId di una sorgente dentro una scena (per set_source_scale)"""
        try:
            resp = self.client.get_scene_item_id(scene_name, source_name)
            return getattr(resp, 'scene_item_id', None)
        except Exception as e:
            debug_log(f"[OBS] get_source_item_id fallito ({scene_name}/{source_name}): {e}")
            return None

    def get_source_base_size(self, scene_name, item_id):
        """Legge boundsType/boundsWidth/boundsHeight/posizione correnti di uno
        scene item, alla dimensione di riposo (100%, va chiamato UNA VOLTA
        all'avvio prima di iniziare a scalare).

        Necessario perche' alcune sorgenti usano un "bounds" fisso
        (es. OBS_BOUNDS_SCALE_INNER): in quel caso OBS ricalcola la scala
        per riempire il riquadro, IGNORANDO scaleX/scaleY — bisogna invece
        ridimensionare il riquadro stesso (boundsWidth/boundsHeight). La
        posizione base serve per poter RICENTRARE il riquadro quando si
        rimpicciolisce (altrimenti resta ancorato all'angolo top-left e
        sembra "scivolare" li' invece di restare centrato)."""
        try:
            t = self.client.get_scene_item_transform(scene_name, item_id)
            tr = t.scene_item_transform
            return {
                "bounds_type": tr.get("boundsType", "OBS_BOUNDS_NONE"),
                "bounds_width": tr.get("boundsWidth", 0.0),
                "bounds_height": tr.get("boundsHeight", 0.0),
                "position_x": tr.get("positionX", 0.0),
                "position_y": tr.get("positionY", 0.0),
            }
        except Exception as e:
            debug_log(f"[OBS] get_source_base_size fallito ({scene_name}/{item_id}): {e}")
            return {"bounds_type": "OBS_BOUNDS_NONE", "bounds_width": 0.0, "bounds_height": 0.0,
                     "position_x": 0.0, "position_y": 0.0}

    def set_source_scale(self, scene_name, item_id, scale, base_bounds=None):
        """Imposta la dimensione di uno scene item in base a `scale` (0.0-1.0+),
        scale-to-sound "fatto in casa" via API WebSocket ufficiale (non un plugin
        di terze parti — non si rompe con gli aggiornamenti OBS).

        Imposta SIA scaleX/scaleY SIA boundsWidth/boundsHeight nella stessa
        chiamata: per le sorgenti senza bounds (OBS_BOUNDS_NONE) conta solo
        scaleX/scaleY; per quelle CON bounds attivi (es. OBS_BOUNDS_SCALE_INNER)
        OBS ignora scaleX/scaleY e ricalcola la scala per riempire il riquadro,
        quindi serve ridimensionare boundsWidth/boundsHeight — inviarli entrambi
        e' innocuo nel primo caso ed efficace nel secondo.

        Quando la sorgente ha bounds attivi, RICALCOLA anche positionX/positionY
        cosi' il riquadro resta centrato sul suo centro originale mentre si
        rimpicciolisce, invece di restare ancorato all'angolo top-left
        (comportamento di default di OBS che faceva "scivolare" la sorgente
        in alto a sinistra invece di farla pulsare al centro).

        base_bounds: dict da get_source_base_size(), stato a scale=1.0.
        Se assente, scala solo scaleX/scaleY (nessun ricentraggio).
        """
        transform = {"scaleX": scale, "scaleY": scale}
        if base_bounds and base_bounds.get("bounds_type", "OBS_BOUNDS_NONE") != "OBS_BOUNDS_NONE":
            new_w = max(1.0, base_bounds["bounds_width"] * scale)
            new_h = max(1.0, base_bounds["bounds_height"] * scale)
            transform["boundsWidth"] = new_w
            transform["boundsHeight"] = new_h

            center_x = base_bounds["position_x"] + base_bounds["bounds_width"] / 2
            center_y = base_bounds["position_y"] + base_bounds["bounds_height"] / 2
            transform["positionX"] = center_x - new_w / 2
            transform["positionY"] = center_y - new_h / 2

        try:
            self.client.set_scene_item_transform(scene_name, item_id, transform)
            # BUG OBS/WebSocket confermato empiricamente: SetSceneItemTransform
            # aggiorna correttamente i dati (confermato da get_scene_item_transform
            # subito dopo), ma NON invalida la cache di rendering dell'item — il
            # cambiamento resta invisibile a schermo finche' qualcos'altro non
            # forza un re-render. Un editing manuale nel pannello Trasformazione
            # di OBS lo fa scattare correttamente; via WebSocket no. Workaround
            # verificato dal vivo: un rapido toggle disable->enable forza OBS a
            # ridisegnare l'item, rivelando il nuovo transform senza sfarfallio
            # percepibile (nessuna pausa tra le due chiamate).
            self.client.set_scene_item_enabled(scene_name, item_id, False)
            self.client.set_scene_item_enabled(scene_name, item_id, True)
        except Exception as e:
            debug_log(f"[OBS] set_source_scale fallito ({scene_name}/{item_id}): {e}")