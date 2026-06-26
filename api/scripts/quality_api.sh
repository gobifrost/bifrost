#!/bin/sh
set -eu

cd /app

python - <<'PY'
import json
from pathlib import Path

config = json.loads(Path("pyrightconfig.json").read_text())
config.pop("venvPath", None)
config.pop("venv", None)
Path("pyrightconfig.docker.json").write_text(json.dumps(config, indent=2) + "\n")
PY

pyright --project pyrightconfig.docker.json --pythonpath /usr/local/bin/python
ruff check .
