"""
PUPA SLIDESHOW (PU.SL) - bozza di struttura (2026-08-14), NON FUNZIONANTE.

Script gemello di pupa.py per lo stato "Music Slideshow": mixer generico di
immagini a tempo di musica, per occasioni diverse da quella specifica di
Esibizione (vedi exhibition/pupa_exhibition.py) - qui niente voce, niente
vincolo di visual restrained, deve sincronizzarsi forte col beat. Riusa i
moduli generici gia' esistenti (obs_controller/audio_analyzer/scene_discovery/
shutdown_helpers) invece di duplicarli - stesso principio architetturale gia'
applicato a Esibizione, vedi memoria/project_pupa_slideshow.md per il
ragionamento completo (design deciso 2026-08-13/14, non ancora implementato
prima d'ora).

QUESTO FILE E' UN'IMPALCATURA PER DISCUTERE LA STRUTTURA, non codice da
lanciare - i punti non ancora decisi sono segnati esplicitamente con
"TODO DECIDERE".

Meccanismo di avanzamento: NON reinventato da zero. pupa.py fa gia' avanzare
sorgenti slideshow a tempo di beat da mesi, dal vivo (vedi pupa.py ~L169-200
e ~L975-998, SLIDESHOW_ADVANCE_BEATS/SLIDESHOW_TRANSITION_SPEED_MS) -
riusiamo la STESSA chiamata (trigger_hotkey_by_name("SlideShow.NextSlide",
contextName=<sorgente>), gia' provata su questo esatto pattern d'uso: una
sola sorgente slideshow attiva per tick) ma NON le tabelle scalate per
brain.State di quel meccanismo - quelle sono legate alla macchina a stati
energetica del DJset (INTRO/GROOVE/DROP/...), che PU.SL con ogni probabilita'
non usera' (stesso ragionamento gia' fatto per Esibizione: non riusare il
decision engine di brain.py per uno stato che non ne ha bisogno). PU.SL ha
una propria costante fissa, non scalata sull'energia.
"""
import os
import sys
import time

# I moduli condivisi (obs_controller, audio_analyzer, scene_discovery, ...)
# vivono nella cartella padre pupa/, non qui dentro slideshow/ - stesso
# pattern di path-insert gia' usato da exhibition/pupa_exhibition.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from obs_controller import OBSController
from audio_analyzer import AudioAnalyzer
import scene_discovery
from debug_logger import setup_debug_logger
from shutdown_helpers import shutdown_step, spegni_luci_qlc, obs_a_nero

# Logger separato dal DJset e da Esibizione - name diverso (altrimenti
# condivide lo stesso logger/file, vedi debug_logger.py) e cartella propria
# slideshow/logs/.
_debug_logger = setup_debug_logger(
    name="pupa_slideshow_debug",
    log_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs"),
)
debug_log = _debug_logger.debug

# Stessa macchina/stesso OBS del DJset - credenziali condivise, nessuna
# ragione di duplicarle (a differenza di Esibizione, qui non c'e' un secondo
# device audio dedicato alla voce che potrebbe far cambiare idea).
from secrets_local import OBS_HOST, OBS_PORT, OBS_PASSWORD

# TODO DECIDERE: stesso device audio del DJset (musica di sala, un solo
# input) o un device dedicato? Verosimilmente lo stesso AUDIO_DEVICE_NAME/
# PULSE_SOURCE gia' in secrets_local.py - non ancora confermato con
# l'operatore, placeholder qui.
MUSIC_DEVICE_NAME = None  # placeholder, da riempire

# AVANZAMENTO A BEAT (design deciso 2026-08-13/14, vedi
# memoria/project_pupa_slideshow.md): gate sui beat come pupa.py, ma costante
# FISSA (non scalata su brain.State - PU.SL non usa la macchina a stati del
# DJset). Valore di partenza scelto per atterrare vicino ai 3-4s indicati
# dall'operatore a un BPM tipico da set (~130-140) - PRIMA cosa da ritarare
# ad orecchio dal vivo, stesso principio di ogni altra costante del progetto.
SLIDE_ADVANCE_BEATS = 8
# Transizione: tipo fisso Slide (stessa scelta gia' fatta dall'operatore per
# le sorgenti slideshow esistenti del DJset, vedi pupa.py). Solo la durata e'
# una costante qui (niente scaling per energia, per lo stesso motivo sopra) -
# partenza nel range gia' live-tarato dal DJset (300-600ms).
SLIDE_TRANSITION_SPEED_MS = 500


def main():
    print("=" * 70)
    print("  PUPA SLIDESHOW - bozza di struttura, non funzionante")
    print("=" * 70)

    obs = OBSController(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD)
    if not obs.connect():
        return
    print(f"[OBS] Connesso! OBS v{obs.version}")

    scenes = obs.cache_scenes()

    # TODO DECIDERE: come vengono individuate le sorgenti slideshow_v2 delle
    # cartelle? Due strade possibili, non ancora scelte con l'operatore:
    #   (a) auto-discovery per CONTENUTO, riusando scene_discovery come fa
    #       gia' pupa.py (slideshow_input_names) - zero config, ma richiede
    #       che ogni cartella sia gia' dentro una scena_A/_B della naming
    #       convention PUPA;
    #   (b) una lista esplicita di nomi sorgente/cartella in config (piu'
    #       controllo esplicito, ma un file di config in piu' da mantenere).
    # Finche' non e' deciso, nessuna sorgente viene davvero pilotata qui.

    # TODO DECIDERE: meccanismo di alternanza tra cartelle (quale sorgente e'
    # "attiva" in un dato momento). Il design gia' fissato in memoria dice
    # solo "randomize OFF per garantire copertura completa per cartella" e
    # "stile scene_A/scene_B" - ma la logica di SELEZIONE (quando passare da
    # una cartella all'altra: a tempo, a comando, in sequenza fissa) non e'
    # ancora stata discussa. Nessuna macchina a stati istanziata qui finche'
    # non e' chiaro se serve davvero (rischio di reinventare un pezzo di
    # brain.py per un bisogno che potrebbe essere molto piu' semplice).

    music = AudioAnalyzer(device=MUSIC_DEVICE_NAME)
    music.start()

    slideshow_last_speed = [None]  # ultima transition_speed INVIATA, stesso pattern anti-set-a-vuoto di pupa.py

    try:
        while True:
            current_time = time.time()

            music_data = music.get_metrics()
            is_beat = music_data.get("is_beat", False)
            beat_count = music_data.get("beat_count", 0)

            # TODO: qui andrebbe il trigger vero - bloccato sui due TODO
            # DECIDERE sopra (quale sorgente e' "quella attiva" in questo
            # momento). Schema gia' pronto, stesso pattern di pupa.py:
            #
            # if active_slideshow_source and is_beat and beat_count % SLIDE_ADVANCE_BEATS == 0:
            #     if SLIDE_TRANSITION_SPEED_MS != slideshow_last_speed[0]:
            #         obs.client.set_input_settings(
            #             active_slideshow_source,
            #             {"transition_speed": SLIDE_TRANSITION_SPEED_MS},
            #             overlay=True,
            #         )
            #         slideshow_last_speed[0] = SLIDE_TRANSITION_SPEED_MS
            #     obs.client.trigger_hotkey_by_name(
            #         "SlideShow.NextSlide", contextName=active_slideshow_source
            #     )

            time.sleep(0.05)  # ~20Hz, stesso ritmo del loop DJset/Esibizione

    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C ricevuto")
    finally:
        print("[PUPA SLIDESHOW] Arresto pulito.")
        # Cascata condivisa - vedi shutdown_helpers.py. QLC+ non ancora
        # istanziato sopra (TODO: PU.SL avra' luci? Non deciso - se si',
        # stesso ALL_LIGHT_CHANNELS del DJset presumibilmente) quindi
        # spegni_luci_qlc non chiamata qui per ora, a differenza di
        # pupa.py/pupa_exhibition.py.
        shutdown_step("OBS a nero", lambda: obs_a_nero(
            obs, "black_color", (), None, debug_log
        ))
        shutdown_step("stop musica", music.stop)
        shutdown_step("disconnetti OBS", obs.disconnect)


if __name__ == "__main__":
    main()
