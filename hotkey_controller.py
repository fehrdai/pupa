"""
hotkey_controller.py - meccanismo puro per gli hotkey OBS (2026-07-30)

Stesso principio di obs_controller.py/qlc_controller.py: qui vive SOLO il
meccanismo di polling/edge-detection, mai la semantica di cosa succede
quando un livello o un toggle cambia - quella resta in pupa.py (dispatch)
e brain.py (decisione), come ovunque in PUPA.

Gli hotkey OBS non esistono come concetto lato WebSocket - il trucco (gia'
in uso da prima di questo file) e' che l'operatore lega un tasto, in OBS
Impostazioni > Hotkey, al Mostra/Nascondi di una source fittizia dentro una
scena di servizio mai mandata in onda (vedi CALM_CONTROL_SCENE in pupa.py).
PUPA poi fa polling della visibilita' di quelle source via WebSocket. La
combinazione "quale tasto fisico" e' invisibile a questo file (vive nella
config di OBS); qui vive solo "quale nome di source significa cosa".

2 tipi di controllo:
  - MultiLevelControl: N source esclusive (0..N-1), pensate per hotkey
    "Mostra" soltanto (mai "Nascondi") - vince la source APPENA accesa
    rispetto al poll precedente, non la piu' alta in assoluto ("vince il
    piu' alto" impediva di scendere di livello - bug reale osservato dal
    vivo con CALM MODE, vedi PUPA_DEVELOPMENT_LOG.md). Autopulizia
    automatica delle altre source rimaste accese, cosi' basta il solo
    hotkey "Mostra" per livello, mai serve un "Nascondi" dedicato.
  - BinaryControl: 1 source, hotkey Mostra/Nascondi = ON/OFF diretto,
    nessuna autopulizia necessaria.

Entrambi espongono poll() che ritorna il nuovo valore SOLO se e' cambiato
rispetto all'ultimo poll (None altrimenti) - il chiamante non deve tenere
un proprio "prev" per fare l'edge-detection, e' gia' fatto qui.
"""


class MultiLevelControl:
    def __init__(self, name, control_scene, level_sources):
        self.name = name
        self.control_scene = control_scene
        self.level_sources = level_sources  # {level: source_name}
        self.item_ids = {}  # {level: item_id}, popolato da resolve()
        self._enabled_prev = set()
        self._active_prev = 0

    def resolve(self, obs, scenes):
        """Risolve gli scene_item_id delle source - va chiamato una volta
        all'avvio. Se la scena/le source non esistono ancora (l'operatore
        non le ha ancora create in OBS), il controllo resta silenziosamente
        disattivato (.active False) invece di far crashare PUPA."""
        if self.control_scene not in scenes:
            print(f"[HOTKEY] {self.name}: scena '{self.control_scene}' non trovata, disattivato")
            return
        for level, source_name in self.level_sources.items():
            item_id = obs.get_source_item_id(self.control_scene, source_name)
            if item_id is not None:
                self.item_ids[level] = item_id
        if not self.item_ids:
            print(f"[HOTKEY] {self.name}: nessuna source trovata in '{self.control_scene}', disattivato")
            return
        print(f"[HOTKEY] {self.name}: {len(self.item_ids)}/{len(self.level_sources)} source trovate in '{self.control_scene}'")
        # Stato di partenza - letto subito cosi' una source gia' accesa da
        # una sessione precedente non genera un falso "livello cambiato" al
        # primo poll.
        self._enabled_prev = set(
            lvl for lvl, item_id in self.item_ids.items()
            if obs.get_scene_item_enabled(self.control_scene, item_id)
        )
        self._active_prev = max(self._enabled_prev) if self._enabled_prev else 0

    @property
    def active(self):
        return bool(self.item_ids)

    def poll(self, obs):
        """Ritorna il nuovo livello attivo se e' cambiato dall'ultimo poll,
        None altrimenti. Fa anche l'autopulizia (spegne le altre source
        rimaste accese)."""
        if not self.item_ids:
            return None
        enabled = set(lvl for lvl in self.item_ids
                      if obs.get_scene_item_enabled(self.control_scene, self.item_ids[lvl]))
        newly_enabled = enabled - self._enabled_prev
        if newly_enabled:
            active_level = max(newly_enabled)
        elif enabled:
            active_level = max(enabled)  # nessuna pressione nuova, stato invariato
        else:
            active_level = 0
        self._enabled_prev = {active_level} if enabled else set()

        # Autopulizia: spegne le altre source rimaste accese.
        for lvl in enabled:
            if lvl != active_level:
                obs.set_scene_item_enabled(self.control_scene, self.item_ids[lvl], False)

        if active_level != self._active_prev:
            self._active_prev = active_level
            return active_level
        return None


class BinaryControl:
    def __init__(self, name, control_scene, source_name):
        self.name = name
        self.control_scene = control_scene
        self.source_name = source_name
        self.item_id = None
        self._enabled_prev = False

    def resolve(self, obs, scenes):
        if self.control_scene in scenes:
            self.item_id = obs.get_source_item_id(self.control_scene, self.source_name)
        if self.item_id is not None:
            print(f"[HOTKEY] {self.name}: source di controllo trovata in '{self.control_scene}'")
            self._enabled_prev = obs.get_scene_item_enabled(self.control_scene, self.item_id)
        else:
            print(f"[HOTKEY] {self.name}: source '{self.source_name}' non trovata, disattivato")

    @property
    def active(self):
        return self.item_id is not None

    def poll(self, obs):
        """Ritorna il nuovo stato bool se e' cambiato dall'ultimo poll,
        None altrimenti."""
        if self.item_id is None:
            return None
        enabled = obs.get_scene_item_enabled(self.control_scene, self.item_id)
        if enabled != self._enabled_prev:
            self._enabled_prev = enabled
            return enabled
        return None
