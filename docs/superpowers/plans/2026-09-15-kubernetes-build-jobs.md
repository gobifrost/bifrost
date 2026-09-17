# Kubernetes build Jobs implementation plan

> **For agentic workers:** Implement bounded tasks with disjoint file ownership; root serializes Docker tests and owns integration.

**Goal:** Launch resource-isolated App builds on demand while the warm scheduler retains its ordinary work capacity.

**Architecture:** Extend the canonical PlatformJob claim, fencing and runner contracts. An opt-in scheduler reconciliation loop launches deterministic Kubernetes Jobs for build-class PlatformJobs; ordinary local slots exclude those durable assignments. Each remote pod supervises the existing handler child, heartbeats its own lease and reports its own cgroup memory. PostgreSQL remains authoritative for status/progress/results. No new feature queue, worker image or progress UI.

**Tech Stack:** PostgreSQL/SQLAlchemy, existing asyncio runner, httpx Kubernetes REST client, batch/v1 Jobs, Kind.

## Delivery boundary

This implements the original design's controller-assigned Job option for build work, before the broader lanes/settings rollout. Start with application.deploy and application.sdk_update; confirm Solution build scope with the user. Keep all earlier worker-memory improvements and their evidence. Do not advertise workflow autoscaling as fulfilling isolated build execution.

- Default execution remains local. At enqueue, persist local vs Kubernetes placement so replicas cannot disagree about ownership during restart or config changes.
- A build has its own resource request/limit (initial experiment: 1 GiB memory). Scheduler replicas and ordinary claim slots stay available.
- Kubernetes API credentials belong only to the controller scheduler. Execution pods use a separate ServiceAccount with token automount disabled and explicitly configured application Secret/ConfigMap references.
- Reuse the application image and the registered handler. Keep payloads/credentials out of Job manifests; pass only job/attempt identifiers.
- Use deterministic attempt names, persisted Job UID binding, and a single admitted pod UID to prevent duplicate handler startup.
- Create Jobs suspended, persist UID, then activate. A restart can adopt the same named/labelled attempt. No silent local fallback on Kubernetes failure.
- Keep remote attempts out of generic expired-local-lease recovery. Before retrying/failing lost remote work, establish that its bound pod cannot still execute. API outages leave ownership intact.
- A separate pending-capacity deadline covers scheduling/image startup. Execution timeout starts inside the admitted pod. Preserve structured timeout, cancellation, OOM and runner-loss outcomes and existing retry policy.
- Use backoffLimit=0 and restartPolicy=Never; Bifrost owns retry decisions. Bound concurrent remote attempts separately from ordinary scheduler slots.
- Terminal cleanup is UID-bound and does not delete unrelated Jobs. Disabling the integration requires draining existing remote work before removing controller credentials.

## Tasks

- [ ] Durable placement and claim boundary: ORM migration, execution-class policy, opt-in settings, enqueue assignment, backend-filtered claims, remote claim metadata and local recovery exclusion. Preserve dedupe/resource locks/concurrency.
- [ ] Remote pod admission and supervisor: atomic first-pod fence, existing process-group runner, own lease/memory/timeout, signal cancellation and durable terminal state.
- [ ] Kubernetes client and reconciler: secure API access, suspended creation/binding/start, restart adoption, pending reasons/deadline, pod death/OOM, stale lease termination and cleanup. Integrate independent controller task in scheduler lifecycle.
- [ ] Deployment enablement: controller RBAC, unprivileged runner ServiceAccount, configurable 1 GiB template, local Kind opt-in, operator-owned limits/concurrency and rollout/drain instructions.
- [ ] Tests: backend selection/local compatibility; real DB claim exclusion/locks/pod fence; Job rendering/security; controller create/restart/errors/cancellation/OOM; real local build in a Job while a light scheduler job succeeds; scheduler restart and unschedulable-capacity cases.
- [ ] Measure actual build cgroup memory/time and scheduler responsiveness; revise 1 GiB only from evidence.
- [ ] Integrate current origin/main, run scoped checks, review, commit exact candidate and run ./test.sh pre-pr. Prepare PR with partial issue linkage; keep #769 open for broader unimplemented runtime policy/lane work.
