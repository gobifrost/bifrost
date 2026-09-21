# Unified Agent Quality Workbench Implementation Plan

> For agentic workers: use the handoff skill and execute one bounded assignment at a time. The originating agent owns design, contract decisions, integration, and final acceptance. Do not implement this whole document as one assignment.

**Goal:** Replace tuning with a cohesive test workbench covering reviews, findings, recorded-run evaluation, simulated regression testing, and explicit agent changes through the same CLI and UI objects.

**Architecture:** Reuse AgentRun, versioned evaluation cases/candidates, assertion evaluation, synthetic tool isolation, and PlatformJob execution. Add missing contracts deliberately; do not introduce separate workers, job transports or browser polling. Tests are the primary collection, with a Files-style inspector for connected tasks.

**Tech Stack:** Existing FastAPI/SQLAlchemy/PostgreSQL, Python CLI, React/TypeScript, Bifrost components, shared PlatformJob transports.

## Authority and current status

This plan supersedes the navigation, suite-first setup, and exclusions for reviews/automation in `2026-09-19-replace-agent-tuning.md`. It preserves that plan's durable execution, authorization, historical AgentRun compatibility, explicit apply/stale guard, and verdict/evidence preservation requirements. Earlier implementation and test reports are baseline evidence, not acceptance of this expanded scope.

User authorizes planning and bounded handoff, including native Terra/Luna or another suitable native executor. Primary owns design; the selected executor implements specific plans and returns for code, rendered UI, CLI, and intent review. No Bifrost commits, pushes, merge or deployment are included. Existing uncommitted work in this worktree is part of the ongoing feature and must be preserved.

Readiness: revised interactive mockup accepted as prototype after primary browser review. Recorded exact/semantic evaluation, evidence authorization, shared quality accounting, synthetic judge accounting, consistent usage aggregation, and global/recorded/synthetic/designer reporting API/CLI have focused verification and primary review. Integrated API quality passed. Phase 3A review data foundation, Phase 3B on-demand review executor, and shared finding visibility have primary focused verification; Phase 3C review API/CLI is being implemented and reviewed. Integrated API quality must be rerun after this slice stabilizes. The user requires testing/review overhead in both per-run and global reporting; production UI integration remains required. Broad review automation/scheduling, proposed tools/designer and production workbench phases remain unfinished. A passing prototype or backend slice is not feature completion.

## Product model and acceptance criteria

### Reviews and findings

- Reviews contain plain-English review statements, run selection/scope, an evidence-formatting instruction, and on-demand/scheduled execution settings. Reviews find both problems and opportunities; they are not synonymous with test failures.
- Use Reviews, Tests, Findings, Run History as collections of one reusable component. Default is Tests, not setup or summary cards. Agent-level scope and Agents-level searchable cross-agent collections reuse the component with an agent filter. Maintain existing access restrictions.
- Findings retain source review/version, authorized run references, Markdown evidence, independent dismissal, and optional linked tests. Multiple findings can share runs; evaluation deduplicates run IDs. A test pass never dismisses a finding.
- Evidence renders through existing `MarkdownContent`. Review evidence instructions may specify canonical ticket-link templates; prefer integration-provided canonical URLs. Links never confer authorization or cause implicit remote fetches. Formatting is separate from review criteria.
- Finding actions: Investigate, Create Test, Draft Changes, Dismiss. Investigate displays source runs, linked tests, and related recent test failures with agent/profile/version context. Related suggestions are distinguishable from proven matches.

### One test, two execution modes

- Test definition: name (Should/Should Not), applicable situation, expected behavior in plain English, explicit tool/output/budget checks, optional synthetic scenario, version/provenance.
- Evaluate Recorded Runs uses immutable recorded evidence without executing the agent or synthetic tools. A separately configured semantic judge may incur model usage. Freeze judge/rubric settings independently of tested profiles.
- Run Simulation executes real agent runtime with simulated tools and scores the same checks. Freeze scenario, test, agent and profile versions. Draft Changes edits instructions/tool attachments/configuration independently of live agent.
- Historical outcomes distinguish Passed, Failed, Not Applicable and Insufficient Evidence. In-progress, cancelled, execution error and pending judge are execution states, never passing assertions. Unknown applicability is insufficient evidence, not an implicit exclusion.
- A no-call check needs complete tool evidence before proving absence. Simulator-state checks cannot be evaluated against production traces absent equivalent recorded state. Historical data is never silently filled with invented fixtures.
- Historical evaluation answers whether checks detect an observed incident. Simulation answers whether controlled scenarios reproduce it and whether a candidate improves it. Results label source/mode so these are not conflated.
- UI and CLI can evaluate selected runs from run detail/list, selected finding evidence, or the test collection. Bulk jobs preserve each test/run result, including non-applicability and insufficient evidence.

### Test Designer and hypothetical tools

- Normal test creation starts with Situation and Expected Behavior, with readable required/forbidden tool checks. Test Designer drafts scenario state, coherent tool responses and checks from requirements, published tool schemas and explicitly selected authorized run evidence. Generated drafts are reviewed before acceptance.
- Simulated Environment is an advanced inspection/override section, not a mandatory JSON form between request and expectation. Variations (timeout, empty/malformed data, alternate shapes) become explicit versioned scenarios. Accepted fixtures remain reproducible.
- Proposed tools have a name, input/output contract and stateful simulation behavior, marked Simulation Only. They may be attached to a candidate without real workflows. They must never resolve to real dispatch. Applying a candidate with unresolved proposed tools is blocked, with an explicit implementation/binding requirement.
- Simulation proves a proposed tool contract useful, not its real workflow implementation correct. Require separate integration coverage before production binding.

### Shared interaction and words

- One component owns scope, collection, selection, active run and draft. Default list has test names, last result, time, cost, profile context; search, selection, Run All and profile selection in its toolbar.
- Inspector contains result, test editing, investigation, review definition, Draft Changes and apply review. Draft comparison adds columns to the same list. No Evidence → Tests → Changes wizard or separate Changes workflow tab.
- Actions use the agreed labels Draft Changes, Evaluate Recorded Runs, Run Simulation, Review Changes, Apply Changes, Create Test. Use existing Bifrost casing conventions; readable prose stays sentence case.
- Apply is explicit, checks stale base, records reason/history, preserves findings/verdicts/evidence, and refuses unresolved tools. Loading, empty, error, cancelled and partial results remain visible. Mobile returns from inspector without losing context.
- Usage belongs in the existing result inspector, with a compact total and expandable breakdown of execution, simulation, judging, review, and generation. Distinguish source-run cost (context) from newly incurred testing/review cost. Keep global breakdowns in the existing Usage reporting surface, with organization, purpose, provider/model/profile, and date filters; do not add a separate cost application or workbench navigation tab. Show unknown/partial cost coverage beside totals. CLI JSON carries the same attribution and coverage so development comparisons can measure overhead across test/review versions and profiles.
- Cache reporting shows raw read/write tokens and, where normalized usage coverage supports it, Cached Input as cache-read tokens divided by inclusive input tokens. Label it as a token fraction, not a request hit rate; zero input has no meaningful ratio. Preserve incomplete legacy coverage indicators. The pinned PydanticAI normalization evidence is in `2026-09-20-quality-cache-accounting-evidence.md`; cached tokens must not be added to input tokens again.

## Existing versus missing: inspected baseline

| Surface | Existing source | Required work |
|---|---|---|
| Cases, suites, candidate/profile matrix | `api/src/routers/agent_evaluations.py`, contracts/ORM `agent_evaluations.py`, services `agent_evaluations/`, platform job `agent_evaluation.py` | Default test collection and last-result read contract; preserve existing named suites/version snapshots; no silent flattening |
| Assertions and evidence | `services/agent_evaluations/assertions.py` (`evaluate_assertions`, async judge path), `evidence.py` (`load_persisted_evaluation_evidence`) | Completeness/applicability semantics plus authorized persisted historical evaluation; existing readers alone are not that product |
| Generated scenarios | `test_designer.py`, `simulator.py`, `simulator_models.py` | Plain-English authoring and proposed-tool identity/schema freeze; audit before extending |
| Findings | `routers/agent_findings.py`, contracts/ORM `agent_findings.py` | Currently single source run, description/expectation and linked case IDs; review source, multi-run evidence, opportunity kind and Markdown evidence need reviewed additive contracts |
| Reviews | No general persisted review-statement workflow identified in focused inventory | Audit existing review/flag surfaces and scheduling before introducing definition/trigger contracts |
| CLI | `api/bifrost/commands/agent_tests.py` has suites-list/get/create/publish, cases-list/create/export, candidates-create/get, designer-drafts/accept, run/status/results/compare/cancel | Review/findings, recorded evaluation, profiles matrix, definition editing/import and coherent follow/exit behavior need parity audit |
| UI | `AgentTuneWorkbench`, `evaluation/*`, `MarkdownContent`, `RunDurableEvidence`, run pages and FilesExplorer | Replace fragmented navigation; reuse renderer/components; retain existing activity inspection |

Inventory is focused, not an exhaustive claim. Phase 1 verifies exact symbols, schemas, CLI/MCP consumers and migration compatibility before issuing backend writes.

## CLI development contract

Keep `agent-tests` existing commands compatible. New vocabulary below is a proposal to reconcile against actual Click registration, not commands claimed to work today. Reuse one API and versioned definitions in UI/CLI; new MCP mutations must be thin HTTP wrappers.

Illustrative developer journey:

```text
bifrost agent-reviews create --agent AGENT --file review.yaml
bifrost agent-reviews run REVIEW --runs RUN1,RUN2 --wait
bifrost agent-findings list --agent AGENT --status open --json
bifrost agent-findings get FINDING --json
bifrost agent-tests generate --agent AGENT --finding FINDING --output draft.yaml
bifrost agent-tests accept --file draft.yaml
bifrost agent-tests evaluate --agent AGENT --tests all --runs RUN1,RUN2 --wait --json
bifrost agent-tests draft-create --agent AGENT --file changes.yaml
bifrost agent-tests simulate --agent AGENT --tests all --draft DRAFT --profiles FAST,REASONING --wait --json
bifrost agent-tests results EXECUTION --json
bifrost agent-tests draft-diff DRAFT
bifrost agent-tests draft-apply DRAFT --reason "Fix approval behavior"
```

- Freeze selected IDs/versions at admission. Local files are versionable test/review/simulator definitions, not credential stores; resolve environment-dependent IDs explicitly. Preserve existing exports; do not revive removed general platform import/export commands.
- Shared PlatformJob ID returned immediately by default; wait/follow uses short shared status requests and supports reconnection, cancellation and deduplication. Terminal results stable for scripts; stdout JSON contains no progress chatter. Do not fake existing `--wait`/`--json` support before auditing it.
- Define documented stable exit semantics before implementation: success only when required checks pass; distinct assertion failure, invocation/job error, and incomplete required evidence. Not Applicable is reported separately and all-inapplicable selections do not produce a misleading green gate.
- Profiles, repetitions, scope and thresholds are saved definition settings reusable by schedules and CI. Scheduling calls the same execution service; no automation-only assertion engine. Review/testing/judge profiles have explicit roles and cost reporting.

## Phases and handoff gates

### Phase 0 — Revised design prototype (ready for bounded handoff)

Own only `client/public/mockups/agent-test-explorer/` and `docs/superpowers/mockups/agent-test-explorer/`. Implement the detailed companion assignment; no production or API code. Primary reviews desktop/mobile and checks the complete investigation→evaluate→draft→simulate→apply journey. This establishes interaction direction; it does not prove backend feasibility.

- [x] Add Reviews, Markdown evidence/link examples, Investigate and existing-test evaluation.
- [x] Rework test creation to Situation/Expected Behavior with generated setup behind disclosure.
- [x] Distinguish recorded evaluation and simulation; include missing-evidence and not-applicable examples.
- [x] Draft Changes and simulation-only tool proposal with apply block.
- [x] Primary visual and intent review, browser checks, recorded disposition.

### Phase 1 — Contract inventory and minimal schema decisions

Own documentation first. Inspect exact routes/DTOs/CLI/MCP/manifest exports and shared scheduling patterns. Produce endpoint/DTO diff, migration design and compatibility table, with explicit immutable identities for test versions, recorded evaluations, scenarios, proposed tools, review versions and evidence references. Map shared Pydantic export policy before adding models; do not proliferate parallel definitions.

- [ ] Inventory all existing commands and API consumers; resolve proposed command spellings.
- [ ] Decide default agent test collection over existing suite storage and preservation of named suites.
- [ ] Define evidence completeness and applicability contract per assertion kind, outcome aggregation and CI policy.
- [ ] Define review scheduling/run selection, persisted provenance, tenant access and snapshot semantics.
- [ ] Define proposed tool namespace and production-apply validation.
- [ ] Primary reviews contract changes; only then issue file-owned backend packets.

### Phase 2 — Recorded-run evaluation with CLI (first backend capability)

Usage accounting is an acceptance requirement, not a later dashboard enhancement. Reuse shared AIUsage records for billable work, with explicit purpose attribution for agent execution, simulation, test judging, review, and scenario/draft generation. Expose per-operation/run and global tenant-authorized breakdowns, filterable by provider/model/profile and time. Show input/output/cache tokens, duration, observed provider cost versus estimated cost, and unknown/partial accounting coverage. Historical evidence reads never duplicate the original run's spend; synthetic execution contributes once. Tests must verify aggregation conservation, operation attribution, idempotency, partial/failed calls, and cross-tenant isolation. UI and CLI must expose the same breakdowns. Review and simulation phases inherit this requirement.

Extend assertion/evidence boundary with explicit completeness and applicability. Add authorized admission/results using shared jobs and frozen test/run/evidence/judge snapshots. Reuse semantic judge settings. Add CLI evaluation/results/wait/cancel and contract tripwires in the same slice; no synthetic invocation.

Acceptance: incomplete trace cannot pass forbidden-call check; cross-tenant run IDs denied; duplicate evidence runs evaluated once; missing/deleted evidence reported; exact and semantic outcomes differ correctly; terminal/cancelled jobs survive restart; existing simulation results unchanged. Live CLI happy path evaluates selected existing runs and returns documented exit status. No real tool/agent call occurs.

### Phase 3 — Reviews and findings with CLI and schedules

Add versioned review statements, scope, formatting instructions, evidence Markdown, review provenance and multiple run references; keep finding dismissal independent. Reuse shared scheduling/job policies for on-demand and scheduled review/testing. Expose create/edit/list/search/run/results operations through CLI and scoped APIs. Define selection windows, overlap dedupe, disabled schedules and permission changes.

Acceptance: a successful run can produce an opportunity; duplicate triggers do not duplicate admitted jobs; source access enforced on reads; canonical URL formatting preserved without fetch; late/pending runs handled explicitly; tests never auto-dismiss; review and test automation use same services as on-demand.

### Phase 4 — Tests, generated scenarios and proposed tools with CLI

Default test collection, latest results per mode/profile/version and versioned portable definitions. Expand designer only after reviewing existing schema restrictions. Generate drafts from situation/expectations/authorized history; freeze accepted scenarios. Add simulation-only tool definitions, candidate binding, matrix comparisons and production apply guard.

Acceptance: coherent create/read/update simulation, deterministic replay, varying failures captured as versioned scenarios; hypothetical tool cannot dispatch real workflow; unresolved tool blocks apply; renamed/colliding tools rejected; profile changes cannot be confused with instruction improvements; edited expectations cannot silently validate old output.

### Phase 5 — Production workbench integration

Primary supplies final interaction spec from Phase 0. Delegate narrow component implementations with exact file ownership. Reuse FilesExplorer patterns and MarkdownContent; services use generated API types. Add global Agents Tests/Findings scope, per-agent component, run-detail/list evaluation actions, and linked failure context. Retire only obsolete tuning views, preserving history and existing run APIs.

Acceptance: all Phase 0 journeys work against real authorized contracts, keyboard/mobile flows and clear partial/error states; no extra suite setup; no bespoke polling; no duplicate debugger; actual CLI-created definitions appear unchanged in UI and vice versa.

### Phase 6 — Integrated acceptance and competitive review

- [ ] Focused backend and UI tests per slice, DTO/CLI/MCP/manifest/version tripwires as relevant, generated types, Python lint/type checks, client lint/types.
- [ ] Run requested full backend and repository UI gates once integrated, after inspecting harness instructions; reproduce and fix discovered failures, never rerun-until-green or waive as flaky.
- [ ] Demonstrate CLI-first review→finding→existing evaluation/new test→draft simulation across profiles→explicit apply.
- [ ] Verify automatic agent resume/outbox/lease/parent-child invariants from original backend objective remain covered; baseline reports do not excuse regressions.
- [ ] Inspect rendered desktop/mobile UI personally; map each criterion to evidence and disclose remaining gaps.
- [ ] Verify cost/cache metrics from actual usage records; no OpenChamber product dependency. Compare final capabilities with current competitor primary documentation, including Copilot Studio; record differences without claiming synthetic tests prove integrations.

## Verification instructions

Use `.claude/skills/bifrost-testing/SKILL.md`. Docker backend via `./test.sh`; no host pytest. Choose focused existing suites including `tests/unit/services/agent_evaluations/`, `tests/e2e/api/test_agent_evaluation_*.py`, `test_agent_findings.py`; add specific files for new contracts. Required contract checks when affected: `tests/unit/test_dto_flags.py`, `tests/unit/test_contract_version.py`, MCP thin-wrapper and portable round-trip tests. Run `./test.sh quality api`, client `npm run tsc`/`npm run lint`, focused Vitest and browser flows. Regenerate types against this worktree's debug stack after DTO changes. Reuse the stack, never reset/restart it for routine source changes.

Each packet lists exact paths and exact commands after inventory. Record command, result, skipped broader suites, reviewed diff and any outstanding issue in its report. No slice is accepted solely on contributor claims.
