# Findings Search and Evidence — Approved Bounded Handoff

Primary owns acceptance; native worker implements in durable-agent-platform-backend, preserving all existing work. Start only after primary transfers shared/models.py, shared/agent_review_admission.py and command/generated-file ownership from the review API worker. No overlapping writers. Follow the findings portions of `2026-09-20-agent-review-phase3c-api-cli-proposal.md` and accepted shared visibility predicate.

## Result

Search findings across visible agents, read formatted Markdown evidence and review provenance, dismiss independently, and use the same objects from the CLI. Existing agent-scoped bare-list API shape is preserved. No UI, scheduling, model calls, new status system, automatic test creation or agent mutation in this packet.

## Ownership

- Move existing finding DTO definitions unchanged into shared/models.py, then add approved fields. Existing src/models/contracts/agent_findings.py becomes the explicitly approved exact-name re-export; remove reverse import from shared/models.py to avoid a cycle. Place FindingPublic before ReviewRunResults.
- New shared/agent_finding_queries.py for search/count/page and common authorized finding serialization. Existing router and review-results service use the same serializer, so Markdown/provenance/link fields cannot drift between surfaces.
- Existing routers/agent_findings.py: additive create/update fields, existing list filters, new literal /search registered before /{finding_id}. Preserve existing routes and bare-list response. Shared SQL visibility applies before count/page/get/update; do not substitute Python paging.
- shared/agent_review_admission.py: replace its duplicate finding construction with shared serializer only. Do not redesign admission/executor.
- New bifrost/commands/agent_findings.py and command registration. Existing command groups unchanged.
- Focused API/CLI tests, contract/DTO metadata and generated appendices/v1 types as required. No changes to accounting, ORM/migrations, executor or scheduling.

## Behavior

1. Manual create/update may set finding_kind (problem/opportunity) and evidence_markdown (max 20000 characters). Explicit null clears evidence on update; omission preserves it. Review provenance fields are read-only and rejected in requests, never silently accepted or cleared by dismissal.
2. Public findings expose source_review_id, source_review_version_id, source_review_run_id, source_review_version, source_run_refs, source_ordinal plus kind/Markdown. Preserve all previous fields/config/defaults/validation.
3. /search returns items,total,limit,offset; agent_id optional, filters status/kind/source kind/review/review run/query and optional admin organization UUID. Nonadmin cannot expand tenant scope. Default limit50, max200, nonnegative offset; stable order created_at descending then id. Existing bare-list default order/shape remain compatible. Query is literal case-insensitive text search (escape wildcard characters), with a reasonable bounded length.
4. Count and page run in ReadSnapshotDbSession with the SAME accepted SQL visibility predicate. Hidden/malformed derived findings are omitted without leaking their count or failing the whole page. Source authorization covers every contributor, not only citations. Shared serializer does not bypass the predicate or fetch external links.
5. CLI top-level agent-findings: create/search/list/get/update (including dismiss through update --status dismissed). --agent accepts existing ref resolver; long evidence/description can come from JSON/YAML --file or dedicated text file. Preserve source-run/sequence/external-ref validation; never expose provenance mutation flags. --json output remains scriptable; local invocation errors exit2. Read commands use existing global CLI conventions.
6. Review results must return the SAME finding Markdown/provenance and actual authorized linked_case_ids as direct finding reads. A test pass never changes finding status.

## Verification

Use focused existing/new finding API/visibility tests, review-results consumer regression, CLI tests, DTO/contract tripwires, generated freshness, integrated API quality. Cover one create Markdown opportunity → search globally → read → dismiss journey; explicit null versus omission; provenance spoof rejection; SQL authorization/count paging; same evidence from review results. Tests must not launch model jobs. Primary reviews and independently checks. Full backend/UI gates remain later integrated acceptance. No commit/push/merge.
