# Code Builder and shared Pydantic runtime integration

**Status:** recovered foundation implemented; parity follow-on pending
**Base:** `origin/main@16e317e62`
**Builder source of truth:** `codex/code-builder-recovery-20260807@dc4a844d1`
**Original immutable backup:** `1696d8693`

## Decisions

- Reconstruct Builder on a fresh main-based branch. Do not rebase or mutate the
  recovery and backup branches.
- `PlatformJob` is the durable control plane. It owns status, progress,
  cancellation, retries, deduplication, and visibility; it is not the compute
  host.
- Local Builder turns run on existing Bifrost workers. Cloudflare is an
  optional execution backend for the same workspace-agent envelope.
- Chat, autonomous Agents, local Builder, and Cloudflare Builder use one shared
  Pydantic AI runtime runner. Builder contributes a workspace tool profile; it
  does not own a second model loop.
- Canonical application compilation runs on existing workers. The scheduler
  coordinates the durable job and trusted deployment finalization but does not
  run npm/Vite compute.
- Solution source revisions/checkpoints and conversation artifact workspaces are
  separate. Uploaded and generated user files use opaque `ArtifactRef` values;
  source archives, internal checkpoints, and deployed `dist/` files do not.
- The withdrawn Builder migration revisions remain tombstones. Builder returns
  only through forward migrations after `20260816_artifact_workspace`.

## Feature-parity matrix

Evidence labels follow the implementation delivery-gate contract. The detailed
command/test ledger and live proof are maintained in the Builder status spec.

| ID | Source surface | Requirement / journey | Disposition | Implementation evidence | Data / files / policy | Automated evidence | Browser evidence | Notes / blocker |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B01 | Builder catalog | My work plus explicit support-wide filtered catalog | integrated | recovered catalog and centralized access service | Solution ownership, collaborators, cross-tenant support gate | component/service coverage green | Playwright green | All-customer view remains deliberate and filtered |
| B02 | Builder creation | Prompt-led private Solution creation and animated workbench handoff | integrated | private scaffold plus staged transition | private Solution and initial revision | component/service coverage green | Playwright green | AI/setup gate enforced |
| B03 | Builder session | Durable conversation, revision, checkpoint, cancellation, resume | integrated | shared conversation plus fenced checkpoint resume | Conversation plus immutable source/checkpoint storage | unit and live resume green | Playwright/live proof green | OpenCode archives retired |
| B04 | Native runtime | One Pydantic loop shared with Chat and autonomous Agents | integrated | `AgentRuntimeRunner` and shared `AgentExecutor` host | provider usage and durable messages | runtime tests green | live proof green | No second Builder loop |
| B05 | Local execution | Run Builder turns on existing worker replicas | integrated | `solution.builder.turn` Worker consumer | PlatformJob is authoritative | PlatformJob/consumer tests green | live proof green | Scheduler does not run model compute |
| B06 | Cloudflare execution | Run the same Builder envelope in an ephemeral sandbox | integrated | provider-neutral runner image and Workflow adapter | job-bound capability only | adapter/provisioning tests green | setup UI verified | No AI/provider secrets in sandbox |
| B07 | App compilation | Canonical npm/Vite build on existing workers | integrated | `solution.build` Worker consumer and `test_solution_build` | staged source and immutable output manifest | real-Worker E2E green | live repair/deploy green | Deterministic artifact reuse proven |
| B08 | Preview/runtime | Same-origin isolated preview and trusted/isolated promotion | integrated | API-mounted Builder runtime | app/Solution/org/viewer-bound runtime token | runtime/token E2E green | Playwright green | Existing apps remain trusted |
| B09 | Attachments/artifacts | Chat V3 upload, preview, generated artifacts, and durable reuse | integrated | direct reuse of shared Chat V3 contracts | canonical `ArtifactRef`, conversation workspace | shared Chat tests green | shared Chat UI | No raw object-store paths |
| B10 | Tool progress | Collapsed activity history with live status and elapsed time | integrated | shared Chat events plus PlatformJob phase | durable tool messages plus PlatformJob phase | runtime/progress tests green | Playwright/live proof green | Provider wait says “AI is working” |
| B11 | SDK/MCP | BYO harness uses the same workspace operations and ArtifactRefs | partial | session-scoped MCP gateway and Builder workspace tools exist | REST authorization and project access | current gateway tests green | external-harness UI verified | Full REST/CLI/MCP operation catalog and legacy direct-ORM reconciliation are tracked in `2026-08-17-builder-capability-parity-execution.md` |
| B12 | Skills | `SKILL.md` instructions, bundle upload/browser/export, visible use | partial | Agent Skill storage, manifest, native runtime, and UI exist | Solution-relative bundle or Agent-owned upload | current backend/client tests green | UI verified | Dynamic MCP still needs revision-bound file hydration and canonical public tool naming |
| B13 | Collaboration | Owner/view/edit/support access with actor attribution | integrated | centralized project/conversation access | owner/collaborator/support identity | access/E2E tests green | catalog/share UI verified | Impersonation does not erase attribution |
| B14 | Promotion | Pin, review, publish a separate release, preserve private source | integrated | immutable release/promotion service | target-org role/user validation | service/client tests green | promotion UI verified | Exact green revision required |
| B15 | Global workspace | Admin proposal, validate, fenced apply, rollback | integrated | admin-only workspace proposal service | global `_repo` digest and write lock | service/component tests green | admin UI verified | Ordinary users denied |
| B16 | Admin readiness | AI and Cloudflare/local setup, live connectivity, enable gate | integrated | Settings and Diagnostics Builder surfaces | encrypted credentials and provisioning job | provisioning/client tests green | setup UI verified | Local needs no external endpoint |
| B17 | Usage | Provider tokens, cache tokens, media, cost, user/org/Solution attribution | integrated | shared AI usage ledger | authoritative server ledger | runtime/accounting tests green | usage projection present | Hierarchical quota enforcement remains post-v1 |
| B18 | Migrations | Restore Builder after tombstones with one Alembic head | integrated | new forward-only revisions after tombstones | fresh and previously-withdrawn DB paths | migration tests green | N/A | Old revision bodies remain tombstones |

## Explicitly retired implementation

- OpenCode Node loop, transcript compaction, and harness-state archives.
- Builder-specific OpenAI-compatible streaming and token accounting.
- External Cloudflare/local application-build mode.
- Permanent Builder coordinator, runner, or app-host services.
- Duplicate Builder attachment, artifact, progress, and generated-media paths.

## Delivery state

- Recovered-foundation gate: **established** — the reconstructed Builder and
  shared Pydantic runtime have production implementation and explicit ownership.
- Full portability gate: **not established** — B11 and B12 require the canonical
  operation, parity, naming, and Skill-hydration work in
  `2026-08-17-builder-capability-parity-execution.md`.
- Existing-foundation integration gate: **established** — focused backend,
  client, real Worker, migration, and live resumed-turn evidence is green.
- Delivery-QA gate: **established for the default local Worker path** — the
  focused Playwright journey, visual inspection, loading/error/recovery states,
  and live repair/deploy flow are green. Live Cloudflare acceptance follows CI
  publication of the first candidate image.
- Customer acceptance: **not accepted**.
