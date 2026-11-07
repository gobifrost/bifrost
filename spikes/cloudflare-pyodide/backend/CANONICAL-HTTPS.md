# Canonical engine/SDK with synthetic HTTPS

Prototype 0.1.0; local qualification, not deployed acceptance. The existing
SDK-only entrypoint, private adapter support vocabulary and public host guard
remain unchanged. A generated separate local entrypoint supplies exactly one
fixed synthetic vendor origin in addition to the existing BiFrost SDK routes.
There is no user-supplied domain policy or new authoritative job queue.

prepare-canonical-https.mjs generates that candidate from the existing local
entrypoint with checked single replacements, without editing it. Each admitted
fixture attempt gets its own policy, original canonical cutoff, six-forward
budget and known access/refresh credential refusal list. Normal completion or
stream cleanup closes/removes both capability gates. The candidate installs the
source-pinned HTTPX redirect patch in the fresh child before engine startup.
The original runtime artifact and engine/SDK bytes remain unchanged.

The two fixed tenant workflows perform real SDK config reads and table writes,
then attempt a vendor request containing their actual run token in a header
(403), write synthetic tenant labels to the independent destination (200), and
follow a302 to a refused origin (403 with HTTPX history[302]). They then follow
the existing held-failure/held-success canonical completion path. The observer
records only fixed synthetic labels and whether vendor authorization matches;
it never retains arbitrary request headers. No external vendor, production
secret or Cloudflare invocation is used.

## Observed acceptance

CT128 existing2CPU/8GiB, provider1CPU/2GiB/128PIDs, existing resource-bounded
PostgreSQL/Redis/API stack. API1d33150, enginef49d4ba, SDK139596497,
runtime bundle61d818ae with staged provenance verified before platform effects;
cached dependency image33cd92be. Existing cf CLI local dev and cached workerd.

```sh
timeout -k 5 430 python3 /opt/bifrost-tests/pyodide-ddf316c1/inbox/canonical-https-v2/ct128-canonical-failure.py --https-fixture --revision 2
node --test spikes/cloudflare-pyodide/backend/prepare-canonical-https.test.mjs spikes/cloudflare-pyodide/backend/approved-https-candidate.test.mjs spikes/cloudflare-pyodide/backend/attempt-egress.test.mjs
node spikes/cloudflare-pyodide/backend/verify-canonical-https.mjs
```

Actual supervisor exit0,88.53299230709672seconds whole lifecycle, not startup.
Independent observer recorded exactly /write and /redirect for each tenant,
all with the synthetic vendor authorization. Both canonical logs contain the
HTTPS success marker. One launch each; Failed/Success persisted; fresh state,
peer403, access401 after terminal, stale-owner rejection, duplicate reuse,
lost-ACK reconnect and unchanged replay checks pass. Busy counts[2,2,1,0];
awaited SDK close returned; canonical ACK retirement and fixture cleanup pass.
All owned test-project containers stopped; volumes and separate dev stack kept.

v1 was a preparation-only packet: review caught malformed generated Python
newline escaping before execution. v2 fixes it; the generator regression checks
valid JavaScript, actual Python newlines, exclusive output and unchanged input.
No failed platform run was hidden by that source correction.

## Remaining boundary

This fixed authored fixture still declares the four SDK-only private requirements;
its separately generated entrypoint grants the extra synthetic capability.
It does not demonstrate general workflow capability discovery, trusted tenant
policy selection or production preclaim admission for external HTTPS. Direct
HTTPS requirements remain rejected by the ordinary private adapter. Actual
external TLS, vendor credential injection, refreshed-token egress revocation,
deadline/in-flight-write races, sync clients, full redirect semantics and deployed
canonical dispatch require qualification. CPU cessation, deployed memory and
resource accounting are unchanged acceptance gaps. Admission stays disabled.

The historical staged-runtime verifier pins its changed supervisor to7ebe69195;
it does not relabel the older run as testing this new HTTPS mode.
