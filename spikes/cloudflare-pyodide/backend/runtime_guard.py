"""Companion bootstrap compatibility pin, not workload authorization/isolation."""

QUALIFIED_LOCAL_RUNTIME = {
    "pyodide": "314.0.6",
    "python": [3, 14, 2],
    "cache_tag": "cpython-314",
    "soabi": "cpython-314-wasm32-emscripten",
    "platform": "emscripten-5.0.3-wasm32",
}


class RuntimeCompatibilityError(RuntimeError):
    pass


def runtime_fingerprint():
    import sys
    import sysconfig
    import pyodide

    return {"pyodide": pyodide.__version__, "python": list(sys.version_info[:3]),
            "cache_tag": sys.implementation.cache_tag,
            "soabi": sysconfig.get_config_var("SOABI"), "platform": sysconfig.get_platform()}


def check_runtime(observed):
    if observed != QUALIFIED_LOCAL_RUNTIME:
        raise RuntimeCompatibilityError("Unqualified Python Worker runtime; requalification required")
    return observed


def require_runtime():
    return check_runtime(runtime_fingerprint())
