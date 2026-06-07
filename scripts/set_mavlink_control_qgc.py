import json
from pathlib import Path


path = Path("/opt/gimli/config/settings.json")
settings = json.loads(path.read_text())
mavlink = settings.setdefault("mavlink", {})
mavlink["control"] = {
    "throttle_axis": "r",
    "steering_axis": "x",
    "throttle_invert": False,
    "steering_invert": False,
    "throttle_scale": 1.0,
    "steering_scale": 1.0,
}
settings.setdefault("rc_input", {})["mix_mode"] = "tracks"
path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({"mavlink_control": mavlink["control"], "rc_mix_mode": settings["rc_input"]["mix_mode"]}, indent=2))
