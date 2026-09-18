"""
create_obs_sources.py - crea in OBS le source di controllo NUOVE delle luci
dentro PUPA_Control (PUPA_LUCI, PUPA_LUCI_LIVELLO_1..3), clonando tipo e
impostazioni della source esistente PUPA_STROBE_WHITE (stessa "dummy" degli
altri hotkey). Idempotente: salta quelle che esistono gia'.

    python lights/create_obs_sources.py            # DRY-RUN: mostra cosa farebbe
    python lights/create_obs_sources.py --apply    # crea davvero

ATTENZIONE: scritto il 2026-09-19 senza OBS disponibile - NON ancora provato
contro un OBS reale (usa il dry-run per primo). Dopo la creazione l'operatore
lega i tasti veri in OBS > Impostazioni > Hotkey (Mostra di PUPA_LUCI e di
ciascun LIVELLO; opzionale Nascondi di PUPA_LUCI su un altro tasto).
Stato di creazione: PUPA_LUCI visibile (luci accese di default), i LIVELLI nascosti.
"""
import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(_HERE)
sys.path.insert(0, os.path.dirname(_HERE))
sys.path.insert(0, _HERE)

import lights_config as C
from obs_controller import OBSController
from secrets_local import OBS_HOST, OBS_PORT, OBS_PASSWORD

TEMPLATE = C.SRC_STROBE_WHITE
WANTED = {C.SRC_LIGHTS_ONOFF: True, **{name: False for name in C.SRC_LIGHTS_LEVEL.values()}}  # nome -> visibile alla creazione


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="crea davvero (default: dry-run)")
    args = ap.parse_args()

    obs = OBSController(host=OBS_HOST, port=OBS_PORT, password=OBS_PASSWORD)
    if not obs.connect():
        sys.exit(1)
    inputs = {i["inputName"]: i for i in obs.get_all_inputs()}
    if TEMPLATE not in inputs:
        print(f"[ERRORE] source modello '{TEMPLATE}' non trovata in OBS - non so che tipo creare.")
        sys.exit(1)
    kind = inputs[TEMPLATE]["inputKind"]
    settings = obs.client.get_input_settings(TEMPLATE).input_settings
    print(f"Modello: {TEMPLATE} kind={kind} settings={settings}")
    for name, visible in WANTED.items():
        if name in inputs:
            print(f"  = {name}: esiste gia', salto")
            continue
        print(f"  + {name}: {'creo' if args.apply else 'creerei'} in {C.CONTROL_SCENE} (visibile={visible})")
        if args.apply:
            obs.client.create_input(C.CONTROL_SCENE, name, kind, settings, visible)
    obs.disconnect()


if __name__ == "__main__":
    main()
