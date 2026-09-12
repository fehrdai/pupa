"""
Cascata di arresto pulito, condivisa tra pupa.py e i suoi script gemelli
(exhibition/pupa_exhibition.py, slideshow/pupa_slideshow.py).

Estratta da pupa.py il 2026-08-14 (era codice annidato dentro il finally di
main(), non importabile) - vedi memoria/project_pupa_esibizione.md per il
ragionamento completo. Estrazione MECCANICA di logica gia' live-verificata,
non una riscrittura: stesso comportamento, solo parametrizzato invece di
chiuso su variabili locali di pupa.py.
"""
import time


def shutdown_step(description, fn):
    """Ogni step di arresto e' protetto A SE' - un secondo Ctrl+C per
    l'impazienza durante la pulizia (es. mentre la chiamata di rete a OBS e'
    in corso) non deve bloccare gli step successivi. 'except Exception' da
    solo NON basta: KeyboardInterrupt eredita da BaseException, non da
    Exception, quindi un nuovo Ctrl+C durante uno step sfuggirebbe e
    interromperebbe tutto il resto - trovato dal vivo 2026-07-30 (OBS non
    passava a nero: il log si fermava a meta' della chiamata di
    switch_scene, il resto del finally non veniva mai raggiunto)."""
    try:
        fn()
    except BaseException as e:
        print(f"[SHUTDOWN] Step di arresto '{description}' interrotto/fallito: {e}")


def spegni_luci_qlc(qlc, light_channels):
    """Senza questo i fari restano accesi/a meta' polso con l'ultimo valore
    inviato, dato che QLC+ non ha un default "torna a 0" da solo. Riconnette
    attivamente se il socket e' caduto (non solo "se gia' connesso") -
    trovato dal vivo 2026-07-29: un disconnect transitorio proprio nel
    momento dello stop faceva saltare lo spegnimento in silenzio."""
    if qlc.sock is None:
        qlc.connect()
    if qlc.sock is not None:
        for ch in light_channels:
            qlc.set_channel(ch, 0)
        print("[QLC] Fari spenti.")
    else:
        print("[QLC] Impossibile spegnere i fari (QLC+ non raggiungibile).")


def obs_a_nero(obs, black_scene_name, monitor_off_ids, window_manager, debug_log, transition_ms=50):
    """Monitor a nero all'arresto (richiesta operatore: fermare PUPA deve
    fermare anche i software collegati). transition_ms=50 (non 1: OBS
    rifiuta SetCurrentSceneTransitionDuration sotto i 50ms, errore codice
    402 - trovato dal vivo 2026-07-30 leggendo debug.log, non a intuito) -
    resta comunque quasi istantaneo.

    2026-07-30 (stessa sera, dopo un test dubstep): l'operatore ha visto OBS
    restare sull'ultima scena viva nonostante il log mostrasse "[OBS]
    APPLICATA: Taglio 50ms -> <scena>" - scoperto rileggendo
    obs_controller.py che quella riga di log scatta subito dopo aver
    impostato tipo/durata transizione, PRIMA della vera chiamata
    set_current_program_scene() - "APPLICATA" non ha MAI confermato che lo
    switch sia arrivato a destinazione, solo che la transizione era stata
    configurata. Fix: verificare DAVVERO con get_current_scene() dopo il
    sleep, e ritentare una volta se non e' quella attesa, invece di fidarsi
    del solo "nessuna eccezione sollevata".

    monitor_off_ids: iterable di window-handle (puo' contenere None, filtrati
    qui) delle finestre Projector gia' bloccate su nero - forzate in primo
    piano ESPLICITAMENTE per ogni uscita, invece di fidarsi che la finestra
    giusta sia gia' quella davanti. 2026-07-30: l'operatore ha visto lo
    switch "confermato" nel log ma il monitor fisico non e' andato a nero
    comunque - root cause reale: sull'alternanza monitor a stacking (vedi
    window_manager.py) ogni uscita ha 2 Proiettori GIA' APERTI sovrapposti
    (uno segue il Programma, l'altro e' bloccato in permanenza sulla scena
    nera) - switchare il Programma cambia solo cosa mostra la finestra "on",
    ma se al momento dello stop era in primo piano quella finestra e non si
    ridisegna in tempo (o resta davanti per qualunque motivo), il monitor
    fisico resta sull'ultimo frame invece di andare a nero, indipendentemente
    da quanto sopra sia davvero riuscito."""
    obs.switch_scene(black_scene_name, transition_ms=transition_ms, transition_type="Taglio")
    # Margine prima di verificare: la richiesta WebSocket non blocca fino
    # alla conferma di OBS - senza pausa la lettura successiva arriverebbe
    # troppo presto anche a switch riuscito.
    time.sleep(0.3)
    actual_scene = obs.get_current_scene()
    if actual_scene != black_scene_name:
        debug_log(f"[OBS] switch a nero non confermato (scena reale='{actual_scene}') - ritento")
        obs.switch_scene(black_scene_name, transition_ms=transition_ms, transition_type="Taglio")
        time.sleep(0.3)
        actual_scene = obs.get_current_scene()

    if actual_scene == black_scene_name:
        print(f"[OBS] {black_scene_name} in programma (confermato).")
        debug_log(f"[OBS] {black_scene_name} confermato in programma dopo switch")
    else:
        print(f"[OBS] ATTENZIONE: switch a nero NON confermato - scena reale rimasta '{actual_scene}'")
        debug_log(f"[OBS] switch a nero NON confermato dopo retry - scena reale='{actual_scene}'")

    if window_manager is not None:
        for off_id in monitor_off_ids:
            if off_id is not None:
                window_manager.activate(off_id)
