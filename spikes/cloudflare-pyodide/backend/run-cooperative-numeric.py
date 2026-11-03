"""Explicit CT128 fixture combination; no new dispatch or runtime settings."""
from pathlib import Path
import runpy

if __name__ == '__main__':
    controller = runpy.run_path(str(Path(__file__).with_name('ct128-canonical-failure.py')))
    controller['main'](https_fixture=True, numeric_result=True, revision=6)
