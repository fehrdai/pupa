"""
monitor_align.py - verifica (e opzionalmente corregge) il layout dei monitor
fisici usati da PUPA per l'alternanza (window_manager.py -> LinuxWindowManager).

Su Linux, LinuxWindowManager identifica le finestre Projector in base alla
loro posizione X sullo schermo (_ids_at(x_position)) - questo funziona SOLO
se i 2 output usati da PUPA (MONITOR_SHOW1_INDEX/MONITOR_SHOW2_INDEX in
secrets_local.py) sono in modalita estesa (posizioni X distinte), non
clonati/sovrapposti alla stessa origine. Utile da rilanciare ogni volta che
si scollegano/ricollegano cavi video, prima di un test live con
l'alternanza attiva.

Uso (sulla macchina Linux, con DISPLAY attivo):
    python3 monitor_align.py                          # solo verifica
    python3 monitor_align.py --fix                     # corregge se serve (layout esteso)
    python3 monitor_align.py --fix --order show2,show1 # inverte l'ordine sinistra/destra

Preferisce interrogare OBS via WebSocket (get_monitor_list, la stessa API
che usa pupa.py) per la mappatura indice->output; se OBS non e' raggiungibile
ripiega sull'ordine riportato da `xrandr --listmonitors`, che di norma
coincide ma non e' garantito identico in ogni configurazione.
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path


def read_secrets():
    path = Path(__file__).parent / "secrets_local.py"
    ns = {}
    exec(path.read_text(), ns)
    return ns


def xrandr_monitors():
    out = subprocess.run(
        ["xrandr", "--listmonitors"], capture_output=True, text=True, check=True
    ).stdout
    monitors = []
    for line in out.splitlines():
        m = re.match(
            r"\s*(\d+):\s*\+?\*?(\S+)\s+(\d+)/\d+x(\d+)/\d+\+(\d+)\+(\d+)\s+(\S+)", line
        )
        if m:
            idx, _, w, h, x, y, name = m.groups()
            monitors.append(
                {"index": int(idx), "name": name, "w": int(w), "h": int(h), "x": int(x), "y": int(y)}
            )
    return monitors


def obs_monitors(secrets):
    """Interroga OBS via get_monitor_list(). Ritorna None se OBS non e' raggiungibile."""
    try:
        from obs_controller import OBSController
    except ImportError:
        return None
    obs = OBSController(
        host=secrets.get("OBS_HOST", "localhost"),
        port=secrets.get("OBS_PORT", 4455),
        password=secrets.get("OBS_PASSWORD", ""),
    )
    try:
        obs.connect()
    except Exception:
        return None
    monitors = obs.get_monitor_list()
    if not monitors:
        return None
    result = []
    for m in monitors:
        d = m if isinstance(m, dict) else m.__dict__
        result.append(
            {
                "index": d.get("monitorIndex"),
                "name": d.get("monitorName", "?"),
                "w": d.get("monitorWidth"),
                "h": d.get("monitorHeight"),
                "x": d.get("monitorPositionX"),
                "y": d.get("monitorPositionY"),
            }
        )
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="applica il layout esteso se serve")
    parser.add_argument(
        "--order",
        default="show1,show2",
        help="ordine sinistra->destra per il fix, es. show1,show2 o show2,show1",
    )
    args = parser.parse_args()

    secrets = read_secrets()
    show1_idx = secrets.get("MONITOR_SHOW1_INDEX")
    show2_idx = secrets.get("MONITOR_SHOW2_INDEX")
    if show1_idx is None or show2_idx is None:
        print("MONITOR_SHOW1_INDEX/MONITOR_SHOW2_INDEX non impostati (o None) in secrets_local.py "
              "- funzionalita' di alternanza disattivata, niente da verificare.")
        return

    monitors = obs_monitors(secrets)
    source = "OBS (get_monitor_list)"
    if monitors is None:
        print("OBS non raggiungibile - uso 'xrandr --listmonitors' come fallback "
              "(l'indicizzazione di norma coincide con quella di OBS, ma non e' garantito).")
        monitors = xrandr_monitors()
        source = "xrandr --listmonitors"

    if not monitors:
        print("Nessun monitor rilevato.")
        sys.exit(1)

    by_index = {m["index"]: m for m in monitors}

    print(f"Monitor rilevati via {source} ({len(monitors)}):")
    for m in monitors:
        tags = []
        if m["index"] == show1_idx:
            tags.append("SHOW1")
        if m["index"] == show2_idx:
            tags.append("SHOW2")
        tagstr = f"  <- {'/'.join(tags)}" if tags else ""
        print(f"  [{m['index']}] {str(m['name']):20s} {m['w']}x{m['h']}+{m['x']}+{m['y']}{tagstr}")

    show1 = by_index.get(show1_idx)
    show2 = by_index.get(show2_idx)
    if show1 is None or show2 is None:
        print(f"\nERRORE: SHOW1_INDEX={show1_idx} o SHOW2_INDEX={show2_idx} non presente tra i "
              f"monitor rilevati - controllare secrets_local.py o i cavi.")
        sys.exit(1)

    if (show1["x"], show1["y"]) != (show2["x"], show2["y"]):
        print(f"\nOK: SHOW1 ({show1['name']}) e SHOW2 ({show2['name']}) sono a posizioni distinte "
              f"({show1['x']},{show1['y']} vs {show2['x']},{show2['y']}) - "
              f"l'identificazione per posizione puo funzionare.")
        return

    print(f"\nPROBLEMA: SHOW1 ({show1['name']}) e SHOW2 ({show2['name']}) sono ALLA STESSA "
          f"POSIZIONE ({show1['x']},{show1['y']}) - probabilmente clonati/sovrapposti anziche' "
          f"estesi. window_manager.py (Linux) identifica le finestre Projector per posizione X: "
          f"con la stessa posizione non puo distinguere i due output e l'alternanza monitor NON "
          f"funzionera' come previsto.")

    xr = {m["name"]: m for m in xrandr_monitors()}
    show1_name, show2_name = show1["name"], show2["name"]

    if show1_name not in xr or show2_name not in xr:
        print("\nNon riesco a mappare i nomi dei monitor OBS sugli output xrandr - "
              "applicare il fix manualmente con `xrandr --output <NOME> --right-of <NOME> --auto`.")
        sys.exit(1)

    order = args.order.split(",")
    left_name, right_name = (
        (show1_name, show2_name) if order[0] == "show1" else (show2_name, show1_name)
    )

    # Ancora i 2 output SHOW a destra di un eventuale monitor "regia" (qualsiasi
    # altro output connesso non gestito da PUPA) senza toccarne posizione o
    # flag primary - la regia e' dove opera l'operatore, non va disturbata.
    other_names = [name for name in xr if name not in (show1_name, show2_name)]
    fix_cmd = ["xrandr"]
    if len(other_names) == 1:
        anchor = other_names[0]
        print(f"\nMonitor 'regia' rilevato: {anchor} - lo lascio invariato (posizione/primary), "
              f"ancoro i due SHOW alla sua destra.")
        fix_cmd += ["--output", left_name, "--auto", "--right-of", anchor]
    elif len(other_names) == 0:
        fix_cmd += ["--output", left_name, "--auto"]
    else:
        print(f"\nATTENZIONE: {len(other_names)} monitor extra oltre a SHOW1/SHOW2 ({', '.join(other_names)}) "
              f"- non scelgo un'ancora automaticamente, posiziono solo i due SHOW l'uno rispetto "
              f"all'altro. Verificare a mano la posizione rispetto alla regia dopo il fix.")
        fix_cmd += ["--output", left_name, "--auto"]
    fix_cmd += ["--output", right_name, "--auto", "--right-of", left_name]

    print(f"\nComando per estendere ({left_name} a sinistra, {right_name} a destra):")
    print("  " + " ".join(fix_cmd))

    if args.fix:
        print("\nApplico...")
        subprocess.run(fix_cmd, check=True)
        after = {m["name"]: m for m in xrandr_monitors()}
        s1, s2 = after.get(show1_name), after.get(show2_name)
        if s1 and s2 and (s1["x"], s1["y"]) != (s2["x"], s2["y"]):
            print(f"OK: ora {show1_name} a {s1['x']},{s1['y']} e {show2_name} a {s2['x']},{s2['y']}.")
        else:
            print("ATTENZIONE: dopo il fix le posizioni risultano ancora uguali - verificare a mano.")
    else:
        print("\n(nessuna modifica applicata - rilanciare con --fix per applicare davvero)")


if __name__ == "__main__":
    main()
