# Scope change: Solutions versioning + upgrade-in-place

Date: 2026-06-09 · From: Jack, via the second-opinion review session ("Solutions Second Opinion")
Decision: **"Upgrades is the right call here, even if it's just a replace in practice."**

Fold the following into the active Solutions plan as a first-class work item (not a follow-up):

1. **`version` field** in `bifrost.solution.yaml`, carried onto the Solution install record and
   shown in the management UI (list + detail).
2. **Upgrade is an explicit verb, replace is the semantics.** Deploying/installing a bundle whose
   slug+scope matches an existing install is an UPGRADE of that install: run the existing
   full-replace reconcile (table rows + config values are already preserved; orphan/reattach
   already handles entity remove→re-add), and record `old_version → new_version` on the install.
3. **Drag-drop routes to upgrade.** Uploading a newer zip of an already-installed solution routes
   to the upgrade path with a preview diff (entities added/removed, config declarations
   added/removed/changed) — it must NOT create a second install or silently replace without preview.
4. **Refuse downgrades by default** (older `version` than installed), with an explicit
   `--force`/confirm override.
5. **No migration framework in v1.** Replace semantics are acceptable; the point is that version is
   recorded, upgrade is a safe first-class verb, and the preview tells the operator what changes.

Rationale (from the review): upgrade is the marketplace/distribution primitive — without it a
disconnected install's only path is uninstall→reinstall. Nearly all machinery already exists
(full-replace reconcile, orphan/reattach, install preview); this is mostly wiring + UI + version
bookkeeping. Full context: `docs/plans/2026-06-09-platform-50ft-action-plan.md` (WS-2).
