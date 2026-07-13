"""
Template per secrets_local.py - copia questo file, rinominalo in
secrets_local.py e inserisci i valori reali della tua rete/OBS.
secrets_local.py e' escluso da git (vedi .gitignore).
"""

OBS_HOST = "192.168.1.102"  # "localhost" se OBS gira sulla stessa macchina
OBS_PORT = 4455
OBS_PASSWORD = "cambia-questa-password"

# Nome ESATTO del device audio da usare (vedi list_audio_devices.py).
# Risolto per nome ad ogni avvio, non per indice numerico: l'indice puo'
# spostarsi tra riavvii (osservato su Linux/PipeWire).
AUDIO_DEVICE_NAME = "Microsoft Sound Mapper - Input"

# Opzionale, SOLO Linux/PipeWire: pinna la sorgente pulse a un device
# specifico invece del default di sistema. Lascia commentato su Windows.
# PULSE_SOURCE = "alsa_input.pci-0000_00_1b.0.analog-stereo"

# Opzionale, SOLO Linux/PipeWire: gain di cattura (%) impostato via pactl ad
# ogni avvio di pupa.py, invece di fidarsi di un settaggio di sistema che
# potrebbe non persistere. Verifica il valore giusto con:
#   pactl list sources | grep -A8 "<nome sorgente>"
# 100% = 0dB (unita'). ATTENZIONE: valori sopra 100% possono causare
# clipping pesante se il segnale in ingresso e' gia' di livello linea
# (osservato dal vivo: 130% su un ingresso linea produceva un picco di 2.6
# su una scala che clippa a 1.0).
# AUDIO_INPUT_GAIN_PCT = 40

# Opzionale, SOLO sul rig con le 2 uscite show fisiche separate (vedi
# brain.get_monitor_outputs) - alternanza acceso/spento tra le 2 uscite,
# convergente su "entrambe accese" in DROP/PEAK. monitorIndex va verificato
# con get_monitor_list() via WebSocket, non indovinato (dipende dall'ordine
# di enumerazione dei display su quella macchina). MONITOR_BLACK_SCENE e' la
# scena mostrata quando un'uscita e' "spenta" (es. "black_master"). Richiede
# `wmctrl` installato (solo Linux/X11) per gestire le finestre proiettore -
# assente = alternanza disattivata, nessun errore.
# MONITOR_SHOW1_INDEX = 1
# MONITOR_SHOW2_INDEX = 2
# MONITOR_BLACK_SCENE = "black_master"
