"""Bridge diagnostico QLC+ -> file: legge lo stato live (valori DMX reali +
elenco widget) via l'API Web di QLC+ (WebSocket, richiede l'avvio con -w -
vedi docs.qlcplus.org/advanced/web-interface) e lo scrive in un JSON su
disco. Sostituisce gli screenshot per verificare lo stato del rig - QLC+
stesso non ha un log su file nativo per questo, l'API Web e' l'unico modo
di leggere i valori realmente in uscita senza guardare lo schermo.

Uso: python qlc_web_monitor.py [--once] [--interval 1.0] [--out logs/qlc_live_state.json]
"""
import argparse
import json
import time

import websocket


def _parse_channels_values(resp):
    """'QLC+API|getChannelsValues|<idx>|<val>|<color>|<override>|...' ->
    {indice_canale_1based: valore}."""
    parts = resp.split("|")[2:]
    values = {}
    for i in range(0, len(parts) - 3, 4):
        try:
            values[int(parts[i])] = int(parts[i + 1])
        except (ValueError, IndexError):
            continue
    return values


def _parse_widgets_list(resp):
    """'QLC+API|getWidgetsList|<id>|<caption>|...' -> {id: caption}."""
    parts = resp.split("|")[2:]
    widgets = {}
    for i in range(0, len(parts) - 1, 2):
        try:
            widgets[int(parts[i])] = parts[i + 1]
        except (ValueError, IndexError):
            continue
    return widgets


def snapshot(ws, universe=1, start_address=1, count=20):
    ws.send(f"QLC+API|getChannelsValues|{universe}|{start_address}|{count}")
    channels = _parse_channels_values(ws.recv())

    ws.send("QLC+API|getWidgetsList")
    widgets = _parse_widgets_list(ws.recv())

    ws.send("QLC+API|isProjectLoaded")
    project_loaded = ws.recv().split("|")[-1]

    return {
        "timestamp": time.time(),
        "project_loaded": project_loaded,
        "channels": channels,
        "widgets": widgets,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9999)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--out", default="logs/qlc_live_state.json")
    parser.add_argument("--once", action="store_true", help="una sola lettura, poi esce")
    args = parser.parse_args()

    ws = websocket.create_connection(f"ws://{args.host}:{args.port}/qlcplusWS", timeout=5)
    print(f"[QLC-MONITOR] connesso a ws://{args.host}:{args.port}/qlcplusWS")

    try:
        while True:
            data = snapshot(ws)
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"[QLC-MONITOR] scritto {args.out} ({len(data['channels'])} canali, "
                  f"{len(data['widgets'])} widget, project_loaded={data['project_loaded']})")
            if args.once:
                break
            time.sleep(args.interval)
    finally:
        ws.close()


if __name__ == "__main__":
    main()
