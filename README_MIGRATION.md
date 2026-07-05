# 🚀 PUPA VJ BRAIN v0.3 — Milestone 3 Complete

**Kill Switch integrato + Quantizzazione su Kick Techno**

---

## 📋 Cosa è stato risolto

### ✅ Milestone 3 — Kill Switch (RMS Silence Detection)
- **Monitoraggio RMS in tempo reale** — Quando RMS < 8 (soglia di silenzio), forzatura istantanea su NERO_MASTER
- **Ripristino automatico** — Quando RMS > 12 (musica torna), resetta il timer e ricomincia a contare
- **Niente più switch bruschi** — Il timer `time_elapsed_in_scene` si congela durante il silenzio, non cresce
- **Quantizzazione corretta** — Una volta tornata la musica, aspetta il prossimo KICK prima di switchare (non lo fa all'istante)

### 🔧 Bug Corretti
1. ~~`rms` non veniva mai letto~~ → Ora è il core del kill switch
2. ~~`time_elapsed_in_scene` cresceva anche durante il silenzio~~ → Ora viene congelato
3. ~~Salvagente `allocated_duration + 2.0` errato~~ → Ridotto a `allocated_duration + 1.5` per una reazione più snella
4. ~~Niente controllo sensato della ripresa~~ → Implementato threshold a due livelli (silence: < 8, recovery: > 12)

---

## 📦 File da sostituire in F:\Desktop\pupa

### Sostituzioni dirette (copia/incolla)

| File Nuovo | Rimpiazza |
|-----------|-----------|
| `pupa_FIXED.py` | → `pupa.py` |
| `brain_FIXED.py` | → `brain.py` |
| `obs_controller_FIXED.py` | → `obs_controller.py` |
| `logger_FIXED.py` | → `logger.py` |
| `config_FIXED.yaml` | → `config.yaml` |
| `scenes_FIXED.yaml` | → `scenes.yaml` |

**Non toccare:**
- `audio_analyzer.py` ✓ (va bene così)

---

## 🚀 Istruzioni di Migrazione

### Step 1: Backup (Opzionale ma consigliato)
```bash
cd F:\Desktop\pupa
mkdir backup
copy pupa.py brain.py obs_controller.py logger.py *.yaml backup\
```

### Step 2: Sostituisci i file
```bash
# Copia i 6 file FIXED sopra elencati nella cartella pupa
# Rinominali rimuovendo il suffisso "_FIXED"
```

### Step 3: Verifica la password OBS
```bash
# Apri config.yaml e assicurati che password sia corretta
# Se non hai impostato una password su OBS, lasciala vuota: password: ""
```

### Step 4: Avvia PUPA v0.3
```bash
cd F:\Desktop\pupa
python pupa.py
```

---

## 🧪 Test del Kill Switch

Una volta avviato lo script:

1. **Fai partire la musica** → Dovresti vedere le barre spettrali (bass, mid, high) muoversi
2. **Ferma la musica** → Dopo ~0.5 secondi vedrai `[🛑 KILL SWITCH] Silenzio rilevato!` e il nero su OBS
3. **Riavvia la musica** → Vedrai `[🎵 RIPRESA] Musica rilevata!` e il set visivo riprenderà solo al prossimo KICK (non all'istante)

Se rimane congelato senza stampare nulla: il problema è `audio_analyzer.start()`. Verifica:
- [ ] La scheda audio è collegata (speakers attivi)
- [ ] Stereo Mix o VB-Audio Cable è abilitato in Windows
- [ ] La musica viene riprodotta da un'applicazione (Spotify, YouTube, DJ Controller, ecc.)

---

## 📊 Parametri Tunable

Apri `pupa.py` intorno alla riga 55-56 per regolare il kill switch:

```python
silence_threshold = 8.0   # RMS sotto questo = silenzio → Nero
recovery_threshold = 12.0 # RMS sopra questo = musica torna
```

**Più basso il numero** = più sensibile al silenzio (rischio falsi positivi)  
**Più alto il numero** = meno sensibile (rischio che tardi a rilevare il silenzio)

---

## 🎛️ Prossimi Step (Milestone 4)

- [ ] BPM detection real-time (aubio integration)
- [ ] MIDI controller override per switch manuale
- [ ] Feedback visivo del BPM rilevato
- [ ] Logging persistente con statistiche

---

## 🆘 Troubleshooting

### "Loop avviato. In ascolto..." e si blocca
→ Audio hardware bloccato. Controlla che `audio_analyzer.start()` riceva dati dalla scheda audio.

### Kill switch non attiva mai
→ Controlla i valori di `silence_threshold` e `recovery_threshold` nel codice (potrebbero essere impostati troppo alti rispetto alla tua scheda audio).

### Switch non aspetta il kick
→ Il salvagente `allocated_duration + 1.5` può essere ridotto se vuoi reazione più veloce, ma aumenta il rischio di switch nel mezzo della cassa.

---

**Versione:** 0.3 — Kill Switch Integrated  
**Data:** 1 Luglio 2026  
**Status:** ✅ Milestone 3 Complete

