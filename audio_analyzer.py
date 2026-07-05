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
from debug_logger import debug as debug_log


class AudioAnalyzer:
    AGC_WARMUP_BLOCKS = 20  # ~1s a blocksize=2048/44100Hz

    def __init__(self, device=8, channels=2, samplerate=44100, blocksize=2048):
        self.device = device
        self.channels = channels
        self.samplerate = samplerate
        self.blocksize = blocksize
        
        # Thresholds
        self.kick_threshold_bass_delta = 14
        self.kick_threshold_bass_min = 60
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
        
        # Kick detection state
        self.last_kick_time = 0
        self.is_kick = False
        self.is_drop = False
        self.is_break = False
        
        # AGC (Automatic Gain Control)
        self.max_bass = 1.0
        self.max_mid = 1.0
        self.max_high = 1.0
        # Warm-up: il tetto AGC a regime (alpha=0.9995) impiega ~93s a
        # raggiungere il livello reale partendo da 1.0 - per quella finestra
        # bass/mid/high restano tagliati al 100% (nessun kick rilevabile,
        # zero dinamica visibile). Primi AGC_WARMUP_BLOCKS blocchi (~1s)
        # usano un adattamento veloce per "scaldare" subito il tetto, poi si
        # torna al ritmo lento gia' testato a regime.
        self._agc_block_count = 0

        # Livello audio in dBFS (RMS del segnale grezzo, non filtrato per banda),
        # per replicare la logica soglia/tetto del vecchio plugin "Scale to Sound".
        # Smoothed via EMA: senza, il valore per singolo blocco (~46ms) e' molto
        # rumoroso blocco-a-blocco (a differenza di bass/mid/high che gia' usano
        # una media mobile su 30 campioni) e produceva una reattivita' "casuale".
        self.db_level = -60.0
    
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
        
        # AGC: adatta dinamicamente. Primi blocchi (~1s): alpha veloce per
        # scaldare subito il tetto partendo da 1.0, poi ritmo lento originale.
        self._agc_block_count += 1
        if self._agc_block_count <= self.AGC_WARMUP_BLOCKS:
            alpha = 0.7
        else:
            alpha = 0.9995
        self.max_bass = self.max_bass * alpha + bass_mag * (1 - alpha)
        self.max_mid = self.max_mid * alpha + mid_mag * (1 - alpha)
        self.max_high = self.max_high * alpha + high_mag * (1 - alpha)
        
        # Normalizza 0-100
        bass_norm = (bass_mag / max(self.max_bass, 0.001)) * 100
        mid_norm = (mid_mag / max(self.max_mid, 0.001)) * 100
        high_norm = (high_mag / max(self.max_high, 0.001)) * 100
        
        bass_norm = min(100, max(0, bass_norm))
        mid_norm = min(100, max(0, mid_norm))
        high_norm = min(100, max(0, high_norm))
        
        with self.lock:
            self.bass_history.append(bass_norm)
            self.mid_history.append(mid_norm)
            self.high_history.append(high_norm)
            self.db_level = db_level

            # Detect kick/drop/break
            self._detect_events()
    
    def _detect_events(self):
        """Rileva kick, drop, break"""
        if len(self.bass_history) < 2:
            return
        
        current_bass = self.bass_history[-1]
        prev_bass = self.bass_history[-2]
        bass_avg = np.mean(list(self.bass_history)[-30:]) if len(self.bass_history) >= 30 else np.mean(self.bass_history)
        mid_avg = np.mean(self.mid_history) if self.mid_history else 0
        
        # KICK: bass delta > 14
        bass_delta = current_bass - prev_bass
        import time
        current_time = time.time() * 1000
        
        if (bass_delta > self.kick_threshold_bass_delta and 
            current_bass > self.kick_threshold_bass_min and
            (current_time - self.last_kick_time) > self.kick_cooldown_ms):
            self.is_kick = True
            self.last_kick_time = current_time
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
        if (current_bass < self.break_threshold_bass and
            bass_avg > self.break_threshold_bass_hist_avg and
            mid_avg > self.break_threshold_mid):
            self.is_break = True
        else:
            self.is_break = False
    
    def get_metrics(self):
        """Ritorna metriche audio correnti"""
        with self.lock:
            if not self.bass_history:
                return {
                    "bass": 0, "mid": 0, "high": 0,
                    "is_kick": False, "is_drop": False, "is_break": False,
                    "db_level": self.db_level,
                }

            return {
                "bass": self.bass_history[-1],
                "mid": self.mid_history[-1],
                "high": self.high_history[-1],
                "bass_avg": np.mean(list(self.bass_history)[-30:]),
                "mid_avg": np.mean(self.mid_history) if self.mid_history else 0,
                "high_avg": np.mean(self.high_history) if self.high_history else 0,
                "is_kick": self.is_kick,
                "is_drop": self.is_drop,
                "is_break": self.is_break,
                "db_level": self.db_level,
            }
