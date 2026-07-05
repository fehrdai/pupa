import sys
sys.path.insert(0, "pupa")

from obs_controller import OBSController
from secrets_local import OBS_HOST, OBS_PORT, OBS_PASSWORD

obs = OBSController(
    host=OBS_HOST,
    port=OBS_PORT,
    password=OBS_PASSWORD
)

try:
    obs.connect()
    print("[OK] OBS CONNESSO!")
    print(f"Versione: {obs.version}")
except Exception as e:
    print(f"[ERROR] {e}")
    import traceback
    traceback.print_exc()
