"""
Audio Analyzer - Real-time frequency analysis
- Bass, Mid, High detection
- Kick detection (bass delta)
- Drop detection (bass cliff)
- Break detection (silence)
"""

import sounddevice as sd
import numpy as np
from collections import deque
import threading
import time
from debug_logger import debug as debug_log, setup_debug_logger

# Log dedicato, separato da debug.log: quello ruota ogni ~100KB/8min sotto il
# traffico dei log di brain.py (TRANS/decisioni), troppo in fretta per
# conservare i livelli audio di un intero live. File/rotazione piu' ampia,
# pensata per coprire un'intera serata (~1MB/ora a 1 riga/5s).
_level_logger = setup_debug_logger(
    name="pupa_levels", log_file="audio_levels.log",
    max_bytes=2_000_000, backup_count=5
)


class AudioAnalyzer:
    # AGC: inseguitore di picco (attacco rapido, rilascio lento), non una EMA
    # simmetrica. Misurato dal vivo su un liveset reale pulito (no clipping,
    # gain corretto): con lo stesso alpha in salita e discesa il tetto
    # convergeva verso la MEDIA di bass_mag, non il picco - per costruzione
    # circa meta' dei valori la superano e vengono tagliati al 100%
    # (mediana bass_norm osservata: 100.0, su 84 campioni/7min). ATTACK
    # basso = sale in fretta per inseguire un nuovo picco; RELEASE alto =
    # scende lentamente, cosi' il tetto non collassa subito dopo un picco
    # ne' durante un calo breve.
    AGC_ATTACK = 0.3
    AGC_RELEASE = 0.9995
    # SOGLIA KICK ADATTIVA (2026-07-23): kick_threshold_bass_min era un
    # valore FISSO, stesso principio gia' risolto per BREAK in brain.py
    # (vedi _adaptive_thresholds) - qui applicato al rilevamento kick, che
    # ne aveva bisogno anche di piu' (e' la base di BPM/stato/tutto il
    # resto). KICK_MIN_PERCENTILE e' il percentile della dinamica RECENTE
    # (KICK_ADAPTIVE_WINDOW_SAMPLES blocchi, ~6s a blocksize/samplerate
    # di default) usato come pavimento - alto abbastanza che solo i kick
    # veri (i picchi) lo superino regolarmente, ma che si ricalibra da
    # solo se la dinamica del segnale cambia (gain, genere, macchina)
    # invece di dover ritarare un numero a mano ogni volta (fatto stasera
    # per Linux: 60->35, funzionava ma solo per QUESTA sera/sorgente).
    # kick_threshold_bass_min (override per macchina in secrets_local.py)
    # ora fa da PAVIMENTO DI SICUREZZA sotto il percentile - stesso ruolo
    # di BREAK_ABSOLUTE_FLOOR, non piu' la soglia primaria.
    KICK_ADAPTIVE_WINDOW_SAMPLES = 130  # ~6s a blocksize=2048/samplerate=44100 (~46ms/blocco)
    KICK_MIN_PERCENTILE = 0.75
    # TETTO LENTO (2026-07-22): lo stesso inseguitore di picco sopra, ma con
    # un rilascio molto piu' lento - usato SOLO per riconoscere un vero
    # break (vedi bass_avg_long in get_metrics/brain.py). Scoperto dal vivo:
    # durante un break prolungato il tetto VELOCE insegue verso il basso
    # entro pochi minuti (0.9995^N con N grande arriva comunque vicino allo
    # zero) - una volta che il tetto si e' gia' abbassato per inseguire una
    # fase piu' calma, un vero silenzio successivo viene diviso per un tetto
    # altrettanto basso e torna a sembrare una percentuale "normale" invece
    # di un crollo netto, perdendo il contrasto che lo renderebbe
    # riconoscibile. Con un rilascio 10x piu' lento (dimezzamento in
    # ~10min invece di ~1min), il tetto lento ricorda il vero picco recente
    # molto piu' a lungo - un break normalizzato contro QUESTO tetto resta
    # basso per davvero, non "rincorso" verso l'alto.
    AGC_RELEASE_LONG = 0.99995
    LEVEL_LOG_INTERVAL = 5.0  # secondi tra un log dei livelli e il successivo

    # ALERT: gain di ingresso troppo alto (clipping) o segnale assente
    # (device sbagliato/cavo scollegato/sorgente muta). Scoperto dal vivo:
    # un gain di cattura troppo alto (130% su un ingresso Linux) produceva
    # un segnale clippato che schiacciava bass/mid/high sempre al tetto,
    # simulando "musica sempre al massimo" indipendentemente dal brano.
    CLIP_PEAK_THRESHOLD = 0.98    # >= questo picco (1.0 = piena scala) = clipping
    CLIP_ALERT_COOLDOWN = 10.0    # secondi minimi tra un alert di clipping e il successivo
    SILENCE_PEAK_THRESHOLD = 0.01  # sotto questo picco il blocco e' considerato "silenzio"
    SILENCE_ALERT_AFTER = 20.0    # secondi di silenzio continuo prima di allertare
    SILENCE_ALERT_COOLDOWN = 30.0  # secondi minimi tra un alert di silenzio e il successivo

    # STIMA BPM (esplorativa): PUPA non leggeva affatto il tempo, solo
    # eventi kick isolati. Stima il BPM dagli intervalli tra kick
    # consecutivi - mediana (robusta a kick persi/spuri) invece di media,
    # con un range di plausibilita' tipico EDM (60-180 BPM) per scartare
    # intervalli spuri (rumore/doppio trigger) o troppo lunghi (break/silenzio).
    BPM_MIN = 60.0
    BPM_MAX = 180.0
    BPM_MIN_INTERVAL_S = 60.0 / BPM_MAX  # 0.333s
    BPM_MAX_INTERVAL_S = 60.0 / BPM_MIN  # 1.0s
    BPM_HISTORY_SIZE = 8
    BPM_SMOOTHING = 0.2  # EMA: quanto peso al nuovo valore ad ogni aggiornamento

    # GRIGLIA DI BEAT (2026-07-14): PUPA leggeva solo il BPM aggregato (quanto
    # e' veloce), non la FASE (quando cade esattamente il prossimo beat) -
    # serve per poter quantizzare le transizioni sul battito invece che su
    # timer liberi (vedi memoria "beat_tracking_and_timer_tuning"). Non
    # insegue il vero downbeat del DJ: la cassa in 4/4 tipica della techno
    # suona su OGNI beat, quindi non c'e' un segnale affidabile per
    # distinguere il beat 1 dagli altri 3 solo dal pattern della cassa.
    # beat_count e' invece una griglia INTERNA coerente con se stessa,
    # ri-agganciata a "beat 1" ad ogni uscita da BREAK (un punto di resync
    # naturale gia' usato altrove) - basta per rendere le transizioni di
    # PUPA coordinate tra loro, senza pretendere di sapere dove il DJ mette
    # davvero la battuta.
    BEAT_PHASE_CORRECTION = 0.25  # 0=nessuna correzione, 1=snap secco sul kick reale

    def __init__(self, device=8, channels=2, samplerate=44100, blocksize=2048,
                 kick_threshold_bass_min=None, kick_threshold_bass_delta=None):
        self.device = device
        self.channels = channels
        self.samplerate = samplerate
        self.blocksize = blocksize

        # Thresholds
        # kick_threshold_bass_min: ora PAVIMENTO DI SICUREZZA (vedi
        # KICK_MIN_PERCENTILE sopra), non piu' la soglia primaria - scoperto
        # dal vivo 2026-07-22 che un valore fisso condiviso e' un problema
        # reale su un ponte audio (es. Windows->Linux): la dinamica del bass
        # normalizzato puo' restare strutturalmente piu' bassa su una
        # macchina che sull'altra anche a parita' di musica (qui, sotto 60
        # per l'80% del tempo su Linux contro ~55% su Windows), perdendo kick
        # veri che non arrivano mai a superare una soglia assoluta tarata
        # altrove - causa sospettata di stime BPM instabili (intervalli
        # kick-a-kick incoerenti quando alcuni kick vengono persi). Un valore
        # fisso piu' basso (60->35 su Linux quella sera) migliorava le cose
        # ma solo per quella sera/sorgente - da qui la versione adattiva.
        # Override opzionale per macchina resta in secrets_local.py
        # (KICK_THRESHOLD_BASS_MIN/DELTA in pupa.py), ora col ruolo di
        # pavimento minimo invece che di soglia fissa.
        self.kick_threshold_bass_delta = kick_threshold_bass_delta if kick_threshold_bass_delta is not None else 14
        # Tornato a 60 di default (2026-07-23, vedi _adaptive_kick_min): con
        # l'adattiva disattivata questo e' di nuovo la soglia FISSA vera e
        # propria, non un pavimento di sicurezza - 60 e' il valore originale,
        # buono su un segnale sano (Windows). Linux usa un override esplicito
        # (35, vedi secrets_local.py) tarato per la sua catena audio specifica.
        self.kick_threshold_bass_min = kick_threshold_bass_min if kick_threshold_bass_min is not None else 60
        self.kick_cooldown_ms = 220
        
        self.drop_threshold_bass = 75
        self.drop_threshold_bass_hist_avg = 35
        
        self.break_threshold_bass = 20
        self.break_threshold_bass_hist_avg = 50
        self.break_threshold_mid = 30
        
        # State
        self.stream = None
        self.running = False
        self.lock = threading.Lock()
        
        # Frequency bins
        self.bass_range = (20, 250)      # Hz
        self.mid_range = (250, 4000)     # Hz
        self.high_range = (4000, 20000)  # Hz
        
        # History for smoothing
        self.bass_history = deque(maxlen=60)
        self.mid_history = deque(maxlen=60)
        self.high_history = deque(maxlen=60)
        self.bass_history_long = deque(maxlen=60)  # bass normalizzato sul tetto LENTO (vedi AGC_RELEASE_LONG) - solo per riconoscere un vero break
        self.bass_history_kick = deque(maxlen=self.KICK_ADAPTIVE_WINDOW_SAMPLES)  # finestra dedicata (~6s) per il pavimento kick adattivo (vedi _adaptive_kick_min)
        
        # Kick detection state
        self.last_kick_time = 0
        self.is_kick = False
        self.is_drop = False
        self.is_break = False

        # Stima BPM (vedi costanti sopra)
        self.kick_intervals = deque(maxlen=self.BPM_HISTORY_SIZE)
        self.bpm = 0.0

        # Griglia di beat (vedi BEAT_PHASE_CORRECTION sopra) - next_beat_time
        # e' un timestamp assoluto (stessa scala di time.time()), <= 0
        # significa "non ancora agganciato".
        self.next_beat_time = 0.0
        self.beat_count = 0
        self.is_beat = False
        
        # AGC (Automatic Gain Control) - vedi _update_agc_ceiling()
        self.max_bass = 1.0
        self.max_mid = 1.0
        self.max_high = 1.0
        self.max_bass_long = 1.0  # tetto a rilascio lento (vedi AGC_RELEASE_LONG), solo per il bass

        # Livello audio in dBFS (RMS del segnale grezzo, non filtrato per banda),
        # per replicare la logica soglia/tetto del vecchio plugin "Scale to Sound".
        # Smoothed via EMA: senza, il valore per singolo blocco (~46ms) e' molto
        # rumoroso blocco-a-blocco (a differenza di bass/mid/high che gia' usano
        # una media mobile su 30 campioni) e produceva una reattivita' "casuale".
        self.db_level = -60.0

        # Log periodico dei livelli grezzi (bass/mid/high normalizzati + tetto
        # AGC), per poter verificare A POSTERIORI se il mix era saturato/
        # schiacciato sul tetto (es. dopo un live), invece di doverlo dedurre
        # indirettamente dalla distribuzione degli stati nei log delle decisioni.
        self._last_level_log_time = 0.0

        # Stato per gli alert di clipping/silenzio (vedi costanti sopra)
        self._last_clip_alert_time = 0.0
        self._last_signal_time = time.time()
        self._last_silence_alert_time = 0.0
        self.last_peak = 0.0
        self.clipping = False

    def start(self):
        """Start audio capture stream"""
        self.running = True
        self.stream = sd.InputStream(
            device=self.device,
            channels=self.channels,
            samplerate=self.samplerate,
            blocksize=self.blocksize,
            callback=self._audio_callback,
            latency='low'
        )
        self.stream.start()
    
    def stop(self):
        """Stop audio capture"""
        self.running = False
        if self.stream:
            self.stream.stop()
            self.stream.close()
    
    def _audio_callback(self, indata, frames, time_info, status):
        """Callback per ogni blocco audio"""
        if status:
            debug_log(f"[AUDIO] STATUS/XRUN: {status}")
            return
        
        # Converti a mono se stereo
        if indata.shape[1] > 1:
            audio = np.mean(indata, axis=1)
        else:
            audio = indata.flatten()

        now = time.time()

        # ALERT: clipping (picco troppo vicino/oltre la piena scala) o
        # silenzio prolungato (probabile device/sorgente sbagliati). Alert
        # rate-limited per non spammare console/log durante un intero brano.
        peak = float(np.max(np.abs(audio))) if len(audio) else 0.0
        self.last_peak = peak
        self.clipping = peak >= self.CLIP_PEAK_THRESHOLD

        if self.clipping and (now - self._last_clip_alert_time) >= self.CLIP_ALERT_COOLDOWN:
            self._last_clip_alert_time = now
            msg = f"[ALERT] CLIPPING sul segnale in ingresso (picco={peak:.2f}, >=1.0 = distorsione) - abbassa il gain di ingresso"
            print(msg)
            _level_logger.warning(msg)

        if peak >= self.SILENCE_PEAK_THRESHOLD:
            self._last_signal_time = now
        elif (now - self._last_signal_time) >= self.SILENCE_ALERT_AFTER and \
                (now - self._last_silence_alert_time) >= self.SILENCE_ALERT_COOLDOWN:
            self._last_silence_alert_time = now
            msg = (f"[ALERT] SEGNALE ASSENTE da oltre {self.SILENCE_ALERT_AFTER:.0f}s "
                   f"- controlla device/cavo/sorgente")
            print(msg)
            _level_logger.warning(msg)

        # Livello RMS in dBFS del blocco grezzo (0dB = piena scala), smoothed
        # via EMA per ridurre il rumore blocco-a-blocco (~46ms/blocco)
        rms = np.sqrt(np.mean(audio.astype(np.float64) ** 2))
        db_level_raw = 20 * np.log10(max(rms, 1e-10))
        # 0.5: meno lag rispetto a prima (0.3) — il doppio smoothing (qui +
        # quello in uscita su scale in pupa.py) sommava troppa latenza,
        # risultando "fermo o lento" invece che reattivo a tempo
        db_level = self.db_level * 0.5 + db_level_raw * 0.5

        # FFT
        fft_result = np.fft.fft(audio)
        freqs = np.fft.fftfreq(len(fft_result), 1 / self.samplerate)
        magnitude = np.abs(fft_result)
        
        # Estrai bande di frequenza
        bass_idx = np.where((freqs >= self.bass_range[0]) & (freqs <= self.bass_range[1]))[0]
        mid_idx = np.where((freqs >= self.mid_range[0]) & (freqs <= self.mid_range[1]))[0]
        high_idx = np.where((freqs >= self.high_range[0]) & (freqs <= self.high_range[1]))[0]
        
        bass_mag = np.mean(magnitude[bass_idx]) if len(bass_idx) > 0 else 0
        mid_mag = np.mean(magnitude[mid_idx]) if len(mid_idx) > 0 else 0
        high_mag = np.mean(magnitude[high_idx]) if len(high_idx) > 0 else 0
        
        # AGC: inseguitore di picco (vedi _update_agc_ceiling)
        self.max_bass = self._update_agc_ceiling(self.max_bass, bass_mag)
        self.max_mid = self._update_agc_ceiling(self.max_mid, mid_mag)
        self.max_high = self._update_agc_ceiling(self.max_high, high_mag)
        # Tetto LENTO, solo bass (vedi AGC_RELEASE_LONG) - stesso attacco
        # veloce (insegue subito un nuovo picco), rilascio molto piu' lento
        # (non "dimentica" il picco recente durante una fase piu' calma).
        self.max_bass_long = self._update_agc_ceiling(self.max_bass_long, bass_mag, release=self.AGC_RELEASE_LONG)

        # Normalizza 0-100
        bass_norm = (bass_mag / max(self.max_bass, 0.001)) * 100
        mid_norm = (mid_mag / max(self.max_mid, 0.001)) * 100
        high_norm = (high_mag / max(self.max_high, 0.001)) * 100
        bass_norm_long = (bass_mag / max(self.max_bass_long, 0.001)) * 100

        bass_norm = min(100, max(0, bass_norm))
        mid_norm = min(100, max(0, mid_norm))
        high_norm = min(100, max(0, high_norm))
        bass_norm_long = min(100, max(0, bass_norm_long))

        with self.lock:
            self.bass_history.append(bass_norm)
            self.mid_history.append(mid_norm)
            self.high_history.append(high_norm)
            self.bass_history_long.append(bass_norm_long)
            self.db_level = db_level

            # Detect kick/drop/break, aggiorna la griglia di beat
            self._detect_events(now)

        if now - self._last_level_log_time >= self.LEVEL_LOG_INTERVAL:
            self._last_level_log_time = now
            _level_logger.debug(
                f"bass={bass_norm:.1f} mid={mid_norm:.1f} high={high_norm:.1f} "
                f"dB={db_level:.1f} peak={peak:.3f} bpm={self.bpm:.1f} | tetto_agc bass={self.max_bass:.0f} "
                f"mid={self.max_mid:.0f} high={self.max_high:.0f} | grezzo bass={bass_mag:.0f} "
                f"mid={mid_mag:.0f} high={high_mag:.0f} | bass_long={bass_norm_long:.1f} tetto_long={self.max_bass_long:.0f}"
            )
    
    def _update_agc_ceiling(self, ceiling, mag, release=None):
        """Inseguitore di picco: attacco rapido se mag supera il tetto
        attuale (insegue subito un nuovo picco, anche a freddo da 1.0),
        rilascio lento altrimenti (il tetto non collassa dopo un picco ne'
        durante un calo breve). Vedi commento su AGC_ATTACK/AGC_RELEASE.

        release: tasso di rilascio da usare al posto di AGC_RELEASE - solo
        per il tetto LENTO del bass (vedi AGC_RELEASE_LONG), stesso
        meccanismo, solo piu' lento a dimenticare un picco recente."""
        rel = self.AGC_RELEASE if release is None else release
        if mag > ceiling:
            return ceiling * self.AGC_ATTACK + mag * (1 - self.AGC_ATTACK)
        return ceiling * rel + mag * (1 - rel)

    def _update_bpm(self, interval_s):
        """Aggiorna la stima BPM da un intervallo tra due kick consecutivi.

        Se l'intervallo e' troppo lungo (kick ogni 2 beat invece che ogni
        beat, comune in stili con groove piu' rado), lo dimezza UNA SOLA
        VOLTA prima di validarlo - cattura il "doppio tempo" implicito senza
        confondere un genere piu' lento con un BPM basso spurio. Un solo
        dimezzamento (non un ciclo) e' voluto: con range 60-180 BPM (rapporto
        3:1) dimezzamenti ripetuti finirebbero comunque per rientrare nel
        range anche per gap lunghi di break/silenzio, sporcando la stima.
        Fuori range anche dopo il dimezzamento -> ignorato.

        Mediana (non media) sugli ultimi N intervalli: robusta a un singolo
        kick perso o spurio, che altrimenti sposterebbe parecchio una media.
        EMA finale per smoothing, ma abbastanza reattivo da seguire un vero
        cambio di tempo (es. transizione b2b tra due brani diversi)."""
        # Un solo dimezzamento: copre il "half-time feel" (kick ogni 2 beat)
        # senza inghiottire gap lunghi (break/silenzio) che dimezzati ripetutamente
        # finirebbero comunque per rientrare nel range e sporcare la stima.
        if interval_s > self.BPM_MAX_INTERVAL_S and interval_s / 2 >= self.BPM_MIN_INTERVAL_S:
            interval_s /= 2

        if not (self.BPM_MIN_INTERVAL_S <= interval_s <= self.BPM_MAX_INTERVAL_S):
            return

        self.kick_intervals.append(interval_s)
        if len(self.kick_intervals) < 4:
            return

        sorted_intervals = sorted(self.kick_intervals)
        median_interval = sorted_intervals[len(sorted_intervals) // 2]
        target_bpm = 60.0 / median_interval

        if self.bpm == 0.0:
            self.bpm = target_bpm
        else:
            self.bpm = self.bpm * (1 - self.BPM_SMOOTHING) + target_bpm * self.BPM_SMOOTHING

    def _adaptive_kick_min(self):
        """Pavimento kick come PERCENTILE della dinamica recente - DISATTIVATO
        2026-07-23 dopo un test dal vivo peggiore della soglia fissa (1.8%
        dei campioni a BPM 140+ contro il 25-27% della soglia fissa 35):
        un percentile e' per costruzione sempre superato dal 25% piu' alto
        della finestra, kick o no - su un brano con meno separazione netta
        tra "kick" e "il resto" rispetto alla simulazione (troppo pulita/
        semplificata) questo ha lasciato passare falsi kick, sporcando la
        stima BPM invece di migliorarla. Tenuto nel codice per riprendere il
        lavoro con calma (serve capire nel dettaglio quali kick venivano
        rilevati quella sera, veri o falsi, prima di ritentare) - per ora
        ricade sempre sul pavimento fisso (kick_threshold_bass_min, vedi
        __init__ e l'override per macchina in secrets_local.py)."""
        return self.kick_threshold_bass_min

    def _detect_events(self, now):
        """Rileva kick, drop, break, e aggiorna la griglia di beat (vedi
        _update_beat_grid/_correct_beat_phase). `now` e' time.time() del
        blocco corrente, passato dal chiamante invece di ricalcolato qui per
        restare sulla stessa base temporale della griglia di beat."""
        if len(self.bass_history) < 2:
            return

        current_bass = self.bass_history[-1]
        prev_bass = self.bass_history[-2]
        bass_avg = np.mean(list(self.bass_history)[-30:]) if len(self.bass_history) >= 30 else np.mean(self.bass_history)
        mid_avg = np.mean(self.mid_history) if self.mid_history else 0

        self.bass_history_kick.append(current_bass)

        # KICK: bass delta > 14, bass sopra il pavimento ADATTIVO (vedi
        # _adaptive_kick_min - percentile della dinamica recente, non piu'
        # un numero fisso, vedi KICK_MIN_PERCENTILE sopra)
        bass_delta = current_bass - prev_bass
        current_time_ms = now * 1000
        kick_min = self._adaptive_kick_min()

        if (bass_delta > self.kick_threshold_bass_delta and
            current_bass > kick_min and
            (current_time_ms - self.last_kick_time) > self.kick_cooldown_ms):
            if self.last_kick_time > 0:
                self._update_bpm((current_time_ms - self.last_kick_time) / 1000.0)
            self.is_kick = True
            self.last_kick_time = current_time_ms
            self._correct_beat_phase(now)
        else:
            self.is_kick = False

        # DROP: bass > 75 && bass_avg_hist < 35 && kick
        if (current_bass > self.drop_threshold_bass and
            bass_avg < self.drop_threshold_bass_hist_avg and
            self.is_kick):
            self.is_drop = True
        else:
            self.is_drop = False

        # BREAK: bass < 20 && bass_avg > 50 && mid > 30
        was_break = self.is_break
        if (current_bass < self.break_threshold_bass and
            bass_avg > self.break_threshold_bass_hist_avg and
            mid_avg > self.break_threshold_mid):
            self.is_break = True
        else:
            self.is_break = False

        if was_break and not self.is_break:
            # Uscita da un momento di silenzio/break: punto di resync
            # naturale per la griglia di beat - si riparte da "beat 1"
            # interno invece di trascinarsi dietro il conteggio di prima
            # della pausa (durante la quale il BPM/la fase non erano
            # comunque affidabili).
            self.beat_count = 0
            self.next_beat_time = 0.0

        self._update_beat_grid(now)

    def _correct_beat_phase(self, kick_time_s):
        """Corregge GRADUALMENTE (non uno snap secco) next_beat_time verso
        il kick reale appena rilevato - stesso principio dello smoothing
        gia' usato per il BPM (vedi _update_bpm): un aggancio troppo rigido
        "scatterebbe" ad ogni piccola imprecisione, uno smoothing lo rende
        stabile pur restando capace di seguire il tempo reale."""
        if self.bpm <= 0 or self.next_beat_time <= 0:
            return
        beat_period = 60.0 / self.bpm
        error = kick_time_s - self.next_beat_time
        # Se l'errore supera mezzo periodo, il kick e' probabilmente il beat
        # prima/dopo quello previsto, non lo stesso arrivato storto -
        # normalizza prima di correggere.
        if error > beat_period / 2:
            error -= beat_period
        elif error < -beat_period / 2:
            error += beat_period
        self.next_beat_time += error * self.BEAT_PHASE_CORRECTION

    def _update_beat_grid(self, now):
        """Griglia di beat prevista (next_beat_time/is_beat/beat_count),
        aggiornata ad OGNI blocco audio (non solo sui kick reali) - mantiene
        un battito stabile anche in un breve buco senza kick reali, invece
        di dipendere esclusivamente dagli onset grezzi come is_kick."""
        self.is_beat = False
        if self.bpm <= 0:
            return
        beat_period = 60.0 / self.bpm
        if self.next_beat_time <= 0:
            self.next_beat_time = now + beat_period
            return
        if now >= self.next_beat_time:
            self.is_beat = True
            self.beat_count += 1
            self.next_beat_time += beat_period
            # Se siamo rimasti troppo indietro (es. un buco lungo non
            # coperto dal resync su BREAK), risincronizza subito invece di
            # emettere una raffica di beat "recuperati" tutti insieme.
            if now - self.next_beat_time > beat_period:
                self.next_beat_time = now + beat_period
    
    def get_metrics(self):
        """Ritorna metriche audio correnti"""
        with self.lock:
            if not self.bass_history:
                return {
                    "bass": 0, "mid": 0, "high": 0,
                    "is_kick": False, "is_drop": False, "is_break": False,
                    "db_level": self.db_level,
                    "peak": self.last_peak, "clipping": self.clipping,
                    "bpm": self.bpm,
                    "is_beat": False, "beat_count": self.beat_count,
                    "bass_avg_long": 0,
                }

            return {
                "bass": self.bass_history[-1],
                "mid": self.mid_history[-1],
                "high": self.high_history[-1],
                "bass_avg": np.mean(list(self.bass_history)[-30:]),
                "mid_avg": np.mean(self.mid_history) if self.mid_history else 0,
                "high_avg": np.mean(self.high_history) if self.high_history else 0,
                # Bass normalizzato sul tetto AGC LENTO (vedi AGC_RELEASE_LONG)
                # - resiste all'erosione del tetto veloce durante un break
                # prolungato, usato da brain.py come segnale di break piu'
                # robusto (vedi BREAK_LONG_FLOOR_PCT).
                "bass_avg_long": np.mean(list(self.bass_history_long)[-30:]) if self.bass_history_long else 0,
                "is_kick": self.is_kick,
                "is_drop": self.is_drop,
                "is_break": self.is_break,
                "db_level": self.db_level,
                "peak": self.last_peak,
                "clipping": self.clipping,
                "bpm": self.bpm,
                "is_beat": self.is_beat,
                "beat_count": self.beat_count,
            }
