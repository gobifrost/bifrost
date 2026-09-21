# Replace agent tuning with the evidence→tests→changes workbench

Session: 2026-09-19, worktree `/home/jack/GitHub/bifrost/.worktrees/durable-agent-platform-backend`, HEAD `70ae5f3c7`.
Parent spec (read-only, do NOT edit): `docs/superpowers/specs/2026-09-19-agent-review-testing-loop-design.md`.
Advisory only: `/tmp/agent-review-testing-scope.md` (known errors: backend `debug_links` are API URLs, keep them; `PlatformEvidence.tsx` is shared, do NOT delete).
Direction approved by user: replace the existing Tune workbench in this session; OpenCode executes one slice at a time, parent owns design decisions + final code/UI review. Implementation completed 2026-09-20 per this plan (see /tmp/replace-agent-tuning-handoff.md); parent review pending. No commits pushed.

## Goal / non-goals

Replace `AgentTuneWorkbench` (`/agents/:id/tune`) predictive dry-run journey (single-LLM "would you change answer") with actual synthetic execution on frozen saved tests, consolidated into the same entrypoint. Preserve: flagged-run conversations, suggestions/editing, prompt history, permissions, historical runs, existing run APIs. No auto-apply, no auto-resolution of findings by test pass, no automated discovery/schedules in this pass.

## Compatibility contracts (hold across all slices)

- Keep: `GET /api/agent-runs/{id}`, `/tree`, `/timeline`, `/snapshot`, `/checkpoints`, `debug_links` values (API URLs, retarget UI links only), verdict GET/POST/DELETE, flag-conversation GET/POST message, per-run `POST /api/agent-runs/{id}/dry-run` (backend stays; UI stops calling), journal/snapshot/checkpoint endpoints, `AgentRunVerdictHistory`, `AgentPromptHistory`.
- Change: `POST /api/agents/{id}/tuning-session*` (propose/dry-run/apply) → deprecated; apply path must NOT clear verdicts. New apply goes through normal authorized `PUT /api/agents/{id}` (+ history write, see S5).
- Obsolete UI-only code (exact, no blanket deletion): `client/src/pages/agents/AgentDebuggerPage.tsx` (+ `.test.tsx`), `App.tsx` debug route line, `useTuningSession`/`useTuningDryRun`/`useApplyTuning` in `client/src/services/agentTuning.ts`, `TuningDryRunResults.tsx`, per-run dry-run call sites in `FlagConversation` UI, `AgentEvaluationStudio.tsx` page (retire after S6, see disposition).
- Never delete: `PlatformEvidence.tsx` (shared by Studio + workbench until S6, then by workbench), `agentPlatform.ts`, run-detail inspection, `AgentRunFlagConversation` rows.
- Auth rule: do NOT repeat legacy tuning `_load_agent_with_access` org-only check (`api/src/routers/agent_tuning.py:47`). New endpoints use evaluation-style gating (`_authorized_agent` + `_entity_access_allowed`, `api/src/routers/agent_evaluations.py:78-117`) intersected with `PUT /api/agents/{id}` ownership rules (`api/src/routers/agents.py:722-758`).
- History audit (verified): only `apply_consolidated_tuning` (`api/src/services/execution/tuning_service.py:362`) writes `AgentPromptHistory`; normal `PUT /api/agents/{id}` does not. S5 closes that gap so the replacement records prompt/tool-config changes through the normal update path.

## Schema / contract choices

- New minimal `AgentFinding` ORM (`api/src/models/orm/agent_findings.py`): `id, agent_id FK, org_id, status (open/dismissed), description, expected_behavior, source_kind (run/manual/external), source_run_id?, source_sequence?, external_ref?, linked_case_ids JSONB, created_by, timestamps`. One alembic migration. No change to review queues.
- Cases: add `origin_kind` (`finding/manual/generated/historical_inspiration`) + `finding_id FK nullable` to `AgentEvaluationCase` (+ check-constraint migration); map onto existing `provenance`/`provenance_run_ids` so old readers keep working. `EvaluationCaseCreate` gains the two optional fields — existing `POST /cases` route needs no code change.
- Matrix execution: NO new job table/worker/endpoint family. Extend `agent.evaluation_suite` payload (`api/src/jobs/platform/agent_evaluation.py`) with `matrix {candidate_ids[], profile_ids[], repetitions_override}`; planner fans out cells as child executions under one parent PlatformJob; aggregate result records `planned_cells`, per-cell `status` (`pending/running/succeeded/failed/cancelled`), freeze `case_definitions` + candidate snapshots + profile ids at admission. Baseline-under-same-profile pairing explicit: each candidate cell is compared only against the baseline cell with the same profile id. Never overwrite immutable candidates (`409` on accepted-case/candidate mutation stays).
- Costs: existing quotas (`services/agent_evaluations/quotas.py`) stay; surface per-cell `cost_usd/tokens_used` + pre-run estimate; estimates labeled estimates, never hard caps.

## Slices (strict order, non-overlapping file ownership)

### S1 — Consolidate run inspection; remove standalone debugger (client only)
Owns: `client/src/App.tsx` (debug-route lines only), `AgentDebuggerPage.tsx` (+test, delete), `AgentRunDetailPage.tsx`, `AgentActivityWorkspace.tsx`, `services/agentPlatform.ts`, debug-link lines in `ExecutionResults.tsx`, `TestDesigner.tsx`, `AgentEvaluationStudio.tsx`, `EvaluationExecutionLink.tsx`. Keeps `PlatformEvidence.tsx`, `RunAIUsageCard`, WS updates.
Do: embed tree/snapshot/checkpoints into run-detail Advanced (only when relevant; empty infra fields hidden); retarget all `/debug` links to run detail with `?sequence=` identity; delete debugger route. Backend untouched.
Accept: `tsc+lint`, `AgentRunDetailPage`/`AgentActivityWorkspace` vitest, grep zero `/debug` in client, run-activity regression passes.

### S2 — Minimal findings backend
Owns (new only + registration): `api/src/models/orm/agent_findings.py`, alembic migration, `api/src/models/contracts/agent_findings.py`, `api/src/routers/agent_findings.py`, router registration line. Auth per compatibility rule; dismiss without test allowed; linking external source grants no fetch/access.
Accept: unit CRUD + dismiss + tenant-isolation tests; DTO parity (`test_dto_flags.py`) if DTOs are CLI-surfaced.

### S3 — Finding→frozen-case seam (no router edits)
Owns: `api/src/models/contracts/agent_evaluations.py` (case-create fields), `api/src/models/orm/agent_evaluations.py` + migration, `api/src/services/agent_evaluations/test_designer.py`. Accepted-case immutability (`409`) and `accepted`/`version` semantics unchanged; passing test never auto-resolves a finding.
Accept: unit model/service tests for origin mapping + double-accept `409`.

### S4 — Saved multi-profile matrix on the shared PlatformJob
Owns: `api/src/jobs/platform/agent_evaluation.py`, `api/src/services/agent_evaluations/executions.py` (+ comparison reuse in `comparison.py`), `api/src/routers/agent_evaluations.py` (execution/batch/result/cancel sections only), `registry.py` (only if a parent-matrix job type is required; prefer payload extension). Reuse `POST /executions` single path via `POST /executions/batch`; shared `GET /api/platform-jobs/{id}` + `.../cancel` + `platform_job_updated` WS; no polling. Freeze suite_version/candidate/profile ids at admission; explicit planned-cell count; partial failures reported per cell, never fake success; cancel propagates to pending cells.
Accept: planner/dedupe unit tests, one e2e enqueue→cancel→results read, concurrency + tenant + synthetic-isolation (`real_calls==0`) boundary tests.

### S5 — Verdict-preserving authorized apply + stale-diff guard (replaces `apply_consolidated_tuning` semantics)
Owns: `api/src/services/execution/tuning_service.py` (remove verdict-clearing; deprecate or 410 `/tuning-session/apply`), `api/src/routers/agents.py` (write `AgentPromptHistory` on `system_prompt`/tool-config change in normal update, incl. `reason`), `api/src/routers/agent_tuning.py` (deprecation headers/410 + migration note; keep GET proposal read-only until S6 if needed). New workbench apply flow: show changed prompt + tool configuration diff → re-read production agent → on mismatch show stale-diff block → explicit user confirm → `PUT /api/agents/{id}`. Verdicts, findings, evidence untouched.
Accept: unit tests proving verdicts preserved + history row written via normal update; stale-mismatch e2e; contract tripwire (`test_contract_version.py`) + types regen.

### S6 — Workbench UI in the existing Tune entrypoint; retire Studio duplicate
Owns: `AgentTuneWorkbench.tsx` (+ `Tuning*` subcomponents), `services/agentTuning.ts` (delete predictive hooks; thin hooks over findings/cases/matrix/apply), `components/agents/evaluation/*` (guided expectation + mock-response controls, JSON advanced; reuse `CaseEditor`/`TestDesigner`/`CandidateEditor`/`ExecutionResults`), `FlagConversation.tsx` (keep history + message; remove dry-run turn calls), `services/agentRuns.ts` (remove dry-run hook), `App.tsx` (studio-route lines only → redirect `/agents/:id/studio` → `/agents/:id/tune?tab=tests`), `AgentEvaluationStudio.tsx` (delete after redirect). Keep `/agents/:id/tune` route; Evidence/Tests/Changes-&-Results as tabs with selected agent/suite/candidate/profiles persisted in URL query state; Review page unchanged, findings integrate into its existing context. No new color system/sidebar; existing design tokens only. Never auto-apply.
Accept: component vitest for new/changed components, one Playwright happy journey (finding→case→candidate→matrix→explicit apply, verdict/history preserved), evidence deep-link regression.

### S7 — Focused verification + parent visual review (no product files)
Owns: test files only — new `api/tests/unit/test_agent_findings.py`, matrix/boundary e2e, `client/e2e/agents-tuning-replacement.admin.spec.ts`, updates to `agents-tuning-apply` specs. Full gates (`./test.sh pre-pr`, full Vitest/Playwright) only after final shared-boundary change (S4/S5), not per slice. Parent desktop + mobile visual review required before merge.

## Migration / commands (run at the slice that changes the boundary)

```bash
./test.sh stack up   # once per worktree; state auto-resets per run (planning only — not run now)
./test.sh tests/unit/test_agent_findings.py -v
./test.sh tests/e2e/platform/test_agent_evaluation_matrix.py -v
./test.sh tests/unit/test_dto_flags.py ./test.sh tests/unit/test_contract_version.py
./debug.sh status | grep -q "Status:   UP" || ./debug.sh up
(cd client && npm run generate:types)   # after any contract change
(cd client && npm run tsc && npm run lint) ; ./test.sh quality api
./test.sh client e2e e2e/agents-tuning-replacement.admin.spec.ts
```

## Unresolved product decisions (flagged, not redesigned here)

1. Pre-run cost-budget numbers: quotas exist but no hard spend-cap values — needs explicit budget/threshold decision (estimates stay truthful until then).
2. `/agents/:id/studio` after S6: redirect (planned) vs 404 — redirect assumed; parent confirms.
3. Flag-conversation `POST message` stays writable (assumed yes, read-only history preserved); only predictive dry-run turns are removed.
4. Stale-diff on apply: hard block (planned) vs warn-and-continue — block assumed; parent confirms.
