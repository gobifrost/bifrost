import json, sys
from pathlib import Path

text = Path(sys.argv[1]).read_text()
start = text.rindex("\n{\n") + 1 if "\n{\n" in text else text.index("{\n")
r = json.loads(text[start:])
print("ok", r["ok"])
s = r["schedules"]
print("callbacks_ok", s["callbacks_ok"], {k: v.get("error") for k, v in s["callbacks"].items() if not v["ok"]})
print("slowest", sorted(((v.get("seconds", 0), k) for k, v in s["callbacks"].items()), reverse=True)[:3])
print("leader", s["startup_schedules_run_by_leader"], s["missing_leader_runs"])
print("failed runs", s["recent_failed_runs"])
print("platform_job", r["platform_job"])
print("workflows", r["workflows"])
