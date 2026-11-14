"""Explicit CT128 real engine result exceeding the candidate RPC wire budget."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    controller = runpy.run_path(str(Path(__file__).with_name('ct128-canonical-failure.py')))
    controller['main'](https_fixture=True, oversized_body=True, revision=9)
