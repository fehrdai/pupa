# PUPA — Porting a Linux

Note pratiche per far girare PUPA su una macchina Linux invece di Windows.
Scritto dopo una ricognizione della codebase (2026-07-03): il codice Python
in se' e' gia' portabile (nessun path Windows hardcoded, nessuna API
Windows-only nei file attivi). I punti che richiedono attenzione sono tutti
legati alla **cattura audio locale**, non al codice.

## OBS non e' toccato dal porting

`pupa.py` si connette a OBS via WebSocket su rete (`192.168.1.102:4455`,
vedi `CONFIG` in `pupa.py`). OBS resta sulla sua macchina Windows attuale —
il porting riguarda solo dove gira lo SCRIPT Python (`pupa.py`), non OBS
stesso. Se la macchina Linux e OBS sono sulla stessa rete, non serve
cambiare nulla in `obs_controller.py`.

## Cosa NON copiare sulla macchina Linux

- `python-3.14.6-amd64.exe` — installer Windows, inutile su Linux (su Linux
  Python si installa via package manager: `apt install python3 python3-pip`
  o simile)
- `__pycache__/` — bytecode compilato, si rigenera da solo
- `debug.log*`, `logs/` — log della sessione Windows, non servono

## Dipendenze di sistema (prima di pip install)

`sounddevice` si appoggia su PortAudio, che su Linux serve installare a
livello di sistema (non basta pip):
```bash
sudo apt install libportaudio2 portaudio19-dev python3-dev
```
(nomi pacchetto per Debian/Ubuntu; su altre distro il nome puo' variare,
es. `portaudio` su Arch/Fedora)

Poi le dipendenze Python (vedi `requirements.txt`, appena creato):
```bash
pip install -r requirements.txt
```

## Il punto critico: audio_device

`pupa.py` ha `CONFIG["audio_device"] = 0` con commento "Microsoft Sound
Mapper - Input" — **questo ID e' specifico di Windows e non ha alcun
significato su Linux**. Gli ID dei device audio sono enumerati in modo
completamente diverso tra le due piattaforme (Windows: WASAPI/DirectSound/
MME; Linux: ALSA/PulseAudio/PipeWire).

**Da fare SEMPRE su ogni nuova macchina (Windows o Linux):**
```bash
python list_audio_devices.py
```
e aggiornare `CONFIG["audio_device"]` in `pupa.py` col numero corretto per
quella macchina specifica — non assumere che "0" funzioni su Linux.

## Niente "Stereo Mix" su Linux

Su Windows, `list_audio_devices.py` cerca device con "Stereo Mix" o
"Loopback" nel nome — e' il modo standard Windows di catturare l'audio che
sta suonando sul sistema (non un microfono fisico). **Linux non ha
"Stereo Mix"**: l'equivalente e' un device "Monitor" esposto da
PulseAudio/PipeWire (es. "Monitor of Built-in Audio Analog Stereo", o
"alsa_output.XXX.monitor"). Ho aggiornato `list_audio_devices.py` per
evidenziare anche questi.

Se sulla macchina Linux non appare nessun device "Monitor", potrebbe
servire abilitarlo esplicitamente:
```bash
pactl load-module module-loopback  # PulseAudio, se serve un loopback esplicito
```
oppure verificare che PipeWire/PulseAudio esponga il monitor del sink di
output che si vuole catturare (di solito e' gia' presente di default).

## Percorsi file

`logger.py` e `debug_logger.py` scrivono log con path relativi
(`logs/pupa.log`, `debug.log`) usando forward slash — funzionano
correttamente sia su Windows sia su Linux senza modifiche.

## Checklist riassuntiva

- [ ] Copiare la cartella pupa su Linux (escludendo quanto sopra)
- [ ] `sudo apt install libportaudio2 portaudio19-dev python3-dev`
- [ ] `pip install -r requirements.txt`
- [ ] `python list_audio_devices.py` — trovare il device giusto (cercare un
      "Monitor" per catturare l'audio di sistema, non un microfono)
- [ ] Aggiornare `CONFIG["audio_device"]` in `pupa.py` col numero trovato
- [ ] `python test_obs.py` — verificare che la connessione OBS remota funzioni
      ancora (dovrebbe, essendo via rete)
- [ ] `python pupa.py` — avvio vero e proprio
