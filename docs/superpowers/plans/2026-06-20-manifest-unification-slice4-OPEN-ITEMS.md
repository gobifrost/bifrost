# Slice 4 (#390) — Open Items After Merge

> Companion to `2026-06-20-manifest-unification-slice4.md`. Slice 4 itself is
> **complete**: every `Manifest*` model is the single source of truth for its
> serialization (`from_row` / `view(dest)` / `to_orm_values(dest)`), output is
> byte-identical to the four legacy writers, full suite 6378/0, detector 25/25,
> contract tripwire green. **None of the items below are unfinished Slice 4
> work** — they are a pre-existing bug the refactor surfaced (B1) plus optional
> polish that was explicitly deferred (P-series). "Model-unification" is DONE;
> the Pass-2 line in the original plan meant the *base+superset model
> consolidation*, a separate opt-in follow-on, NOT core unification.

Branch at time of writing: `390-manifest-unify-spike` @ `dbb5132c`.

---

## B1 — Topic EventSources lose their topic key (REAL bug, pre-existing on main)

**Severity:** Medium. **Introduced by this branch?** No — exists on `main`
(`29543363`) today; Slice 4 reproduced it byte-identically.

**What's wrong:** `EventSource` has three kinds — `webhook` (id'd by
`adapter_name`/`webhook_config`), `schedule` (id'd by `cron_expression`), and
`topic` (id'd by `event_type`, e.g. `"ticket.created"`). The `event_type` field
is the topic's routing key, looked up by `EventSourceRepository.get_by_topic()`
(`api/src/repositories/events.py:172`). **`ManifestEventSource` has no parent
`event_type` field** and never did — neither the old `serialize_event_source`
nor the old `_upsert_events` (its `mevent.get("event_type")` always returned
None because capture/serialize never emitted it). So:

- A **topic** EventSource exported to `.bifrost/events.yaml` or a Solution bundle
  **loses its topic key**.
- On import/deploy it is written with `event_type = NULL`.
- `get_by_topic()` can never find it → the trigger silently never fires.

Webhook and schedule sources round-trip correctly; only `topic` is affected.

**Where:**
- Model: `api/bifrost/manifest.py` `ManifestEventSource` (add `event_type` field).
- Export (git-sync): `api/src/services/manifest_generator.py::serialize_event_source`
  (set `event_type=es.event_type`).
- Export (install): `api/src/services/solutions/capture.py::_event_entries`
  delegates to `serialize_event_source().model_dump()` — fixed automatically once
  the model + serializer carry it (verify).
- Import (git-sync): `api/src/services/manifest_import.py::_resolve_event_source`
  (write `event_type` onto the EventSource row).
- Import (install): `api/src/services/solutions/deploy.py::_upsert_events`
  (its `_direct` comes from `to_orm_values(INSTALL).direct`; once `event_type` is
  a model field + in the install allowlist it flows through — verify, and confirm
  the old line `"event_type": mevent.get("event_type")` is fully replaced).

**Why it's NOT in Slice 4:** Slice 4's contract is byte-identical, no behavior
change. Adding `event_type` round-trip is a deliberate behavior change (topic
sources start carrying a field they didn't), so it must be a separate commit
with its own test and (because it changes manifest output) a deliberate golden
re-capture.

**Acceptance:**
- New e2e: seed a `topic` EventSource with `event_type="x.y"`, round-trip it
  through BOTH the `_repo` git-sync path and a Solution install, assert
  `get_by_topic("x.y")` finds the re-imported source on each.
- `ManifestEventSource.event_type` classified (likely `FieldClass.CONTENT`).
- Re-capture the `event_*` goldens (the install/git_sync views now include
  `event_type`); confirm the round-trip detector + full suite stay green.
- Confirm `EventSubscription.event_type` (a *different*, already-working field on
  subscriptions) is untouched.

**Decision needed from Jack:** fix B1 ON this branch before merge (one extra
commit, intentionally breaks byte-identity for topic sources only), OR merge
Slice 4 as-is and fix B1 in a fast follow-up PR. Either is defensible; the user
stated a preference against landing on main with known issues, which argues for
fixing on-branch.

---

## B2 — `Workflow.tool_description` not captured for Solution install (pre-existing)

**Severity:** Medium. **Introduced by this branch?** No — `main`'s legacy
`SolutionCapture._workflow_entries` hand-list never included `tool_description`,
so a workflow's MCP tool-description has never travelled in a Solution bundle.
Slice 4 preserved this byte-identically (`_WORKFLOW_INSTALL_ALLOWLIST` omits it,
matching the legacy omission), so `view(INSTALL)` drops it and a redeploy writes
`tool_description=NULL`.

**Fix (follow-up):** add `tool_description` to `_WORKFLOW_INSTALL_ALLOWLIST` in
`api/bifrost/manifest.py` (it is already a model field and already written by
`ManifestWorkflow.to_orm_values(INSTALL)`), then re-capture the
`workflow_install` golden. Deliberate output change (like B1), so its own commit
+ a capture→deploy round-trip test asserting the description survives. NOTE the
git-sync path already round-trips it (resolver-conditional emit), so this is
install-only.

**Found by:** the adversarial Codex pass on PR #392.

## B3 — git-sync cannot clear role bindings once roles go empty (pre-existing)

**Severity:** Medium. **Introduced by this branch?** No — identical
`if hasattr(m, "roles") and m.roles:` gating exists on `main` for
workflow/app/form/agent resolvers (`manifest_import.py`). When a manifest entry's
`roles` list becomes empty, the git-sync importer emits no `SyncRoles` op, so the
previously-bound roles are left in place — whereas install deploy always
full-syncs roles (empty list clears them). The two import paths diverge on the
"all roles removed" case.

**Fix (follow-up):** emit a `SyncRoles` op for an empty `roles` list in the
git-sync resolvers (so an emptied manifest entry clears bindings, matching
install), guarded so it only fires when the entry actually carries a `roles` key.
Needs a git-sync round-trip test: bind a role, then re-import with `roles: []`,
assert the binding is gone.

**Found by:** the adversarial Codex pass on PR #392.

---

## P-series — Deferred polish (all "ship-as-is" per the per-task + final reviews)

None affect correctness; all were reviewed and triaged as non-blocking. Batch
them into one small cleanup PR (or fold into Pass 2).

- **P1 — `serialize_*` delegator docstrings.** `serialize_organization`,
  `serialize_integration`, `serialize_config` (`api/src/services/manifest_generator.py`)
  dropped their docstrings when reduced to one-line `from_row` delegators; sibling
  serializers kept theirs. Restore for uniformity. Cosmetic.

- **P2 — `_install_view` allocates its allowlist set literal per call.**
  `ManifestWorkflow`/`Form`/`Agent`/`App._install_view` build the `_ALLOWLIST`
  set inline each call. Hoist to a module-level `frozenset`. Micro-perf only.

- **P3 — import-at-call-site.** A few `from bifrost.manifest import ...` /
  `from bifrost.manifest_codec import Destination` live inside loops/if-blocks in
  `capture.py`/`deploy.py` (e.g. App). Hoist to top-of-function for consistency
  with the rest of the batch. Style.

- **P4 — child models' `to_orm_values` `NotImplementedError` not unit-tested.**
  Integration's `config_schema`/`oauth`/`mapping`, MCPServer's `connection`/`tool`,
  and `ManifestEventSubscription` raise `NotImplementedError` unconditionally
  (they're reconciled by the parent resolver, never standalone). The parent case
  is tested; the children aren't directly. Add trivial `pytest.raises` coverage
  for completeness. Recurring note from Tasks 9/11.

- **P5 — `entity_change_hook.py` bypasses `view()`.** It calls
  `serialize_X(...).model_dump(mode="json", exclude_defaults=True, by_alias=True)`
  rather than `.view(Destination.GIT_SYNC)`. Intentional today (it wants
  `exclude_defaults`, which `view` doesn't apply), but it's another wire surface
  not routed through the unified seam. Add a one-line comment explaining the
  deliberate difference, or (Pass 2) add a `view` variant that takes
  `exclude_defaults`.

- **P6 — `ManifestAgent.path` lacks an explicit `import_owner`.** Defaults to
  `"direct"`; harmless because `to_orm_values` is hardcoded and never emits
  `path` to import. Add `import_owner="direct"` (or a comment) for annotation
  uniformity. Inert.

---

## Pass 2 (separate, opt-in — NOT a known issue, NOT required to merge Slice 4)

Greenlit later, explicitly out of Slice 4 scope. Reference: original plan +
`docs/superpowers/specs/2026-06-20-base-superset-viability-verdict.json`.

- **Shape cleanups** — now that one model owns each entity's serialization, the
  per-path divergences in `api/tests/roundtrip/paths.py` (`FIELD_OVERRIDES` /
  `EXTRA_FIELD_POLICY`) can be reduced where the divergence is incidental rather
  than required. Each cleanup deliberately breaks byte-identity for that field →
  golden re-capture + detector verification per change.
- **Format-version latch** — once shapes are cleaned, stamp a manifest
  format-version so future readers can detect/upgrade old layouts.
- **Base+superset model consolidation** (the original plan's "model-unification"
  — DISTINCT from the core unification this slice delivered) — Agent/Form only,
  GO-ONLY-IF per the viability verdict. Collapses the git_sync (whole-model) and
  install (curated-subset) shapes into one base + superset where it reduces real
  duplication. Opt-in; do not start without re-confirming value.
