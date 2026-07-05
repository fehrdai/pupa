# PUPA VJ Brain - Architecture & Technical Spec

**Version:** v0.6 (Unified Energy-Reactive Model + Strobe Burst + Sovrapposizioni + Scale-to-sound)
**Last Updated:** 2026-07-02
**Status:** ⚠️ PAUSED — scale-to-sound funziona in isolamento (verificato
20Hz/5s, zero errori) ma in produzione l'utente riporta "funziona in
Preview, spesso fermo in Program" (non sempre nemmeno in Preview) per
wave_kick, e strobo_B non raggiunge mai il 100%. Applicati fix (throttle
refresh per-scena, bug smoothing condiviso corretto, smoothing velocizzato,
log diagnostico) MA NON ANCORA TESTATI DAL VIVO. Vedi
"PUPA_DEVELOPMENT_LOG.md" → "Aggiornamento 6" per i dettagli e il piano di
verifica per la prossima sessione. Il bug di cache di rendering di base
("MISTERO RISOLTO") resta valido/confermato — il refresh disable/enable
funziona in test isolati; il problema aperto e' la sua affidabilita' a
20Hz sostenuto durante l'uso reale in Program.

---

## ⚠️ LEGGERE PRIMA DI TOCCARE brain.py

Questo progetto ha avuto MOLTI cicli di "ho verificato tecnicamente che funziona"
seguiti da "dal vivo non va bene". Le cause storiche reali (non ipotesi) sono state:
1. Un metodo OBS chiamato (`set_current_scene_transition_override`) **non esiste**
   nella libreria `obsws_python` — falliva silenziosamente da SEMPRE, prima di
   questa sessione. Vedi "Bug Storici Risolti" sotto.
2. Nomi di transizione inventati (`Flash`, `Strobe`, `Cut`) che non esistono in
   questo OBS (localizzato in italiano: il Cut si chiama **"Taglio"**).
3. Un bug di direzione (A→B vs B→A invertiti) causato da leggere `self.in_scene_a`
   DOPO che era già stato flippato.
4. Un bug di reset mancante (`temp_b_scene_time` mai azzerato) che rompeva i cicli
   wave_kick dal secondo in poi.

**Prima di dichiarare "risolto" qualcosa**, verificare SEMPRE con un test che
simula il flusso REALE `decide_next_scene()` → `get_transition_info()` in
sequenza (non impostando manualmente `in_scene_a` prima di chiamare
`_get_transition_info()` in isolamento — quello ha mascherato il bug #3 per
un'intera sessione).

**E anche quando i test passano**: chiedere sempre conferma visiva esplicita
prima di dichiarare fatto. I log confermano che il codice fa quello che dice
di fare — NON confermano che il risultato visivo sia quello desiderato
dall'utente. L'ultima sessione (2026-07-02) ha chiuso con l'utente che dice
"sembra peggiorato" nonostante ogni singolo fix fosse verificato nei log.

---

## 🚀 Milestone Tracker

### ✅ Milestone 1 — PUPA Core (completo)
- Connessione OBS WebSocket v5 (obsws-python), lettura/cambio scene
- Config hardcoded in pupa.py (host/port/password/audio_device)
- Logging: logger.py → logs/pupa.log, debug_logger.py → debug.log
- Main loop ~20Hz (time.sleep(0.05) in pupa.py)

### ✅ Milestone 2 — PUPA Audio (completo)
- FFT real-time (audio_analyzer.py), bande bass/mid/high, AGC
- Kick (bass_delta>14, bass>60, cooldown 220ms), Drop, Break detection

### 🔄 Milestone 3 — PUPA Brain (v0.6, tecnicamente completo, esito visivo da rivalutare)

---

## 🎯 Architettura Transizioni (v0.6 — stato REALE del codice)

### Nomi di transizione REALMENTE registrati in questo OBS
Verificato via `client.get_scene_transition_list()` (NON fidarsi di nomi assunti):
```
Stinger, Fade, Taglio, Scivola, Dissolvenza, Burn, Displace, Luma Wipe, Move, Blur, White Fade
```
Scene extra usate per effetti: `strobo_B` (scena bianca per la raffica strobo).

### Metodo OBS corretto per applicare le transizioni
```python
# obs_controller.py — CORRETTO (dopo il fix):
self.client.set_current_scene_transition(transition_type)
self.client.set_current_scene_transition_duration(transition_ms)
self.client.set_current_program_scene(scene_name)
```
Il metodo `set_current_scene_transition_override(...)` **non esiste** in
obsws_python — se lo vedi da qualche parte, è il bug storico, va rimosso.
`set_current_scene_transition` è **globale** (non per-scena): impostarlo
PRIMA di switchare, non dopo.

### Tre fasi comportamentali distinte

**1. INTRO/BREAK — alternanza wave_kick ↔ _A**
- Nessun ciclo A/B normale in questa fase: solo wave_kick e la scena _A corrente
- Valutata ad OGNI tick (oltre il debounce), NON solo su kick veri (durante il
  silenzio i kick richiedono bass>60, raramente si verificano)
- `prob_wave` (probabilità di entrare in wave_kick) decresce nel tempo:
  - INTRO: `max(0.2, 1 - couple_elapsed/30)`
  - BREAK: `max(0.3, 1 - time_in_break/20)`, e il debounce di BREAK stesso si
    riduce fino al 70% se il crollo del bass è brusco (drop_rate alto) —
    alternanza Cut/Fade invece di solo Fade
- **Permanenza minima su wave_kick: 3.0s** (`MIN_WAVE_KICK_DWELL`) prima che un
  kick possa farlo tornare a _A — applicata SEMPRE che si sia effettivamente
  su wave_kick, indipendentemente da come lo stato sia cambiato nel frattempo
- Transizioni: Stinger (esclusivo, solo entrata wave_kick, 20s), Fade (ritorno
  e ciclo INTRO generico)

**2. Recupero post-BREAK (BUILD/GROOVE) — wave_kick esteso**
- Se usciamo da BREAK con una risalita rapida del bass, wave_kick resta
  "eligible" anche in BUILD/GROOVE per una finestra di 25s
  (`POST_BREAK_RECOVERY_WINDOW`), con probabilità proporzionale alla velocità
  di risalita (soglia 3 unità/s, `POST_BREAK_RISE_RATE_THRESHOLD`)
- Se il roll di wave_kick fallisce in questa fase, il kick NON viene sprecato:
  prosegue al ciclo energetico normale sotto (fallthrough)
- Qui SERVE un vero kick (`is_kick=True`), a differenza di INTRO/BREAK, perché
  l'energia è già attiva

**3. Ciclo energetico principale (BUILD/GROOVE/DROP/PEAK/RELAX) — A↔B**
- **STESSA pool di transizioni per ENTRAMBE le direzioni** (A→B e B→A) — prima
  c'era un'asimmetria (B→A sempre Fade) che causava "FADE ovunque"
- Pool per coppia (COUPLE_TRANSITIONS, SENZA Fade — riservato a INTRO/BREAK):
  ```python
  COUPLE_TRANSITIONS = {
      "urbanfree_A":   ["Blur", "Displace"],
      "psicodance_A":  ["Displace", "Burn"],
      "montezuma_A":   ["Burn", "Blur"],
      "kusanagi_A":    ["Burn", "Blur"],
      "mri_A":         ["Displace", "Blur"],
      "futureflash_A": ["Burn", "Displace"],
      "segnali_A":     ["Burn", "Displace"],
  }
  ```
- Ogni switch sceglie random tra: pool della coppia + "Taglio" (Cut), con
  probabilità di Taglio che cresce con lo stato E col bass live
  (`CUT_PROBABILITY_BY_STATE`, 0.10 in BUILD → 0.75 in PEAK, +fino a 0.2 extra
  scalato sul bass corrente)
- Durata scala con l'energia (crescendo): 900ms in BUILD → 180ms in PEAK, mai
  sotto 150ms (floor visibile)
- DROP forza SEMPRE ritorno immediato a _A (priorità massima, NIENTE
  sovrapposizione qui — deve restare un colpo secco)

### Raffica Strobo/Flash ("burst")
Alterna rapidamente `strobo_B` con la scena di base, 4 flash (8 frame), poi
atterra sulla scena target normale. **Non bloccante**: piccola macchina a
stati che avanza un frame per tick di `decide_next_scene()` (niente
`time.sleep`, il loop a 20Hz continua a girare).
- Trigger: kick in PEAK (35%) o DROP (15%)
- Transizione per frame: scelta random tra "Taglio" e "White Fade" ad ogni
  raffica (per confrontarle)
- Intervallo tra frame: 100ms

### Sovrapposizioni ("overlap peek + return")
Su richiesta esplicita: spinge una transizione verso l'altra scena fino a un
blend 40-60%, la mantiene lì (non un vero "freeze" — OBS non lo supporta via
WebSocket; si ottiene con UN fade continuo abbastanza lungo), poi
**torna indietro alla scena di partenza** (nessun cambio scena netto,
`in_scene_a` non viene mai toccato).
- Trigger: 15% di probabilità (25% in INTRO/BREAK) ad ogni switch che sarebbe
  altrimenti normale — applicata al ciclo A↔B, a wave_kick↔A, MA NON al DROP
- Hold: 2-4s se il peek va verso _A, 0.5-2s se va verso _B
- Transizione: random tra "Fade" e "Dissolvenza"
- Non bloccante: stessa filosofia della raffica strobo

---

## 🐛 Bug Storici Risolti (questa sessione, 2026-07-02)

### Bug #1 — Metodo OBS inesistente (il più grave, mai notato prima)
`obs_controller.py` chiamava `self.client.set_current_scene_transition_override(...)`,
un metodo che **non esiste** in `obsws_python` (verificato con `dir(ReqClient)`).
Ogni chiamata lanciava `AttributeError`, ingoiata da un `except` silenzioso senza
log. Risultato: **nessuna transizione custom è mai stata applicata**, da prima
di questa sessione — OBS usava sempre la transizione globale impostata
manualmente nell'interfaccia. Motivo per cui "si vedeva sempre solo Fade".
**Fix:** usare `set_current_scene_transition(name)` +
`set_current_scene_transition_duration(ms)`, chiamati PRIMA di
`set_current_program_scene()`. Verificato dal vivo leggendo lo stato reale di
OBS dopo ogni chiamata (`get_current_scene_transition()`).

### Bug #2 — Nomi di transizione inventati
"Flash", "Strobe", "Cut" non esistono in questo OBS (localizzato in italiano).
Il Cut nativo si chiama **"Taglio"**. Verificato via `get_scene_transition_list()`.

### Bug #3 — Direzione A→B / B→A invertita
`decide_next_scene()` flippava `self.in_scene_a` PRIMA di ritornare la scena;
`get_transition_info()` veniva chiamato DOPO da pupa.py e rileggeva
`self.in_scene_a` già mutato, invertendo sistematicamente la direzione
rilevata. **Fix:** flag esplicito `self.last_transition_is_return` impostato
al momento della decisione, non ri-derivato dopo.

### Bug #4 — time.sleep() bloccante per gli "hold"
Il primo tentativo di implementare "sovrapposizioni" usava `time.sleep()`
dentro `obs_controller.switch_scene()`, bloccando l'intero loop a 20Hz (audio,
kick detection, tutto) per la durata dell'hold (8-20 secondi!). **Fix:**
rimosso completamente; OBS applica la durata della transizione in modo
nativo e asincrono, non serve alcun blocco lato Python.

### Bug #5 — temp_b_scene_time mai resettato
Nel nuovo ramo di ritorno da wave_kick, resettavo `self.temp_b_scene` ma non
`self.temp_b_scene_time`. Il timestamp "vecchio" di un ciclo precedente
faceva scattare il timeout dei 20s quasi subito al ciclo successivo,
bypassando la permanenza minima appena introdotta. **Fix:** reset esplicito
di entrambi al momento del ritorno effettivo (non durante un semplice peek).

### Bug #6 — _update_state usava time.time() invece di current_time
Bug preesistente (dalla v0.5 originale, non introdotto in questa sessione):
`state_start_time` veniva impostato con l'orologio reale invece del
`current_time` simulato/passato dal chiamante. Innocuo in produzione (pupa.py
passa `time.time()` comunque) ma rende ogni test sintetico fragile e
imprevedibile. **Fix:** `_update_state` ora accetta e usa `current_time`.

---

## ⚠️ STATO APERTO (fine sessione 2026-07-02, ~03:00)

**Tutti i bug sopra sono stati verificati con test realistici E dal vivo sui
log/debug di OBS.** Ciononostante, l'utente ha valutato il risultato finale
come **peggiorativo** rispetto a prima ("non proprio bene, sembra
peggiorato"), senza specificare ancora il dettaglio di COSA esattamente non
funziona a dovere.

**Prossima sessione deve:**
1. Chiedere all'utente COSA specificamente sembra peggiore (troppe
   sovrapposizioni? raffiche strobo fastidiose? wave_kick ora troppo
   invadente rispetto a prima? crescendo meno percepibile?) — NON assumere e
   NON ripartire a modificare codice alla cieca.
2. Considerare che l'aggiunta cumulativa di feature (burst strobo +
   sovrapposizioni + wave_kick esteso + BREAK reattivo, tutte nella stessa
   sessione) potrebbe aver reso il sistema visivamente troppo "affollato" o
   imprevedibile, anche se ogni singolo pezzo funziona correttamente in
   isolamento — il problema potrebe essere di BILANCIAMENTO/quantità, non di
   bug.
3. Valutare se fare un rollback selettivo di una o più feature (es. ridurre
   drasticamente le probabilità di trigger di burst/overlap) prima di
   aggiungerne altre.

---

## 📦 Architettura Core (invariata)

- **pupa.py** — Main loop (~20Hz)
- **brain.py** — Decision logic + state machine (v0.6)
- **obs_controller.py** — OBS WebSocket wrapper (FIXED: metodo corretto)
- **audio_analyzer.py** — FFT + kick/drop/break detection
- **logger.py** / **debug_logger.py** — Logging (logs/pupa.log, debug.log)

### Config (Hardcoded, non caricato da YAML)
- `pupa.py` — OBS credentials, audio device ID
- `brain.py` — COUPLES, COUPLE_TRANSITIONS, STATE_PARAMS, tutte le costanti
  di burst/overlap/wave_kick

---

## 🎬 OBS Scene Setup

### Couples (1:1 mapping)
```
urbanfree_A         ↔ spectrumbar_B
psicodance_A        ↔ stormlightning_B
montezuma_A         ↔ roundedbar_B
kusanagi_A          ↔ radialspike_B
mri_A               ↔ waveform1_B
futureflash_A       ↔ waveform2_B
segnali_A           ↔ ring_B
```

### Scene speciali (non fanno parte del ciclo couples)
- `wave_kick` — virtuale (non richiede scena dedicata particolare, gestita
  via `temp_b_scene` override in brain.py). Rinominata da `waveform_kick` il
  2026-07-05 per allinearsi al nome scena reale in OBS (vedi
  PUPA_DEVELOPMENT_LOG.md).
- `strobo_B`, `strobo_A` — scena bianca/effetto per la raffica strobo
- `NERO_MASTER` — scena nera (kill-switch, non attivo nel codice corrente)

### Transizioni OBS disponibili (nomi ESATTI, verificati via API)
`Stinger, Fade, Taglio, Scivola, Dissolvenza, Burn, Displace, Luma Wipe, Move, Blur, White Fade`

---

## Testing Commands

```bash
# Verify syntax
python -m py_compile brain.py obs_controller.py pupa.py

# Lista transizioni REALI registrate in OBS (non fidarsi di nomi assunti)
python -X utf8 -c "
from obsws_python import ReqClient
c = ReqClient(host='192.168.1.102', port=4455, password='<vedi secrets_local.py>', timeout=5)
r = c.get_scene_transition_list()
print([t.get('transitionName') if isinstance(t,dict) else getattr(t,'transitionName',None) for t in r.transitions])
"

# Verifica metodi REALI disponibili in obsws_python (se in dubbio su un nome)
python -c "import obsws_python, inspect; print([m for m in dir(obsws_python.ReqClient) if 'transition' in m.lower()])"

# Run + monitor log dal vivo
python pupa.py
tail -f logs/pupa.log
tail -f debug.log
```
