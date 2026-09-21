# Review Findings Visibility — Approved Bounded Handoff

Owner: primary; executor: native worker (GPT-5.5). Worktree: durable-agent-platform-backend; preserve all existing edits. This authorizes only the visibility portion of Phase 3C, not the rest of its proposal.

Read `2026-09-20-agent-review-phase3c-api-cli-proposal.md` for the approved source/provenance predicate and `2026-09-20-unified-agent-quality-workbench.md` for intent. Review-derived text must not outlive access to any contributor. Counts and paging must filter in SQL before aggregation; no Python overfetch.

## Ownership

- New `api/shared/agent_finding_visibility.py`: shared SQL visibility/query helpers accepting UserPrincipal, no FastAPI imports.
- Existing `api/src/routers/agent_findings.py`: wire the predicate into existing list/get/update before reading or mutating review-derived findings. Preserve bare-list response, fields, paths, manual creation behavior, and linked-case behavior.
- New `api/tests/e2e/api/test_agent_finding_visibility.py` and focused changes to existing finding tests only when necessary.
- This document for execution notes.

Do not edit shared/models.py, agent_reviews.py, ORM/migrations, job registry, CLI, generated files or UI. No new public search route/DTO in this slice. The accepted shared predicate will power that next slice. No commits/push. Evidence worker owns test stack until primary transfers it.

## Acceptance

1. Preserve existing agent visibility (private owner, role grant, authenticated vs external, shared/global and exact tenant). Null-org nonadmin fails closed. Inspect real role association schema, do not guess joins.
2. Manual/legacy findings require all review provenance columns absent. Partial provenance must never downgrade to manual visibility.
3. Review-derived rows require matching review/version/run/agent/org provenance, nonempty source refs, source ordinal and consistent version identity. Validate ALL run contributors, including uncited ones; finding refs must be a nonempty subset.
4. Reuse canonical AgentRun visibility; require exact frozen run/agent/org/root/parent/trigger identity, including explicit nullable keys. Selected root agent equals review agent; historical descendant agent may be explicit null. Every contributor org equals review org, including null.
5. Guard JSON array expansion AND length via CASE, never trust SQL conjunct order. Invalid/missing fields or UUID text hide only the malformed row, not error the page. No unchecked UUID casts.
6. Same SQL predicate applies before existing list selection, get, and update. Unauthorized update must not change content/status. Existing manual sources retain prior API behavior.
7. Tests prove valid visibility, another tenant/private agent/uncited revoked run denial, domain deletion/provenance corruption, malformed JSON (object/scalar/invalid IDs/missing-null keys), nullable descendant identity, and filtering count/page before pagination. Keep fixtures scoped and explicitly cleaned.

## Verification

When stack ownership is granted: `./test.sh tests/e2e/api/test_agent_finding_visibility.py tests/e2e/api/test_agent_findings.py -v`, then applicable API lint/type check. Report exact results and unresolved issues. Primary independently reviews and runs focused checks. Full suites remain later integrated acceptance.

## Execution notes

- Added `api/shared/agent_finding_visibility.py` with a bound SQL predicate for existing `AgentFinding` rows. The predicate keeps legacy/manual rows visible only when all review provenance columns are absent, and requires review-derived rows to match their review definition, version, and run identity before source checks run.
- Wired the predicate into the existing agent-scoped list, get, and update routes before returning or mutating rows. Existing create behavior, bare-list response shape, paths, and case linkage behavior are unchanged.
- The predicate validates every `AgentReviewRun.source_refs` contributor, not just the finding-cited subset, using guarded `jsonb_array_elements` and guarded `jsonb_array_length` expressions. UUID values are compared as text after regex shape checks; no JSON text is cast to UUID.
- Contributor `AgentRun` readability is enforced by a SQLAlchemy correlated `NOT EXISTS` overlay that calls `agent_run_visibility_conditions(user)` directly; the raw SQL portion only validates frozen JSON shape and identity.
- `AgentFinding.source_run_refs` must be a nonempty array and a subset of the frozen review-run contributors. Invalid shape, invalid UUID text, missing nullable identity keys, deleted contributors, source domain deletion, or source identity mismatch hides only that row.
- Existing HTTP routes have no count/page contract in this bounded slice, so no public count/page route was invented. The focused e2e tests exercise the shared predicate directly in `SELECT count(*)` and paged `SELECT` queries to prove filtering happens before aggregation/page slicing for the later global search route.
