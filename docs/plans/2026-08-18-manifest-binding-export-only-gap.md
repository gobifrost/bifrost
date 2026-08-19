# Finding: catalog `manifest` bindings exist for export-only entities

**Found:** 2026-08-18, during Phase 2 Claims slice review
**Status:** logged, deliberately NOT fixed in Phase 2
**Severity:** low today, but it makes the catalog overstate a surface

## What

The operation catalog's `manifest=ManifestOperationBinding(...)` field reads as
"this operation participates in the manifest surface." For Custom Claims (and
for Roles, the precedent Claims followed) that is only half true:

- **Export side exists.** `manifest_generator.py` has
  `serialize_custom_claim()` / `ManifestCustomClaim`, and claims are written
  into `.bifrost/*.yaml`.
- **Import side does NOT exist.** `github_sync.py` contains no
  `_resolve_claim*` — and no `_resolve_role*` either. Verified by grep
  2026-08-18.

So both entities are **export-only** in git sync. A manifest carrying claims
round-trips out of the DB but is never read back in.

## Why it was not "fixed" in the slice

Binding `manifest` on `claims.create` / `claims.update` is consistent with
every sibling entity and with the actual serializer, so the Claims slice
(commit `7d1d63195`) kept it. Changing the binding to an exclusion would have
made Claims inconsistent with Roles without fixing the underlying gap, and
writing an importer is well outside MCP/CLI parity work.

## The real question, for a later phase

Decide which is true and make the catalog say it:

1. Claims and Roles SHOULD be importable — then `github_sync.py` needs
   `_resolve_claim` / `_resolve_role`, following the non-destructive
   upsert-by-natural-key pattern documented in CLAUDE.md (important here:
   claims are referenced by table policies, so a delete-and-reinsert would
   cascade).
2. They are intentionally export-only — then the catalog's `manifest` binding
   should distinguish "serialized into the manifest" from "reconciled from the
   manifest," because today one field means both.

Option 2 is a catalog-modeling change and would touch every entity's entry, so
it belongs with the Phase 3+ manifest work rather than a parity slice.

## Related

- `action_scopes` has the same shape-vs-membership seam: the catalog validator
  enforces the lowercase `resource.verb` FORM (see
  `test_catalog_requires_valid_graph_inspired_action_scopes`), but nothing
  checks the scope against `shared/authorization_scopes.py`'s
  `AUTHORIZATION_SCOPE_CATALOG`, which only `routers/roles.py` consumes. A
  well-formed but nonexistent scope passes today. Phase 5 (the authorization
  intersection) is the right place for that.
