"""Qualify the pinned stream-capture closure in actual local Python Workers."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('closure_probe', Path(__file__).with_name('ct128-disposal-probe.py'))
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
probe.PACKET = probe.ROOT / 'inbox/stream-runtime-v1'
probe.RUN = probe.ROOT / 'runs/stream-runtime-v1'
probe.NAME = 'pyodide-ddf316c1-stream-runtime-v1'
probe.main(memory='2g')
