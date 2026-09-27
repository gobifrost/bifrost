# Local Kubernetes Runtime Spike Implementation Plan

> **For agentic workers:** Use subagent-driven-development for bounded tasks; root owns integration and live validation.

**Goal:** Run Bifrost and KEDA locally, measure worker scaling and memory, prove worker draining, and produce operator configuration guidance from observed results.

**Architecture:** An isolated Kind cluster runs locally built production images and disposable dependencies. Kubernetes operators own scaling configuration. Existing Compose workflows remain unchanged. This is a diagnostic spike, not production enablement of the broader runtime-policy design.

**Tech Stack:** Kind, Kubernetes, KEDA, RabbitMQ, Python, existing Docker test runner.

## Task 1: Repeatable local cluster

- [x] Add `scripts/kubernetes/` lifecycle and `k8s/local/` manifests with pinned Kind/KEDA versions, explicit kubeconfig, local image loading, dependency readiness and failure collection.
- [x] Integrate `./test.sh kubernetes up|status|down|collect|kubectl` before Compose environment loading.
- [x] Run a real local cluster, migrations, API, scheduler and worker.
- [x] Default to operator-owned scaling with automatic scale-down disabled until execution-aware behavior has been measured.

## Task 2: Workload and scaling proof

- [x] Add `api/scripts/elastic_runtime_spike.py` to submit deterministic inline executions through the existing producer, broker and protected child path, returning terminal results and producer-to-result latency.
- [x] Measure fixed warm baseline, zero-to-one activation and loaded scale-out, collect resource observations and verify each submitted execution's result.
- [x] Exercise worker pod deletion during an active execution and inspect terminal status.
- [x] Explicitly distinguish producer-to-result benchmark from browser/API request latency; do not claim production durability or exactly-once external side effects.

## Task 3: Existing worker drain

- [x] Inspect active-child/result-callback accounting and add regression coverage before fixing confirmed shutdown gaps.
- [x] Run targeted worker/consumer/pool tests with `./test.sh`; run `./test.sh quality api` after backend changes.
- [x] Verify bounded grace completion and explicit interruption behavior in the local cluster.

## Task 4: Memory attribution

- [x] Add stdlib-only isolated-stage import and live cgroup/PSS measurements in `api/scripts/elastic_runtime_memory.py`.
- [x] Measure idle and loaded worker memory, record image and concurrency, and distinguish warm-template benefit from unexplained growth.
- [x] Report import-only observations honestly; do not call them an implemented protected lean runner.

## Task 5: Handoff

- [x] Document observed results, installation, resource ownership, scaling limits, roles and repeatable local commands.
- [x] Review changes and run scoped contract tests; no PR or production rollout is part of this spike.
- [x] Record unproven portions of the larger design (remote PlatformJob supervision, policy generations, lean protected one-shot runner) explicitly rather than claiming their implementation.
