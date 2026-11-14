// Fixed local microbenchmark. No BiFrost identity, packages or network capability.
const python=`
import json, statistics, time, pyodide
from workers import WorkerEntrypoint, Response
from pyodide.ffi import to_js
from js import JSON, Object

class Default(WorkerEntrypoint):
    async def fetch(self, request):
        rows = []
        null_value = {"none": None}
        null_probe = {"direct_equal": to_js(null_value, dict_converter=Object.fromEntries).to_py() == null_value,
                      "js_json_equal": JSON.parse(JSON.stringify(to_js(null_value, dict_converter=Object.fromEntries))).to_py() == null_value}
        for size, loops in ((128, 100), (4096, 50), (65536, 10)):
            payload = {"text": "x" * size, "numbers": list(range(32)), "nested": {"ok": True, "label": "ok"}}
            text = json.dumps(payload)
            operations = {
                "python_json_roundtrip": lambda: json.loads(json.dumps(payload)),
                "ffi_roundtrip": lambda: to_js(payload, dict_converter=Object.fromEntries).to_py(),
                "js_json_roundtrip": lambda: JSON.parse(JSON.stringify(to_js(payload, dict_converter=Object.fromEntries))).to_py(),
            }
            samples = {name: [] for name in operations}
            # Rotate order across five samples to avoid a fixed first-method bias.
            names = list(operations)
            for sample in range(5):
                for name in names[sample % 3:] + names[:sample % 3]:
                    operation = operations[name]
                    if operation() != payload:
                        raise RuntimeError("Interop correctness mismatch")
                    started = time.perf_counter()
                    for _ in range(loops):
                        result = operation()
                    elapsed = (time.perf_counter() - started) * 1000 / loops
                    if result != payload:
                        raise RuntimeError("Timed interop correctness mismatch")
                    samples[name].append(elapsed)
            rows.append({"text_bytes": size, "python_json_bytes": len(text.encode()), "loops_per_sample": loops,
                         "samples_ms_per_operation": samples,
                         "median_ms_per_operation": {name: statistics.median(values) for name, values in samples.items()}})
        return Response.json({"pyodide_version": pyodide.__version__, "correctness": True, "null_probe": null_probe, "rows": rows})
`;
export default {
  async fetch(request,env) {
    if(env.LOCAL_INTEROP!=='true' || request.method!=='POST') return new Response('Local POST only',{status:403});
    const stub=env.LOADER.load({compatibilityDate:'2026-10-30',compatibilityFlags:['python_workers'],
      mainModule:'main.py',modules:{'main.py':{py:python}},env:{},globalOutbound:null,
      limits:{cpuMs:1000,subRequests:0}});
    return stub.getEntrypoint().fetch(new Request('https://interop.invalid/'));
  },
};
