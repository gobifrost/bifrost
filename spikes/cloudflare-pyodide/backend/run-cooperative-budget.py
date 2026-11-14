"""Explicit CT128 existing body-budget fixture over candidate RPC."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    controller = runpy.run_path(str(Path(__file__).with_name('ct128-canonical-failure.py')))
    controller['main'](https_fixture=True, oversized_body=True, revision=7)
