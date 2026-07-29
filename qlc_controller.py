"""
QLC+ Controller - invio eventi via OS2L (TCP + JSON) al plugin OS2L di QLC+.

OS2L e' un protocollo TCP semplice usato dai software DJ (VirtualDJ, Serato)
per sincronizzare luci esterne - QLC+ lo implementa come plugin di ingresso
(porta di default 9996). A differenza di OSC non serve creare un Profilo di
Ingresso: i pulsanti di Console Virtuale si triggerano direttamente per
NOME (l'etichetta del pulsante), i comandi diretti impostano un canale a
un valore, e l'evento beat pulsa un canale dedicato - pensato apposta per
sync BPM/kick, che PUPA calcola gia' in audio_analyzer.py.

Formato messaggi (JSON, nessun delimitatore esplicito richiesto oltre alla
parentesi di chiusura, nessun handshake):
    {"evt": "btn", "name": "<etichetta pulsante>", "state": "on"|"off"}
    {"evt": "cmd", "id": <canale>, "param": <valore 0-255>}
    {"evt": "beat"}

Vedi https://docs.qlcplus.org/v4/plugins/os2l e
plugins/os2l/os2lplugin.cpp nel repo di QLC+.
"""
import json
import socket
import time

from debug_logger import debug as debug_log


class QLCController:
    def __init__(self, host="127.0.0.1", port=9996, reconnect_interval=5.0):
        self.host = host
        self.port = port
        self.sock = None
        # Intervallo minimo (secondi) tra un tentativo di riconnessione e il
        # successivo - senza questo, un QLC+ irraggiungibile per un intero show
        # farebbe ritentare connect() ad ogni singolo invio (decine/sec, un
        # frame audio ciascuno), inutile e rumoroso nel debug.log.
        self.reconnect_interval = reconnect_interval
        self._last_connect_attempt = 0.0

    def connect(self):
        """Apre la connessione TCP persistente verso il plugin OS2L di QLC+."""
        self._last_connect_attempt = time.monotonic()
        try:
            self.sock = socket.create_connection((self.host, self.port), timeout=5)
            debug_log(f"[QLC] connesso ({self.host}:{self.port})")
        except Exception as e:
            debug_log(f"[QLC] connessione OS2L fallita ({self.host}:{self.port}): {e}")
            self.sock = None

    def _ensure_connected(self):
        """Se il socket non e' attivo, ritenta al massimo ogni
        reconnect_interval secondi (vedi commento in __init__) - permette a
        PUPA di recuperare da sola sia un avvio prima di QLC+ sia una caduta
        di connessione a show in corso, senza che pupa.py debba richiamare
        connect() esplicitamente."""
        if self.sock is not None:
            return
        now = time.monotonic()
        if now - self._last_connect_attempt >= self.reconnect_interval:
            self.connect()

    def _send(self, payload):
        self._ensure_connected()
        if self.sock is None:
            return
        try:
            self.sock.sendall(json.dumps(payload).encode("utf-8"))
        except Exception as e:
            debug_log(f"[QLC] invio OS2L fallito: {e}")
            self.sock = None  # ritentato da _ensure_connected() al prossimo invio, non subito

    def trigger_button(self, name, state="on"):
        """Preme (o rilascia, state='off') un pulsante di Console Virtuale per nome/etichetta."""
        self._send({"evt": "btn", "name": name, "state": state})

    def set_channel(self, channel_id, value):
        """Imposta direttamente un canale/parametro a un valore 0-255.
        'id' va mandato come NUMERO JSON, non stringa - nonostante la prosa
        della documentazione OS2L lo mostri tra virgolette (`"id": "1"`), il
        codice sorgente del plugin lo tratta come intero; mandarlo come
        stringa fa si' che QLC+ lo legga sempre come 0 (verificato dal log
        di debug: "Got CMD message 0" per ogni id, quando veniva mandato
        come stringa)."""
        self._send({"evt": "cmd", "id": int(channel_id), "param": value})

    def beat(self):
        """Manda un impulso di beat (pensato per sync BPM/kick su un canale dedicato)."""
        self._send({"evt": "beat"})
