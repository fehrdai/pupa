#!/usr/bin/env bash
# avvia_luci.sh - avvia / ferma le luci PUPA (lights/pupa_luci.py) sul PC Linux.
#
#   ./avvia_luci.sh            avvia (controlla QLC+ e OBS, poi lancia le luci)
#   ./avvia_luci.sh restart    ferma e riavvia le luci
#   ./avvia_luci.sh stop       ferma le luci (spegnimento pulito, fari a zero)
#   ./avvia_luci.sh status     mostra cosa sta girando
#
# Le luci sono un processo separato da pupa.py: si avviano/riavviano senza toccare il video.
# Per fermare TUTTO durante il live basta F12 in OBS (ferma video e luci).
# Tasti: vedi TASTI.txt. Dettagli: LIGHTS_CONFIG.md.

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR" || exit 1
PROJ="$DIR/QLC+/pupa.qxw"
LOGDIR="$DIR/lights/logs"
STDOUT="$LOGDIR/stdout.log"
mkdir -p "$LOGDIR"
export DISPLAY="${DISPLAY:-:0.0}"
export XAUTHORITY="${XAUTHORITY:-$HOME/.Xauthority}"

CMD="${1:-start}"

port_open()  { ss -tln 2>/dev/null | grep -q ":$1 "; }
luci_pid()   { pgrep -f '^python3 (-u )?lights/pupa_luci\.py' | head -1; }
qlc_pid()    { pgrep -x qlcplus | head -1; }

wait_port() {  # wait_port <porta> <secondi>
    for _ in $(seq 1 "$2"); do port_open "$1" && return 0; sleep 1; done
    return 1
}

stop_luci() {
    local pid; pid="$(luci_pid)"
    if [ -z "$pid" ]; then echo "[LUCI] non stanno girando."; return 0; fi
    echo "[LUCI] stop pulito (PID $pid)..."
    kill -INT "$pid"
    for _ in $(seq 1 10); do kill -0 "$pid" 2>/dev/null || { echo "[LUCI] fermate (fari a zero)."; return 0; }; sleep 1; done
    echo "[LUCI] non risponde: forzo la chiusura e azzero i fari."
    kill -KILL "$pid" 2>/dev/null
    python3 lights/pupa_luci.py --zero
}

ensure_qlc() {
    if port_open 9996; then
        echo "[QLC+] ok (OS2L in ascolto sulla porta 9996)."
        return 0
    fi
    if [ -n "$(qlc_pid)" ]; then
        echo "[QLC+] e' aperto ma NON ascolta OS2L (porta 9996): probabile workspace vuoto o senza -p."
        read -r -p "        Lo chiudo e lo riapro col progetto pupa.qxw? [s/N] " ans
        case "$ans" in s|S|y|Y) kill "$(qlc_pid)"; sleep 2 ;; *) echo "[QLC+] lasciato com'e': senza OS2L le luci non funzionano."; return 1 ;; esac
    fi
    if [ ! -f "$PROJ" ]; then echo "[QLC+] progetto non trovato: $PROJ"; return 1; fi
    echo "[QLC+] avvio col progetto (modalita' operate, -p)..."
    setsid nohup qlcplus -w -o "$PROJ" -p > /tmp/qlc.log 2>&1 < /dev/null &
    if wait_port 9996 25; then echo "[QLC+] ok."; return 0; fi
    echo "[QLC+] non ascolta sulla 9996 dopo 25 s - controlla /tmp/qlc.log"
    return 1
}

show_status() {
    if port_open 9996; then echo "QLC+ : ok (OS2L 9996)"; else echo "QLC+ : NON pronto (9996 chiusa)"; fi
    if port_open 4455; then echo "OBS  : ok (WebSocket 4455)"; else echo "OBS  : WebSocket 4455 chiuso (hotkey luci non disponibili)"; fi
    local pid; pid="$(luci_pid)"
    if [ -n "$pid" ]; then echo "Luci : in esecuzione (PID $pid)"; else echo "Luci : ferme"; fi
    if pgrep -f '^python3 (-u )?pupa\.py' >/dev/null; then echo "Video: pupa.py in esecuzione"; else echo "Video: pupa.py fermo"; fi
    grep -h "\[SUM\]" "$LOGDIR/lights.log" 2>/dev/null | tail -1 | cut -c1-230
}

case "$CMD" in
    status) show_status; exit 0 ;;
    stop)   stop_luci; exit 0 ;;
    restart) stop_luci ;;
    start)  ;;
    *) echo "uso: $0 [start|restart|stop|status]"; exit 2 ;;
esac

if [ -n "$(luci_pid)" ]; then
    echo "[LUCI] gia' in esecuzione (PID $(luci_pid)). Usa '$0 restart' per riavviarle."
    exit 0
fi

ensure_qlc || exit 1
if port_open 4455; then echo "[OBS ] ok (WebSocket 4455)."; else echo "[OBS ] WebSocket 4455 chiuso: le luci partono lo stesso, ma senza i tasti (acceso/spento, livelli)."; fi

echo "[LUCI] avvio..."
: > "$STDOUT"
setsid nohup python3 -u lights/pupa_luci.py > "$STDOUT" 2>&1 < /dev/null &

# attende la prima riga riassuntiva ([SUM], dopo ~10 s) per dare un verdetto
for _ in $(seq 1 25); do grep -q "\[SUM\]" "$STDOUT" 2>/dev/null && break; sleep 1; done
grep -E "QLC\]|HOTKEY\] stato iniziale|AUDIO\] (Preflight|ALERT)|partenza|\[SUM\]|ERROR|Traceback" "$STDOUT" | cut -c1-200
echo "-----"
if grep -q "\[SUM\].*qlc=ok" "$STDOUT"; then
    echo "OK: luci attive. Fermare: F12 in OBS oppure '$0 stop'."
else
    echo "ATTENZIONE: nessuna conferma qlc=ok - controlla QLC+ e $STDOUT"
    exit 1
fi
