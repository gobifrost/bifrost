# Pending pve-t340 test handoff

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
commit read back for this handoff**, never a moving branch head. The final
handoff manifest binds relevant source/fixture hashes. Obtain public source by
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
