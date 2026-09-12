"""
PUPA ESIBIZIONE - bozza di struttura (2026-08-02), NON FUNZIONANTE.

Script gemello di pupa.py per lo stato "Esibizione": voce+musica dal vivo,
contenuto SOLO slide (una OBS scene collection dedicata, gia' filtrata a
monte - scene_discovery.py funziona identico, non serve nessun filtro qui),
niente rotazione identita' del DJset. Riusa i moduli generici gia' esistenti
(obs_controller/qlc_controller/hotkey_controller/audio_analyzer) invece di
duplicarli - vedi memoria/PUPA_DEVELOPMENT_LOG.md per il ragionamento
architetturale completo (2026-08-02).

QUESTO FILE E' UN'IMPALCATURA PER DISCUTERE LA STRUTTURA, non codice da
lanciare - i punti dove il routing musica/voce->luci/monitor/slide non e'
ancora deciso sono segnati esplicitamente con "TODO DECIDERE".

2026-08-13: spostato in exhibition/ (cartella dedicata, separata dalla root
di pupa.py) su richiesta esplicita dell'operatore - lo sviluppo di Esibizione
va tenuto separato da quello del DJset (log, debug, e in futuro sessione di
lavoro) pur riusando gli stessi moduli condivisi. Il path-insert sotto serve
solo a questo: importare i moduli condivisi (obs_controller ecc.) che vivono
nella cartella padre, senza copiarli qui dentro.
"""
import os
import sys
import time
import threading

import numpy as np
import sounddevice as sd

# I moduli condivisi (obs_controller, qlc_controller, brain, ...) vivono
# nella cartella padre pupa/, non qui dentro exhibition/ - questo script
# resta un file "gemello" che li riusa, non li duplica (vedi discussione
# 2026-08-13 in project_pupa_esibizione nella memoria).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from obs_controller import OBSController
from qlc_controller import QLCController
from audio_analyzer import AudioAnalyzer
from hotkey_controller import MultiLevelControl, BinaryControl
import scene_discovery
from debug_logger import setup_debug_logger
from shutdown_helpers import shutdown_step, spegni_luci_qlc, obs_a_nero

# Logger separato da quello del DJset - name diverso (altrimenti condivide
# lo stesso logger/file, vedi debug_logger.py) E cartella propria
# exhibition/logs/, cosi' un test Esibizione non si mischia ne' rischia di
# far ruotare via i log di un DJset dello stesso giorno (o viceversa).
_debug_logger = setup_debug_logger(
    name="pupa_exhibition_debug",
    log_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"),
)
debug_log = _debug_logger.debug

# TODO: probabilmente lo stesso secrets_local.py del DJset (stessa macchina,
# stesso OBS/QLC+) - ma con 2 device audio da configurare invece di 1 (vedi
# sotto). Da capire se conviene un secrets_local_exhibition.py separato o
# nuove costanti nello stesso file. Resta nella cartella padre per ora
# (import invariato) proprio perche' non ancora deciso se va duplicato.
from secrets_local import OBS_HOST, OBS_PORT, OBS_PASSWORD

# Device della musica (stesso ruolo di AUDIO_DEVICE_NAME/PULSE_SOURCE del
# DJset) - stessa interfaccia gia' in uso.
MUSIC_DEVICE_NAME = "pulse"  # placeholder, da confermare

# Device della voce - iConnex dedicato, via il mixerino a parte (vedi
# memoria: routing fisico deciso 2026-08-02). Nome PipeWire/sounddevice
# esatto da scoprire quando l'iConnex e' collegato per davvero (stesso
# procedimento usato stasera per la UCA222 - lsusb + pactl list sources).
VOICE_DEVICE_NAME = None  # placeholder, da riempire

# QLC+ - stessi canali/fixture del DJset (stesso rig fisico) - riuso diretto
# delle costanti, non reinventarle. Da importare da pupa.py o spostarle in
# un modulo condiviso (oggi vivono dentro pupa.py, non sono importabili
# pulite da qui) - vedi nota in fondo al file.
# from pupa import QLC_CHANNEL_F1_MASTER, ... (NON FUNZIONA COSI', vedi nota)


class VoiceEnvelopeFollower:
    """Segue l'inviluppo di ampiezza della voce (RMS per blocco, con
    attack/release esponenziali in stile compressore/gate) - NON
    rilevamento di kick/onset: la voce cantata non si presta bene a quel
    tipo di rilevamento (vedi ricerca 2026-08-01 in
    project_future_milestones.md - articolazione incoerente, timbro
    dipendente dal cantante, letteratura conferma che l'onset detection sul
    canto e' un problema mal posto). Espone un valore continuo 0.0-1.0 e un
    booleano "sopra soglia" per l'eventuale modalita' a soglia (operatore
    ha chiesto entrambe le modalita' disponibili, selezionabili).

    Stesso principio di sd.InputStream + callback gia' usato da
    AudioAnalyzer, ma volutamente MOLTO piu' leggero - non serve la
    macchina a stati energia/kick/BPM del DJset per questo."""

    def __init__(self, device, samplerate=44100, blocksize=1024,
                 attack_s=0.05, release_s=0.4, threshold=0.15,
                 gain=5.0):
        """attack_s/release_s: costanti di tempo (secondi) per la salita/
        discesa dell'inviluppo - valori di PARTENZA, da tarare dal vivo
        come tutto il resto in questo progetto (vedi CALM_MULTIPLIERS,
        AGC, ecc. in brain.py/audio_analyzer.py per lo stesso pattern).
        Attack corto = reattivo/nervoso, release lungo = "respiro" morbido
        dopo che la voce si ferma - la combinazione giusta per
        l'atmosfera cinematica di Esibizione va trovata ascoltando, non
        indovinata qui.
        gain: moltiplicatore grezzo pre-normalizzazione, stesso ruolo di
        AUDIO_INPUT_GAIN_PCT ma lato software qui - da tarare col livello
        REALE che arriva dall'iConnex via il mixerino (vedi test col
        segnale sintetico prima di avere un microfono vero)."""
        self.device = device
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.threshold = threshold
        self.gain = gain

        # Coefficienti di smoothing esponenziale per-blocco, dalla costante
        # di tempo in secondi - stessa formula standard di un envelope
        # follower/compressore analogico.
        block_dt = blocksize / samplerate
        self._attack_coeff = 1.0 - np.exp(-block_dt / max(attack_s, 1e-6))
        self._release_coeff = 1.0 - np.exp(-block_dt / max(release_s, 1e-6))

        self._envelope = 0.0
        self._lock = threading.Lock()
        self.stream = None
        self.running = False

    def start(self):
        self.running = True
        self.stream = sd.InputStream(
            device=self.device,
            channels=1,  # mono - un mic/mandata voce dedicata, non serve stereo
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            callback=self._callback,
            latency='low',
        )
        self.stream.start()

    def stop(self):
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()

    def _callback(self, indata, frames, time_info, status):
        if status:
            debug_log(f"[VOCE] STATUS/XRUN: {status}")
            return
        audio = indata[:, 0] if indata.ndim > 1 else indata.flatten()
        if len(audio) == 0:
            return
        rms = float(np.sqrt(np.mean(audio ** 2)))
        target = min(1.0, rms * self.gain)
        with self._lock:
            coeff = self._attack_coeff if target > self._envelope else self._release_coeff
            self._envelope += (target - self._envelope) * coeff

    def get_level(self):
        """Livello corrente dell'inviluppo, 0.0-1.0."""
        with self._lock:
            return self._envelope

    def is_above_threshold(self):
        return self.get_level() >= self.threshold


def main():
    print("=" * 70)
    print("  PUPA ESIBIZIONE - bozza di struttura, non funzionante")
    print("=" * 70)

    obs = OBSController(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD)
    if not obs.connect():
        return
    print(f"[OBS] Connesso! OBS v{obs.version}")

    qlc = QLCController()
    qlc.connect()

    scenes = obs.cache_scenes()
    # Collection dedicata = gia' solo slide, scene_discovery non ha bisogno
    # di nessun filtro speciale - stesso identico meccanismo del DJset.
    scene_a_list = scene_discovery.discover_a_scenes(scenes)
    scene_b_list = scene_discovery.discover_b_scenes(scenes)
    print(f"[PUPA ESIBIZIONE] {len(scene_a_list)} scene_A, {len(scene_b_list)} scene_B trovate")

    music = AudioAnalyzer(device=MUSIC_DEVICE_NAME)
    music.start()

    voice = VoiceEnvelopeFollower(device=VOICE_DEVICE_NAME)
    voice.start()

    # TODO: quali hotkey servono davvero qui dentro? Ipotesi da discutere:
    #   - toggle continuo/soglia per la risposta voce (chiesto dall'operatore)
    #   - avanzamento manuale "fase" dello spettacolo (colore/atmosfera)
    #   - uno shutdown pulito stile F12 (probabilmente si', riusa lo stesso
    #     principio - vedi nota sotto)
    # Non ancora istanziati, struttura del loop di poll sarebbe identica a
    # quella di pupa.py (hotkey_controller.py e' gia' pronto per questo).

    try:
        while True:
            current_time = time.time()

            music_data = music.get_metrics()
            voice_level = voice.get_level()
            voice_above = voice.is_above_threshold()

            # TODO DECIDERE (operatore, 2026-08-02: "non ho ancora le idee
            # chiare, potrebbe essere un mix"): cosa avanza le slide -
            # la musica (kick, come il DJset - riuso diretto del
            # meccanismo SLIDESHOW_ADVANCE_BEATS gia' esistente), la voce,
            # o un mix (es. musica per il ritmo di base, voce come accento
            # extra)? Lasciato aperto apposta.

            # TODO DECIDERE: routing luci - musica, voce, o entrambe
            # miscelate? (es. colore/base dalla musica, intensita' extra
            # dalla voce - o viceversa). L'inviluppo voce (voice_level,
            # continuo) e la soglia (voice_above) sono ENTRAMBI gia'
            # disponibili qui per qualunque combinazione si scelga.

            # TODO DECIDERE: routing monitor - esempio dell'operatore "un
            # uscita monitor linkata alla voce" (es. si accende/mostra
            # contenuto solo mentre si canta, effetto spot) - stesso
            # meccanismo di forced_mode/solo_monitor del DJset ma pilotato
            # in continuo da voice_level invece che da un hotkey statico.

            time.sleep(0.05)  # ~20Hz, stesso ritmo del loop DJset

    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C ricevuto")
    finally:
        print("[PUPA ESIBIZIONE] Arresto pulito.")
        # Cascata condivisa con pupa.py - vedi shutdown_helpers.py (estratta
        # 2026-08-14, sblocca il TODO che stava qui prima). OBS a nero e'
        # gia' cablabile per davvero: "black_color" e' il nome fallback
        # obbligatorio in QUALSIASI collection OBS che segue la convenzione
        # PUPA (vedi CLAUDE.md), quindi valido anche prima che questa
        # collection dedicata esista per davvero. obs_a_nero() accetta
        # window_manager=None e una tupla vuota di monitor_off_ids senza
        # errori (nessuna finestra da forzare in primo piano finche' quella
        # parte non e' cablata - vedi TODO sotto).
        #
        # TODO: spegni_luci_qlc(qlc, <canali>) non ancora chiamata - manca
        # ancora una tupla di canali DMX per questa collection. Probabilmente
        # identica a pupa.py's ALL_LIGHT_CHANNELS (stesso rig fisico), ma non
        # ancora confermato/copiato - non invocarla con una tupla vuota nel
        # frattempo, stamperebbe "[QLC] Fari spenti." senza aver spento nulla
        # davvero (fuorviante nei log).
        # TODO: window_manager/monitor_off_ids non ancora istanziati qui -
        # vedi project_pupa_esibizione.md, decisione 2026-08-14 "controllo
        # completo 2 uscite" - da cablare quando si arriva a quella parte del
        # loop principale (vedi anche TODO DECIDERE su routing monitor sopra).
        shutdown_step("OBS a nero", lambda: obs_a_nero(
            obs, "black_color", (), None, debug_log
        ))
        shutdown_step("stop voce", voice.stop)
        shutdown_step("stop musica", music.stop)
        shutdown_step("disconnetti OBS", obs.disconnect)


if __name__ == "__main__":
    main()
