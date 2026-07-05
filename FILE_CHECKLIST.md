# PUPA v0.4 - FILE CHECKLIST ✅

## File necessari in F:\Desktop\pupa\

### Core PUPA v0.4
- ✅ `pupa_v04_couples.py` — Main loop (rinomina come pupa.py)
- ✅ `brain_v2_couples.py` — Logica coppie AB
- ✅ `obs_controller.py` — WebSocket OBS wrapper
- ✅ `audio_analyzer.py` — Analizzatore frequenze audio
- ✅ `logger.py` — Logging decisioni

### Configurazione
- ✅ `COUPLES_CONFIG.yaml` — Personalizzabile coppie/durate

### Utilità
- ✅ `list_audio_devices.py` — Diagnostica device audio (già esiste)

### Documentazione
- ✅ `SETUP_v04_COUPLES.md` — Guida setup + troubleshooting

### Directory richiesta
- `logs/` — Cartella per log (crea se non esiste)

---

## 🚀 SETUP IMMEDIATO

### 1. Copia tutti i file in F:\Desktop\pupa\
```bash
# Windows
copy *.py F:\Desktop\pupa\
copy *.yaml F:\Desktop\pupa\
mkdir F:\Desktop\pupa\logs
```

### 2. Verifica file
```bash
cd F:\Desktop\pupa
dir
```

Output atteso:
```
pupa_v04_couples.py
brain_v2_couples.py
obs_controller.py
audio_analyzer.py
logger.py
list_audio_devices.py
COUPLES_CONFIG.yaml
logs\ (directory)
```

### 3. Renaming (opzionale ma consigliato)
```bash
cd F:\Desktop\pupa
ren pupa_v03.py pupa_v03_backup.py
ren pupa_v04_couples.py pupa.py
```

### 4. Test immediate
```bash
python pupa.py
```

Output atteso:
```
======================================================================
  PUPA VJ BRAIN v0.4 'Disciplined Couples'
  Scene A: 4-5 min | Scene B: 3-4 transizioni casuali
======================================================================

[🤖 OBS] Connesso! OBS v32.1.2 (WS v5.7.3)
[PUPA] Mappate 19 scene hardware stabili.
[BRAIN] Inizializzato su scena: urbanfree_A

[AUDIO] B: [████████████████████] M: [████████████████████] H: [████████████████████] |
```

---

## ❓ Cosa fa ogni file

| File | Funzione |
|------|----------|
| `pupa.py` (v04) | Loop principale: OBS + Audio + Brain |
| `brain_v2_couples.py` | Logica decisioni scene (coppie AB) |
| `obs_controller.py` | Comunica con OBS WebSocket |
| `audio_analyzer.py` | Analizza frequenze, rileva kick/drop |
| `logger.py` | Scrive decisioni su file log |
| `COUPLES_CONFIG.yaml` | Config coppie scene (personalizzabile) |
| `list_audio_devices.py` | Diagnostica device audio |

---

## 📋 IMPORTS verificati

Tutti i file importano:
```python
import sounddevice       # pip install sounddevice
import numpy            # pip install numpy
from obsws_python import ReqClient  # pip install obsws-python
```

Se mancano, installa:
```bash
pip install sounddevice numpy obsws-python
```

---

## 🎯 PROSSIMO PASSO

1. **Verifica tutti i 5 file Python in F:\Desktop\pupa\**
2. **Crea cartella F:\Desktop\pupa\logs\**
3. **Lancia: `python pupa.py`**
4. **Attendi 10 secondi, poi premi Ctrl+C**
5. **Leggi il log**: `type logs\pupa_v04.log`

Se tutto ok, sei pronto per testare le coppie! 🎪
