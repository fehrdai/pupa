"""
Monitoraggio stabilita' runtime di PUPA, incrociato con l'audio in ingresso.

Nato dal blocco monitor/OBS ricorrente (entrambe le uscite show nere e ferme
sotto carico, causa non ancora confermata - vedi PUPA_ARCHITECTURE.md): le
uniche prove dirette finora (GetStats di OBS - fps/frame renderizzati o
persi) venivano raccolte da uno script Linux a parte (resource_monitor.py),
lanciato a mano solo durante un test esplicito, mai durante un vero live.
Qui gira invece SEMPRE dentro pupa.py stesso, con alert (non solo log
passivo) e con lo stato di audio/brain allegato ad ogni riga - un futuro
blocco si legge cosi' da un solo file (logs/runtime_health.log) invece di
incrociare a mano audio_levels.log/debug.log su macchine diverse.
"""
import time
from debug_logger import setup_debug_logger

_health_logger = setup_debug_logger(
    name="pupa_health", log_file="runtime_health.log",
    max_bytes=2_000_000, backup_count=5
)


class RuntimeMonitor:
    # OBS GetStats: non ad ogni frame (~20Hz sarebbe inutile e pesante sul
    # WebSocket) - un campione ogni 2s basta per cogliere in flagrante un
    # blocco lungo secondi/minuti come quello osservato dal vivo.
    OBS_POLL_INTERVAL_S = 2.0

    # LOOP LATENCY: stessa soglia gia' in uso ad hoc in pupa.py (atteso
    # ~50ms, time.sleep(0.05)), qui promossa ad alert vero con cooldown,
    # piu' un secondo livello ("sostenuto") se si ripete piu' volte in poco
    # tempo - distingue un singolo blip (GC, un I/O lento un istante) da un
    # vero rallentamento prolungato come quello del blocco monitor.
    LOOP_LATENCY_THRESHOLD_S = 0.15
    LOOP_LATENCY_ALERT_COOLDOWN_S = 10.0
    LOOP_LATENCY_SUSTAINED_COUNT = 3        # quante soglie superate...
    LOOP_LATENCY_SUSTAINED_WINDOW_S = 30.0  # ...entro questa finestra = "sostenuto"

    # OBS GetStats: alert se render/output iniziano DAVVERO a perdere
    # fotogrammi tra un poll e il successivo (un delta, non il contatore
    # assoluto - quello cresce anche su installazioni sane con qualche skip
    # storico in avvio) - o se l'fps crolla sotto una frazione del proprio
    # basale (misurato dal vivo sui primi poll, diverso per macchina/canvas,
    # niente soglia assoluta indovinata - stesso principio dell'inseguitore
    # di picco AGC in audio_analyzer.py).
    RENDER_SKIP_DELTA_ALERT = 3
    OUTPUT_SKIP_DELTA_ALERT = 3
    FPS_BASELINE_SAMPLES = 5
    FPS_DROP_FRACTION_ALERT = 0.5  # sotto meta' del basale
    OBS_STATS_ALERT_COOLDOWN_S = 15.0

    # GetStats stesso che fallisce/non risponde: il segnale piu' diretto
    # possibile che OBS o il WebSocket sono in difficolta' - proprio la
    # domanda aperta sul blocco monitor ("il websocket resta vivo?",
    # finora verificato solo a mano durante un test dal vivo).
    GETSTATS_FAIL_ALERT_AFTER = 2  # fallimenti consecutivi (a 2s/poll = 4s)
    GETSTATS_FAIL_ALERT_COOLDOWN_S = 15.0

    def __init__(self, obs):
        self.obs = obs
        self._last_obs_poll = 0.0

        self._prev_render_total = None
        self._prev_render_skip = None
        self._prev_output_total = None
        self._prev_output_skip = None

        self._fps_samples = []
        self._fps_baseline = None

        self._getstats_fail_count = 0
        self._last_getstats_alert = 0.0
        self._last_obs_stats_alert = 0.0

        self._loop_latency_hits = []  # timestamp degli ultimi superamenti soglia
        self._last_loop_alert = 0.0
        self._last_loop_sustained_alert = 0.0

    def tick(self, current_time, tick_gap, audio_metrics, brain_state, current_scene):
        """Chiamata una volta per ciclo del loop principale di pupa.py -
        internamente si auto-limita (OBS_POLL_INTERVAL_S) per la chiamata
        costosa a OBS, quindi e' sicuro invocarla ad ogni frame (~20Hz)."""
        if tick_gap is not None:
            self._check_loop_latency(current_time, tick_gap, audio_metrics, brain_state, current_scene)

        if current_time - self._last_obs_poll >= self.OBS_POLL_INTERVAL_S:
            self._last_obs_poll = current_time
            self._poll_obs_stats(current_time, audio_metrics, brain_state, current_scene)

    def _context(self, audio_metrics, brain_state, current_scene):
        """Riga compatta di contesto incrociato, allegata ad ogni alert e ad
        ogni campione periodico - permette di leggere in UNA riga cosa
        stava facendo l'audio E lo stato/scena di PUPA nello stesso istante,
        invece di dover incrociare a mano piu' file per timestamp."""
        a = audio_metrics or {}
        return (f"audio[bass={a.get('bass', 0):.0f} mid={a.get('mid', 0):.0f} "
                f"high={a.get('high', 0):.0f} dB={a.get('db_level', -60.0):.1f} "
                f"peak={a.get('peak', 0.0):.2f} clip={a.get('clipping', False)} "
                f"bpm={a.get('bpm', 0.0):.0f}] stato={brain_state} scena={current_scene}")

    def _check_loop_latency(self, now, tick_gap, audio_metrics, brain_state, current_scene):
        if tick_gap <= self.LOOP_LATENCY_THRESHOLD_S:
            return

        self._loop_latency_hits = [t for t in self._loop_latency_hits
                                    if now - t <= self.LOOP_LATENCY_SUSTAINED_WINDOW_S]
        self._loop_latency_hits.append(now)

        ctx = self._context(audio_metrics, brain_state, current_scene)

        if now - self._last_loop_alert >= self.LOOP_LATENCY_ALERT_COOLDOWN_S:
            self._last_loop_alert = now
            msg = f"[ALERT][LOOP] ciclo PUPA rallentato: gap={tick_gap * 1000:.0f}ms (atteso ~50ms) | {ctx}"
            print(msg)
            _health_logger.warning(msg)

        if (len(self._loop_latency_hits) >= self.LOOP_LATENCY_SUSTAINED_COUNT and
                now - self._last_loop_sustained_alert >= self.LOOP_LATENCY_SUSTAINED_WINDOW_S):
            self._last_loop_sustained_alert = now
            msg = (f"[ALERT][LOOP] SOSTENUTO: {len(self._loop_latency_hits)} rallentamenti negli "
                   f"ultimi {self.LOOP_LATENCY_SUSTAINED_WINDOW_S:.0f}s - PUPA sta aspettando la CPU "
                   f"insieme a OBS, non solo OBS da sola | {ctx}")
            print(msg)
            _health_logger.warning(msg)

    def _delta_since_last(self, total, skip, prev_total_attr, prev_skip_attr):
        """Delta di fotogrammi persi rispetto all'ultimo poll (non il
        contatore assoluto). 0 se e' il primo poll o se OBS ha resettato i
        contatori (es. riavvio dell'output) - un total piu' basso del
        precedente non e' un delta negativo valido."""
        prev_total = getattr(self, prev_total_attr)
        prev_skip = getattr(self, prev_skip_attr)
        delta = 0
        if prev_total is not None and total >= prev_total:
            delta = skip - prev_skip
        setattr(self, prev_total_attr, total)
        setattr(self, prev_skip_attr, skip)
        return max(0, delta)

    def _poll_obs_stats(self, now, audio_metrics, brain_state, current_scene):
        stats = self.obs.get_render_stats()
        ctx = self._context(audio_metrics, brain_state, current_scene)

        if stats is None:
            self._getstats_fail_count += 1
            if (self._getstats_fail_count >= self.GETSTATS_FAIL_ALERT_AFTER and
                    now - self._last_getstats_alert >= self.GETSTATS_FAIL_ALERT_COOLDOWN_S):
                self._last_getstats_alert = now
                msg = (f"[ALERT][OBS] GetStats fallito {self._getstats_fail_count}x di fila - "
                       f"OBS/WebSocket potrebbe essere in difficolta' | {ctx}")
                print(msg)
                _health_logger.warning(msg)
            return

        if self._getstats_fail_count >= self.GETSTATS_FAIL_ALERT_AFTER:
            msg = f"[OBS] GetStats tornato a rispondere dopo {self._getstats_fail_count} fallimenti | {ctx}"
            print(msg)
            _health_logger.warning(msg)
        self._getstats_fail_count = 0

        fps = stats["fps"]
        render_skip_delta = self._delta_since_last(
            stats["render_total"], stats["render_skipped"], "_prev_render_total", "_prev_render_skip"
        )
        output_skip_delta = self._delta_since_last(
            stats["output_total"], stats["output_skipped"], "_prev_output_total", "_prev_output_skip"
        )

        if self._fps_baseline is None and fps > 0:
            self._fps_samples.append(fps)
            if len(self._fps_samples) >= self.FPS_BASELINE_SAMPLES:
                self._fps_baseline = sum(self._fps_samples) / len(self._fps_samples)

        alerts = []
        if render_skip_delta >= self.RENDER_SKIP_DELTA_ALERT:
            alerts.append(f"render_skip +{render_skip_delta} in {self.OBS_POLL_INTERVAL_S:.0f}s")
        if output_skip_delta >= self.OUTPUT_SKIP_DELTA_ALERT:
            alerts.append(f"output_skip +{output_skip_delta} in {self.OBS_POLL_INTERVAL_S:.0f}s")
        if self._fps_baseline and fps < self._fps_baseline * self.FPS_DROP_FRACTION_ALERT:
            alerts.append(f"fps={fps:.1f} sotto meta' del basale ({self._fps_baseline:.1f})")

        if alerts and now - self._last_obs_stats_alert >= self.OBS_STATS_ALERT_COOLDOWN_S:
            self._last_obs_stats_alert = now
            msg = (f"[ALERT][OBS] {', '.join(alerts)} | avg_render={stats['avg_render_time_ms']:.1f}ms "
                   f"cpu_obs={stats['cpu_usage']:.0f}% | {ctx}")
            print(msg)
            _health_logger.warning(msg)

        _health_logger.debug(
            f"fps={fps:.1f} avg_render={stats['avg_render_time_ms']:.1f}ms "
            f"render_skip_tot={stats['render_skipped']} output_skip_tot={stats['output_skipped']} "
            f"cpu_obs={stats['cpu_usage']:.0f}% | {ctx}"
        )
