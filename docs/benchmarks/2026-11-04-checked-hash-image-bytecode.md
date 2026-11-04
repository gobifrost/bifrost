# Checked-hash platform bytecode screen

This proposal compiles immutable platform Python source during image construction,
after CLI artifact packaging and before npm adds dependency trees under `src`.
`-f` replaces any pre-existing timestamp caches; checked hashes preserve source
invalidation even when a replacement has the same size and modification time.
Python assertions, package synchronization, full agent preloading, readiness,
credentials, admission and worker lifecycle remain unchanged.

The [retained component samples](2026-11-04-checked-hash-image-bytecode.json) use a
published official image at source `457134df`, not a built image of this proposal's
newer base. Three fresh-interpreter samples per variant measured agent imports,
mapper configuration and the pricing snapshot. Compilation ran in a separate
process before the candidate's measured interpreter. Exact source and loaded-module
fingerprints matched (926 source files, 2,315 loaded modules). Before/after checks
passed. Both variants pre-read source; host image/page cache was otherwise
uncontrolled and likely cached.

| Scope | Baseline | Candidate |
|---|---:|---:|
| Component median | 6,772.355 ms | 6,123.928 ms |
| Individual samples | 6,768.902 / 6,772.355 / 6,813.674 ms | 6,136.349 / 5,990.991 / 6,123.928 ms |
| Added uncompressed bytecode | 0 | 14,469,678 bytes |

The component reduction is 648.427 ms (9.6%). Median compile preparation was
3,477.971 ms, excluded from this hypothetical build-time candidate's runtime
scope. Three samples do not support p95 or a statistical significance claim.
Do not add this reduction to overlapping import or inventory microbenchmarks.

The exact proposed compile command was also exercised in a disposable container:
all 926 platform-source cache headers had checked-hash flags, and a source edit
with unchanged size/mtime loaded the new value. The timestamp-cache control loaded
the old value, proving the exposing condition. Fresh agent imports, mapper setup
and snapshot type matched the original module fingerprint; the container was
removed. No application source was patched in a running service.

## Reproduce the component

Save the following as `/tmp/agent-preload-bytecode-bench.py`. It runs **inside**
the pinned image. Its measured child starts fresh in both modes; compiler imports
cannot warm that child's module graph. The output source/module fingerprints must
match between variants. This is an isolated component screen, not a worker test.

```python
import argparse,compileall,hashlib,importlib.util,json,pathlib,py_compile,subprocess,sys,time
parser=argparse.ArgumentParser();parser.add_argument('--precompile',action='store_true');args=parser.parse_args()
files=sorted(p for d in ['/app/src','/app/shared','/app/bifrost']for p in pathlib.Path(d).rglob('*.py')if 'node_modules'not in p.parts)
digest=hashlib.sha256()
for p in files:digest.update(str(p).encode()+b'\0'+p.read_bytes())
started=time.perf_counter()
if args.precompile:
 for directory in ['/app/src','/app/shared','/app/bifrost']:
  assert compileall.compile_dir(directory,force=True,quiet=2,invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
 caches=[pathlib.Path(importlib.util.cache_from_source(str(p)))for p in files]
 assert all(int.from_bytes(p.read_bytes()[4:8],'little')==3 for p in caches)
else:caches=[]
compile_ms=(time.perf_counter()-started)*1000
probe="import time,json,sys,hashlib\nwall=time.perf_counter();cpu=time.process_time()\nimport asyncpg\nimport pydantic_ai.models.openai\nimport pydantic_ai.providers.openai\nimport sqlalchemy.dialects.postgresql.asyncpg\nfrom src.jobs.agent_run_child import run_agent_message\nimport src.jobs.consumers.agent_run\nimport src.services.agent_executor\nimport src.services.agent_runtime.tool_policy\nfrom genai_prices.data_snapshot import get_snapshot\nfrom sqlalchemy.orm import configure_mappers\nconfigure_mappers();snapshot=get_snapshot()\ncpu_ms=(time.process_time()-cpu)*1000;wall_ms=(time.perf_counter()-wall)*1000\nassert src.jobs.agent_run_child.__file__.startswith('/app/') and type(snapshot).__name__=='DataSnapshot'\nmodules=sorted(sys.modules)\nprint(json.dumps({'wall_ms':wall_ms,'metrics':{'cpu_ms':cpu_ms},'module_hash':hashlib.sha256(json.dumps(modules).encode()).hexdigest(),'snapshot_type':type(snapshot).__name__,'modules':modules}))\n"
result=subprocess.run([sys.executable,'-c',probe],capture_output=True,text=True,check=True)
value=json.loads(result.stdout.splitlines()[-1]);value.pop('modules')
value.update(source_hash=digest.hexdigest(),source_files=len(files),precompiled_bytes=sum(p.stat().st_size for p in caches),compile_ms=compile_ms)
print(json.dumps(value))

```

```sh
IMAGE=ghcr.io/gobifrost/bifrost-api@sha256:782288689f7ab0c1e95ff1992f13ada09044293320197df945c748400ed536c3
# Repeat each variant three times, retaining every JSON result and failure.
docker run --rm -i --platform linux/amd64 --network none --cpus 0.5   --memory 768m --pids-limit 32 --user 1000:1000 --entrypoint python   "$IMAGE" - < /tmp/agent-preload-bytecode-bench.py
docker run --rm -i --platform linux/amd64 --network none --cpus 0.5   --memory 768m --pids-limit 32 --user 1000:1000 --entrypoint python   "$IMAGE" - --precompile < /tmp/agent-preload-bytecode-bench.py
```

## Acceptance still required

Build and validate the actual candidate release image through normal producer,
signing and publication gates. Measure compressed layer growth and complete
zero-capacity startup on the target runtime, including image distribution,
package synchronization, both templates, enrollment and first execution. Keep
warm and restarted instances separate and preserve handler correctness, fencing,
cancellation, draining and memory accounting. This component result does not
establish a Cloudflare speedup or satisfy the separate 30% engineering target.
No candidate image was deployed for these measurements.

The previous same-interpreter compile/import probe was confounded by warmed
compiler imports and is superseded by this fresh-interpreter screen. The first
new harness failed quoting before measurement; no sample from that attempt is
counted. The retained sample data corrects generic orchestrator labels: actual
execution was Linux with 32 PIDs, not the Windows orchestration host or the
inherited 64-PID text label.
