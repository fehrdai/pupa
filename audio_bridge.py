"""
Bridge audio: CABLE Output (VB-Audio Virtual Cable) -> Altoparlanti (High
Definition Audio Device), l'uscita fisica cablata verso Linux.

Bypassa la funzione "Ascolta questo dispositivo" nativa di Windows, che si
e' rotta specificamente per questa uscita dopo l'aggiornamento Windows del
2026-07-16 (verificato: "Ascolta" funziona ancora se puntato alle Creative,
quindi non e' un problema generale della funzione - solo di questo
dispositivo, probabilmente il driver HD Audio aggiornato lo stesso giorno).

Uso: python3 audio_bridge.py
Ctrl+C per fermare.
"""
import sys

import sounddevice as sd

INPUT_NAME = "CABLE Output (VB-Audio Virtual Cable)"
OUTPUT_NAME = "Altoparlanti (High Definition Audio Device)"
CHANNELS = 2
SAMPLERATE = 44100
BLOCKSIZE = 1024


def _find_device(name, kind):
    """kind: 'input' o 'output'. Se piu' dispositivi condividono lo stesso
    nome (comune con VB-Cable, esposto sia WDM che MME/DirectSound), sceglie
    quello col sample rate di default piu' vicino a SAMPLERATE invece di
    prendere il primo a caso."""
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    candidates = [(i, d) for i, d in enumerate(sd.query_devices())
                  if d["name"] == name and d[key] > 0]
    if not candidates:
        print(f"[BRIDGE] nessun dispositivo {kind} trovato con nome {name!r}")
        sys.exit(1)
    idx, dev = min(candidates, key=lambda p: abs(p[1]["default_samplerate"] - SAMPLERATE))
    print(f"[BRIDGE] {kind}: idx={idx} name={dev['name']!r} default_sr={dev['default_samplerate']}")
    return idx


def _callback(indata, outdata, frames, time_info, status):
    if status:
        print(f"[BRIDGE] status: {status}")
    outdata[:] = indata


def main():
    in_idx = _find_device(INPUT_NAME, "input")
    out_idx = _find_device(OUTPUT_NAME, "output")

    print("[BRIDGE] avvio - Ctrl+C per fermare...")
    with sd.Stream(device=(in_idx, out_idx), samplerate=SAMPLERATE,
                    channels=CHANNELS, blocksize=BLOCKSIZE, callback=_callback):
        try:
            while True:
                sd.sleep(1000)
        except KeyboardInterrupt:
            print("\n[BRIDGE] fermato")


if __name__ == "__main__":
    main()
