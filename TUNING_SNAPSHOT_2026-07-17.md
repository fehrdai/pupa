# Tuning snapshot — 2026-07-17 (prima del ribilanciamento cuts/monitor/pre-drop)

Valori esatti in `brain.py` prima delle modifiche di questa sessione, per poter confrontare/tornare indietro senza dover scavare in `git log`. Non fa parte della documentazione viva — è un riferimento temporaneo, cancellabile una volta che il nuovo tuning è validato dal vivo.

```python
# Raffiche di taglio (CUT BURST) - il lampo di riferimento per "quanto scatta"
CUT_BURST_PROBABILITY = {
    State.BUILD:  0.50,
    State.GROOVE: 0.40,
    State.DROP:   0.30,
    State.PEAK:   0.30,
    State.RELAX:  0.20,
}

# Raffiche strobo (colore pieno) - già riservate a DROP/PEAK
STROBE_BURST_PROBABILITY = {
    State.PEAK: 0.35,
    State.DROP: 0.15,
}

# Alternanza monitor: intervallo (min,max) secondi tra un flip e l'altro
MONITOR_ALTERNATION_INTERVAL_RANGE = {
    State.INTRO:  (3.0, 6.0),
    State.BREAK:  (3.0, 6.0),
    State.RELAX:  (2.25, 4.5),
    State.GROOVE: (0.9, 2.25),
    State.BUILD:  (0.4, 1.2),
}
MONITOR_BOTH_ON_STATES = (State.DROP, State.PEAK)  # DROP/PEAK sempre "both_on", mai alternating

# Alternanza monitor: peso configurazione (alternating / both_off / both_on) per stato
MONITOR_CONFIG_WEIGHTS = {
    State.INTRO:  {"alternating": 0.70, "both_off": 0.20, "both_on": 0.10},
    State.BREAK:  {"alternating": 0.30, "both_off": 0.55, "both_on": 0.15},
    State.RELAX:  {"alternating": 0.60, "both_off": 0.30, "both_on": 0.10},
    State.GROOVE: {"alternating": 0.75, "both_off": 0.10, "both_on": 0.15},
    State.BUILD:  {"alternating": 0.70, "both_off": 0.05, "both_on": 0.25},
}

# Flash pre-drop (V2, dopo la prima ricalibrazione già fatta oggi)
RUNUP_WINDOW_SHORT = 24
RUNUP_WINDOW_LONG = 80
RUNUP_SLOPE_FRACTION = 0.5
RUNUP_PERSISTENCE_S = 0.4
RUNUP_FLASH_COOLDOWN = 6.0
```

**Risultato osservato con questi valori (test live 2026-07-17 sera):** flash pre-drop scattato 54 volte in ~13 minuti; Linux percepito "meno sintonizzato" di Windows, in parte per il carico OBS (220%+ CPU) sommato al costo dell'alternanza monitor.
