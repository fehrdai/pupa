"""
Gestione della riproduzione dei video delle scene _B "esclusive di un livello CALM"
(scenes_config.yaml, chiave `calm_scenes`).

PERCHE' (2026-09-20, misurato sul rig Linux): OBS decodifica TUTTI i suoi video in continuo, anche
quelli di scene non in onda ("Chiudi il file quando inattiva" non li ferma: stato PLAYING con il cursore
che avanza). Con i 4 video nuovi di CALM 3 in piu' la CPU di OBS e' passata da ~240% a ~380% su 4 core,
il render da ~11 ms a 42-76 ms (limite a 24 fps: 41.7 ms) e i video andavano a scatti. Fermare un video
mettendolo in PAUSA (TriggerMediaInputAction/PAUSE) ne azzera il costo (CPU 130% -> 42% con 4 video nuovi
in pausa) e con PLAY riprende esattamente dal punto in cui era, anche a scena in onda o fuori onda.
ATTENZIONE - provato sul rig: STOP + PLAY NON funziona (stato "PLAYING" ma cursore fermo e immagine nera);
dopo uno STOP solo un RESTART a scena GIA' in onda riporta il video (e riparte da 0). Per questo qui si usa
PAUSE, mai STOP; il RESTART resta solo come rete di sicurezza se un video risulta STOPPED all'arrivo in onda.

COSA FA: tiene in PLAY solo i video della scena _B corrente della coppia (se e' una di quelle gestite) e
mette in PAUSA gli altri gestiti. La _B e' scelta all'inizio della coppia e il primo passaggio A->B avviene dopo la
permanenza minima (secondi), quindi il video ha tempo di partire. Non tocca i video delle altre scene.
La PAUSA e' ritardata (PAUSE_DELAY_S) perche' la _B appena lasciata potrebbe essere ancora a schermo.
Nessuna scena gestita = nessun effetto (es. su Windows senza calm_scenes).
"""
from debug_logger import debug as debug_log

PLAY = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PLAY"
PAUSE = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_PAUSE"
RESTART = "OBS_WEBSOCKET_MEDIA_INPUT_ACTION_RESTART"
PAUSE_DELAY_S = 15.0   # attesa prima di mettere in pausa il video della _B appena lasciata
RETRY_EVERY_S = 5.0    # se un comando e' fallito, riprova con questo intervallo


class MediaGuard:
    def __init__(self, client, managed):
        """client: ReqClient di obsws_python (o equivalente con trigger_media_input_action).
        managed: {nome_scena: [nomi dei video (ffmpeg_source) della scena]}."""
        self.client = client
        self.managed = {s: list(m) for s, m in (managed or {}).items() if m}
        self.all_media = sorted({m for ms in self.managed.values() for m in ms})
        self.enabled = bool(self.all_media)
        self.playing = set()       # cosa credo stia riproducendo tra i gestiti
        self.pending_pause = {}     # video -> istante (time) in cui fermarlo
        self.current_b = None
        self._retry_at = 0.0

    def _act(self, media, action):
        try:
            self.client.trigger_media_input_action(media, action)
            return True
        except Exception as e:
            debug_log(f"[MEDIA] {action.rsplit('_', 1)[-1]} {media} fallito: {e}")
            return False

    def on_switched(self, scene):
        """Da chiamare DOPO ogni cambio di scena in OBS. Rete di sicurezza: se la scena appena andata in onda e'
        una gestita e un suo video risulta STOPPED/ENDED (es. fermato a mano), il PLAY non lo farebbe partire
        (vedi nota sopra): serve un RESTART a scena in onda. Costa 1 richiesta solo per le scene gestite."""
        if not self.enabled or scene not in self.managed:
            return
        for m in self.managed[scene]:
            try:
                state = self.client.get_media_input_status(m).media_state.replace("OBS_MEDIA_STATE_", "")
            except Exception as e:
                debug_log(f"[MEDIA] stato di {m} non leggibile: {e}")
                continue
            if state in ("STOPPED", "ENDED", "NONE"):
                debug_log(f"[MEDIA] {m} era {state} all'arrivo in onda: RESTART")
                if self._act(m, RESTART):
                    self.playing.add(m)
                    self.pending_pause.pop(m, None)

    def _desired(self, b_scene):
        return set(self.managed.get(b_scene, []))

    def startup(self, b_scene, now):
        """All'avvio: mette in pausa tutti i gestiti tranne quelli della _B corrente (che parte)."""
        if not self.enabled:
            return
        desired = self._desired(b_scene)
        self.playing = set()
        self.pending_pause = {}
        ok = True
        for m in self.all_media:
            if m in desired:
                if self._act(m, PLAY):
                    self.playing.add(m)
                else:
                    ok = False
            else:
                ok &= self._act(m, PAUSE)
        self.current_b = b_scene
        self._retry_at = 0.0 if ok else now + RETRY_EVERY_S
        debug_log(f"[MEDIA] avvio: in PLAY {sorted(self.playing)}, in pausa {sorted(set(self.all_media) - self.playing)}")

    def tick(self, b_scene, now):
        """Da chiamare a ogni giro del loop (costo trascurabile se non cambia nulla)."""
        if not self.enabled:
            return
        if b_scene != self.current_b:
            desired = self._desired(b_scene)
            ok = True
            for m in desired:
                self.pending_pause.pop(m, None)             # rientra in gioco: annulla la PAUSA
                if m not in self.playing:
                    if self._act(m, PLAY):
                        self.playing.add(m)
                    else:
                        ok = False
            for m in list(self.playing):
                if m not in desired and m not in self.pending_pause:
                    self.pending_pause[m] = now + PAUSE_DELAY_S
            self.current_b = b_scene
            if not ok:
                self._retry_at = now + RETRY_EVERY_S
            debug_log(f"[MEDIA] _B={b_scene}: in PLAY {sorted(self.playing)}, PAUSA in attesa {sorted(self.pending_pause)}")
        # PAUSE scadute
        for m, t in list(self.pending_pause.items()):
            if now >= t and m not in self._desired(self.current_b):
                if self._act(m, PAUSE):
                    self.playing.discard(m)
                    del self.pending_pause[m]
                else:
                    self.pending_pause[m] = now + RETRY_EVERY_S
        # ritenta i PLAY mancanti
        if self._retry_at and now >= self._retry_at:
            missing = [m for m in self._desired(self.current_b) if m not in self.playing]
            for m in missing:
                if self._act(m, PLAY):
                    self.playing.add(m)
            self._retry_at = 0.0 if not [m for m in self._desired(self.current_b) if m not in self.playing] else now + RETRY_EVERY_S
