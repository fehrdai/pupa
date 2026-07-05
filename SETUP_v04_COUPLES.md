# PUPA v0.4 - Disciplined Couples Model
## Guida implementazione

---

## 📋 COSA CAMBIA

### Prima (v0.3.3)
- Scene randomiche senza logica
- Kill switch nero casuale
- Decisioni ogni 100ms = caotico

### Dopo (v0.4)
- **Coppie AB disciplinate**: A (corpo) 4-5min → B (riempimento) 3-4 volte → nuova coppia
- **No nero casuale**: kill switch disabilitato, logica pulita
- **Transizioni lunghe**: 2-3 secondi (gestite in OBS)
- **Drop detection**: cambio veloce coppia su drop
- **Coppie non ripetute**: ultime 5 coppie tracked

---

## 🚀 SETUP

### Step 1: Copia i file
```bash
cd F:\Desktop\pupa

# Copia i nuovi file
copy C:\path\to\outputs\brain_v2_couples.py .\
copy C:\path\to\outputs\pupa_v04_couples.py .\
copy C:\path\to\outputs\COUPLES_CONFIG.yaml .\
```

### Step 2: Modifica pupa.py per usare v0.4
```bash
# Opzione A: Rinomina file
ren pupa.py pupa_v03_old.py
ren pupa_v04_couples.py pupa.py

# Opzione B: Crea un launcher
echo python pupa_v04_couples.py > launch_v04.bat
```

### Step 3: Verifica OBS Transitions
```
OBS → Scenes
Per OGNI scena (A e B):
  - Click destro scena
  - "Manage Sources" → "Transitions"
  - Aggiungi "Fade" 2000-3000ms
```

---

## ⚙️ CONFIGURAZIONE COPPIE

File: `COUPLES_CONFIG.yaml`

### Personalizza pool B per ogni A
```yaml
couples:
  urbanfree_A:
    duration_seconds: 240      # 4 minuti
    b_pool: [spectrumbar_B, radialspike_B, tunnelwave_B]
  
  psicodance_A:
    duration_seconds: 300      # 5 minuti
    b_pool: [stormlightning_B, waveform1_B, ring_B]
```

**Linee guida:**
- Durata A: 240-320 secondi (4-5 min)
- Pool B: 3-4 scene casuali (diversificate per variety)
- Max transitions B: 3-4 (regola quanto tempo stai in riempimento)

---

## 🧠 LOGICA FLUSSO

```
START
  ↓
Inizializza su scena attuale
Copia current_couple_a da nome scena (_A)
Carica pool B corrispondente
  ↓
LOOP:
  [IN SCENA A - Aspetta 4-5 min]
    ├─ Audio analysis
    ├─ Timer A >= DURATION?
    │  └─ YES: Seleziona B casuale dal pool → SWITCH
    │     (b_transitions_count = 1)
    └─ NO: Continua in A
  
  [IN SCENA B - Aspetta kick/drop]
    ├─ is_drop?
    │  └─ YES: Seleziona NUOVA coppia A (non ripetuta)
    │     Reset timer, Reset counter B
    │     → SWITCH immediato a A
    │
    ├─ is_kick?
    │  ├─ b_transitions_count < MAX_B_TRANSITIONS?
    │  │  └─ YES: Torna ad A stessa coppia
    │  │     b_transitions_count += 1
    │  │     → SWITCH A
    │  │
    │  └─ b_transitions_count >= MAX_B_TRANSITIONS?
    │     └─ YES: Seleziona NUOVA coppia A
    │        Reset tutto, → SWITCH a A nuova
    │
    └─ NO kick/drop: Aspetta
```

---

## 🎯 TESTING

### Test 1: Verifica inizializzazione
```bash
python pupa.py

# Output atteso:
# [🤖 OBS] Connesso! OBS v32.1.2
# [PUPA] Mappate 19 scene
# [BRAIN] Inizializzato su scena: urbanfree_A
```

### Test 2: Verifica timer A (4-5 min)
1. Lancia PUPA
2. Attendi 4 minuti
3. Verifica che cambi a una scena B automaticamente
4. Check log: `[SWITCH] urbanfree_A → spectrumbar_B | Timeout A`

### Test 3: Verifica counter B
1. Quando in B, dai kick manualmente (picchiare bassone)
2. Dopo 3 kick, dovrebbe switchare a A diversa
3. Check log: `[SWITCH] spectrumbar_B → psicodance_A | Limite B raggiunto`

### Test 4: Verifica drop detection
1. Quando in B, aspetta drop audio (calo improvviso bass dopo peak)
2. Dovrebbe switchare VELOCEMENTE a A nuova (diversa dalla coppia precedente)
3. Check log: `[SWITCH] spectrumbar_B → psicodance_A | ⚡ DROP EVENT`

---

## 📊 LOGGING & DEBUG

File: `logs/pupa_v04.log`

Ogni decisione scrive:
```
17:01:06.146
  [Cambio Scena] urbanfree_A ──> spectrumbar_B
  [Motivo] Timeout A (240s)
  [Stats] Couple: 1/7, B transitions: 1/3, Bass: 42.0, Mood: Chill
```

### Debug audio device
Se audio non funziona:
```bash
python list_audio_devices.py
# Verifica device 8 o installa Stereo Mix
```

---

## 🎪 CUSTOMIZZAZIONE AVANZATA

### Cambia durata A per una coppia
```yaml
# In COUPLES_CONFIG.yaml
urbanfree_A:
  duration_seconds: 180  # Riduci a 3 min (invece di 4)
```

### Cambia max B transitions
```yaml
# In brain_v2_couples.py, riga ~35
self.MAX_B_TRANSITIONS = 5  # Aumenta a 5 (invece di 3)
```

### Aggiungi nuova coppia
```yaml
# In COUPLES_CONFIG.yaml
my_new_scene_A:
  duration_seconds: 250
  b_pool: [spectrumbar_B, ring_B, waveform1_B]

# In brain_v2_couples.py riga ~9
"my_new_scene_A": ["spectrumbar_B", "ring_B", "waveform1_B"],
```

---

## 🔧 TROUBLESHOOTING

### Problem: Scene non switch
**Soluzione:**
1. Verifica OBS WebSocket connesso (log dice "Connesso!")
2. Verifica nome scene in OBS match con config (case-sensitive)
3. Verifica audio device 8 cattura audio (guarda bar realtime)

### Problem: Switch troppo veloce/lento
**Soluzione:**
```python
# In pupa.py, riga ~85
decision_interval = 2.0  # Aumenta a 2 secondi tra decisioni
```

### Problem: Coppie ripetute
**Soluzione:**
```python
# In brain_v2_couples.py riga ~50
self.couple_history = deque(maxlen=5)  # Aumenta a 10
```

---

## 📝 VARIABILI CHIAVE

| Variabile | Valore | Modifica? |
|-----------|--------|----------|
| `SCENE_A_DURATION` | 240s (4min) | ✅ Per mood corto/lungo |
| `MAX_B_TRANSITIONS` | 3 | ✅ Per più/meno riempimento |
| `couple_history maxlen` | 5 | ✅ Se vuoi più varietà |
| `transition_duration_ms` | 2500 | ✅ In OBS scene settings |

---

## 🎬 NEXT STEPS

1. ✅ Testa v0.4 in pausa (no DJ set)
2. ✅ Registra log per 5-10 min
3. ✅ Verifica flusso coppie nel log
4. ✅ Regola durata A/B per il tuo ritmo
5. ✅ Testa con musica live
6. ✅ Deploy per primo DJ set

---

**Buon VJ! 🎪**
