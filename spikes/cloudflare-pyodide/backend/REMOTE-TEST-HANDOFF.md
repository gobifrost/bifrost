# pve-t340 test offload

## Observed CT128 qualification, owner run68

The newer delivery 304a9578-18c2-4a5c-ad77-c0fd98e9c682 was acknowledged in
native run68. Existing pve-t340 SSH -> pct exec 128 access was verified without
new credentials. owner-binding.json assigns this thread the exclusive
/opt/bifrost-tests/pyodide-ddf316c1 scratch. Guest identity was rechecked:
bifrost-pyodide-test-01, 172.16.15.181, 2 vCPU, 4096 MiB, zero swap, onboot=0,
unprivileged, independent 40 GiB root disk and about 32 GiB free.

Remote execution **occurred** against pinned source
`4d493d5547a6864cc6337df9144cdf9366652915`, tree
`c3966435749a6952bf6cf06f7a79eaf076ae9712`, in its own clean Git worktree.
There were no staged/unstaged patches or required untracked source fixtures.
The uncommitted transport harness is retained as remote-ct128-qualify.py;
its SHA-256 is c87225f3cdd2c756aa6910ddeb8772305bdabda51c48cd7b72995b055ea6b35c.
It was sent over existing SSH stdin, not copied with private caches/profiles.

The assigned guest already held owner run73's successful 40-case baseline at
0c3241789; those receipts, source, containers and volumes were preserved. No
other test was active. Existing image
sha256:33cd92be03ec518c7b10e6b6abe5fbe19cb46fbf548fb93bee80f17967f54a1b
was reused only after verifying that changes since its source baseline were
limited to spike files and API tests. All four sidecar digest/tag identities
were checked against the pinned manifest. No image build or latest pull ran.

Exact commands, in /opt/bifrost-tests/pyodide-ddf316c1/<source>:

```sh
export BIFROST_SKIP_BUILD=1 BIFROST_TEST_IMAGE_TAG=bifrost-test-7ddfac36
export BIFROST_TEST_UNIT_ONLY=1
./test.sh stack up
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_launcher.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q
```

The supervisor supplied an empty per-run HOME and only the required environment.
Project bifrost-test-da7a5eef used its own disposable DB, Redis, network and
volumes, with no published ports. Both commands exited 0. JUnit: **40 passed,
0 failures/errors/skips**, pytest 3.52 s, supervisor 100.44 s including cleanup.
All owned project containers were stopped and read back as not running; volumes
and logs were preserved. Laptop heavy containers stayed stopped.

The existing guest enforces the 2 vCPU/4 GiB/no-swap ceiling. Supervisor bounds:
900 s and sampled stop guard at 24 GiB disk used. These are not filesystem quotas
or per-container limits. Across 98 one-second guest meminfo samples, the largest
MemTotal-minus-MemAvailable was 1495.07 MiB. This is a sampled guest working-memory
indicator, **not process peak RSS, an absolute peak, or the minimum required RAM**.
4 GiB is sufficient for this subset; no resource expansion is requested.

Raw evidence: results/ct128-run68/receipt.json, test-results.xml,
memory-samples.jsonl and preflight-failure.json. Receipt SHA-256:
142e233549d9fe3fc3c918efe1204878ec89054b6cbac9ebd604398c29559f6c.
Transferred JUnit/memory hashes match the remote receipt. The first preflight
failed before tests because the harness referenced results/remote-test-image-pins
instead of backend/remote-test-image-pins; its receipt is retained unchanged.
After that specific correction a new exclusive run directory was used; no test
failure was rerun. Reuse is refused for any existing run reservation.

This proves Linux remote contract tests and the latest journal-test edits, not
Pyodide execution, cloud acceptance, refresh bridge tests or physical termination.
The next safe remote step is the fixed control-auth fixture on this same isolated
stack, followed by dependency/ABI-qualified Pyodide setup. No new cloud invocation,
production cutover or resource/security change is authorized by this offload.

## Subsequent control-auth qualification, run71

Source eee2a6faf23b3242b633e42b030d6959455a4593 was checked out separately in
the same assigned scratch, with no source patches/untracked fixtures. The
transport harness now supports explicit --control-auth; its baseline mode and
exclusive run68 reservations remain preserved. Invocation from the existing
Windows owner connection:

```powershell
Get-Content -Raw spikes/cloudflare-pyodide/backend/remote-ct128-qualify.py | ssh -o BatchMode=yes -o ConnectTimeout=6 pve-t340 "pct exec 128 -- python3 - --control-auth"
```

The same image/sidecar checks, 900 s/disk guard, empty HOME and 2 CPU/4 GiB guest
envelope applied. After ./test.sh stack up, only the owner project's worker and
scheduler were stopped; its API remained live. Exact Docker fixture invocation
and read-only /spike mount are recorded in results/ct128-run71-control-auth/receipt.json.
All three commands exited 0. Total supervisor duration was 107.33 s.

Actual upstream WebSocket /api/worker-control accepted signed hello generation 1
and reconnect generation 2. Invalid signature, nonce replay, superseded socket
and stale generation each closed with 1008. Canonical DB assertions verified no
invalid owner acquisition/advance, empty supported handlers, Starting state and
zero claims. Linux template processes were not started. Fixture worker deletion
was verified; signed hello material was not retained in evidence.

All project bifrost-test-a68d5ca1 containers were stopped, read back empty, and
volumes/logs preserved. Receipt SHA-256:
75e32215702729302f93eefca90c2c11d85fddc0db8a8923e0bc7ec719a292a0.
Memory samples were copied with their recorded hash verified. This qualifies
remote authentication/fencing only: execution_admission_qualified and
physical_termination_verified remain false. No 40-test baseline rerun or cloud
invocation occurred. Next: preserve these handshake semantics when qualifying
physical inventory/adoption; fresh untrusted CPU termination remains blocked.

## Historical pending packet (preserved)

**Acted upon locally; remote execution has not occurred.** Delivery
af7a12d2-0873-4f74-9eab-e6c75099e0f3 was acknowledged in the existing native
run22. The active probe finished before checkpoint. All own laptop test
infrastructure and helper containers were then stopped; no volumes, uncertain
locks or other projects were removed. No further heavy laptop batch is allowed.

## Prerequisites, not permission to run

The existing pve-t340 workspace owner must provide a verified existing guest,
current exclusive test-path/resource lease, existing access route, scratch root,
available capacity and compatible harness/runtime evidence. **VM108/Kata is not
free capacity.** Do not choose a guest, create one, duplicate a writer, change
guest/runtime/security settings, create credentials or spend money to satisfy
this packet. New host/model/runtime configuration authority is not inferred.

The passive existing-caller capacity query c5842180-9153-4841-b5f7-130aa489f58b
was denied with source_run_not_active and safe_to_resend:false. It was not retried
or sent with another caller. The denial and successful delivery ACK are retained
in results/remote-capacity-query-denial.json. Reviewer receipt reconciliation or
the existing owner's explicit verified handoff is required; a new T3 turn is
not a substitute. No guest is currently selected or verified.

## Reproducible source and isolation

Use the public personal fork MTG-Thomas/bifrost-upstream at the **exact published
commit `0c3241789a15e418d02ca8eb6fbd8fac8016edef`**, never a moving branch head.
results/remote-test-handoff.json binds the relevant source/fixture hashes;
the later publication commit adds only this pin/packet and offline verification.
Obtain public source by
existing authorized access; do not copy .env files, production secrets, signed
hello material, SSH/Docker/npm credentials or entire private caches. Do not
reuse the laptop-specific canonical-refresh.mjs container paths remotely.

The owner-approved worktree/scratch namespace is:

```
<verified-owner-scratch>/pyodide-ddf316c1/<exact-source-sha>/
```

The checkout must be an isolated git worktree under the confirmed owner, with
no unrelated dirty files. test.sh computes its Compose project from that
worktree path, uses its own default network and /tmp/bifrost-<project> logs.
Record the actual computed project and image tag; never reuse another owner's
project, database, logs, volumes or ports. The first phase needs no published
host ports, Wrangler, Cloudflare credentials, Pyodide cache or external AI keys.

## Capacity and compatibility gate

Proposed first-phase envelope, **not measured/approved target capacity**: at most
2 vCPU, 8 GiB combined memory, 30 GiB owner scratch and 15 minutes wall time.
The owner must verify those bounds can be enforced using existing authorized
capacity/runner controls without configuration changes, and that they suffice
for the build/stack. Otherwise do not run; report the exact missing bound.
A client timeout does not stop Docker containers: expiry cleanup must stop only
the owner project's actual processes and verify stopped state, preserving logs,
volumes and uncertain locks. Never use down -v or blanket Docker cleanup.

Verify Linux/architecture, existing Docker/Compose, cgroup/tmpfs/bind-mount/network
support and disk/memory/CPU availability on the approved guest before tests.
If an LXC needs nesting, keyctl, AppArmor, privilege or runtime changes, return
that prerequisite to its owner; do not apply it. This installation's skill
catalog supplied no matching Proxmox/LXC procedure, so none is invented here.

API build bases are pinned by api/Dockerfile.dev and Python dependencies by the
tracked locks. remote-test-image-pins.json records the four observed sidecar
digests; Compose currently names mutable tags. Compare actual target digests.
If they differ/miss, obtain an owner-approved isolated pinning/build route;
do not silently accept latest, retag shared images or import laptop caches.
No target dependencies or harness compatibility have been verified yet.

## Smallest first remote phase

Only after the above gates, from the pinned isolated worktree, with optional
GitHub/AI test credentials absent from this test process:

```sh
./test.sh stack up
./test.sh tests/unit/dev/test_pyodide_attempt_journal.py tests/unit/worker/remote/test_launcher.py tests/unit/worker/remote/test_ownership.py tests/unit/worker/remote/test_fencing.py -q
```

This is the existing 40-case protocol/DB phase, not the entire suite or a cloud
test. Capture JUnit, source SHA/digests, actual project/network/container IDs,
resource-limit evidence, image digests, wall time and actual cleanup receipts
under the per-owner results directory. Preserve any failure without retries,
timeout increases or skips. Do not claim remote success from syntax checks.

The next phase, only after harness compatibility, is the actual control-auth
fixture via the same project's test-runner with its own read-only spike mount.
Use Docker internal api/pgbouncer service names. Do not port the laptop's helper
container IDs/absolute paths. Only later qualify actual Pyodide fixtures using
their runtime/ABI guard and reproducibly built public dependencies. No cloud
invocation, canary extension or production migration is authorized by this move.
