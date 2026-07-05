#!/usr/bin/env python3
"""
Diagnostica dei device audio disponibili.
Esegui questo script per trovare l'ID corretto del device da usare in audio_analyzer.py
"""

import sys
import sounddevice as sd

# Su Windows la console usa cp1252 di default, che non sa codificare
# emoji/simboli unicode (es. ✓) e fa crashare lo script a meta' lista.
sys.stdout.reconfigure(encoding="utf-8")

print("\n" + "="*70)
print("  AUDIO DEVICE DIAGNOSTICS")
print("="*70 + "\n")

devices = sd.query_devices()

print(f"Trovati {len(devices)} device audio:\n")

for i, device in enumerate(devices):
    print(f"[{i}] {device['name']}")
    print(f"    Canali IN: {device['max_input_channels']} | Canali OUT: {device['max_output_channels']}")
    print(f"    Sample rate: {device['default_samplerate']} Hz")
    
    # Evidenzia i device interessanti per l'input audio
    if device['max_input_channels'] > 0:
        print(f"    ✓ USABILE COME INPUT")
    
    # "Stereo Mix"/"Loopback" (Windows) oppure "Monitor" (Linux PulseAudio/
    # PipeWire, es. "Monitor of Built-in Audio Analog Stereo") — su Linux
    # non esiste "Stereo Mix", l'equivalente per catturare l'audio di
    # sistema (non un microfono fisico) e' un device "Monitor"
    if "Stereo Mix" in device['name'] or "Loopback" in device['name'] or "Monitor" in device['name']:
        print(f"    ⭐ IDEALE PER MONITORARE AUDIO DEL SISTEMA")
    
    if "Altoparlanti" in device['name'] or "Speakers" in device['name']:
        print(f"    📢 Speaker (Non usare per input)")
    
    print()

print("="*70)
print("\n💡 NEXT STEP:")
print("1. Trova il device che dice '✓ USABILE COME INPUT' e che vuoi usare")
print("2. Copia il NOME ESATTO (tra parentesi quadre il numero, ma quello")
print("   NON e' stabile tra riavvii su alcuni sistemi - vedi PUPA_DEVELOPMENT_LOG.md)")
print("3. Apri secrets_local.py e imposta:")
print("   AUDIO_DEVICE_NAME = \"<nome esatto del device>\"")
print("\n")
