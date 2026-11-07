"""Reuse the credential-free cf CLI supervisor for fixed real-engine context probes."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('local_runtime_probe', Path(__file__).with_name('ct128-disposal-probe.py'))
assert spec is not None and spec.loader is not None
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
probe.PACKET = probe.ROOT / 'inbox/public-url-v1'
probe.RUN = probe.ROOT / 'runs/public-url-v1'
probe.NAME = 'pyodide-ddf316c1-public-url-v1'
probe.main(memory='2g')
