# Files Explorer Redesign — Design

**Date:** 2026-06-22
**Branch:** `codex/files-sdk-policies`
**Status:** Implemented — see plan `docs/superpowers/plans/2026-06-22-files-explorer-redesign.md`. Backend (seeded admin_bypass, `POST /api/files/structure`, 403-vs-404), frontend (FilesExplorer 3-pane responsive shell + ShareTree/FolderListing/FilePreview/EffectiveAccessPanel/TestAccessModal/PolicyEditorModal/NewShareDialog), and tests (backend e2e, vitest, Playwright desktop+narrow) all green. Live-drive caught two real bugs the unit tests missed: Global scope must send the explicit `"global"` sentinel (not `null`, which the write path reads as the caller's own org), and Test Access must list all users (not just the share's org).

## Problem

The admin Files page (`client/src/pages/Files.tsx` + `client/src/components/files/FileBrowser.tsx`) shipped in a state that does not match how the Files SDK works or how an admin expects to manage files:

- **No upload.** There is no way to put a file anywhere from the page.
- **Hardcoded locations.** A fixed `<select>` of `workspace`/`shared`/`uploads`/`temp`, but the SDK accepts *any* freeform location string.
- **Flat list, not a tree.** You type a `prefix` to list; there is no folder navigation, breadcrumbs, or tree.
- **Instant "Access denied" for platform admins.** Default state is `location="workspace"`, `scope=undefined` → resolves to the admin's own org, where no root policy exists → 403. The page cannot distinguish *policy denied* from *path does not exist*.
- **Bespoke org selector** instead of the platform's standard one.

### Root-cause backend defects (verified live)

1. **No platform-admin bypass in policy evaluation.** `FilePolicyService.is_allowed` returns `False` whenever `policy_row is None` — there is no admin bypass in the evaluator. `_principal_matches_org` bypasses only the *org-match* gate, not the allow/deny. So a platform admin with no matching policy is **denied**. This diverges from Tables, which seeds an editable `admin_bypass` policy (`shared/policies/probe.py::make_seed_admin_bypass`) on every new table so admins are allowed *by a visible, revocable policy*.
2. **403 vs 404 conflation.** The policy-gated read/list path does not cleanly distinguish "denied by policy" (403) from "path does not exist" (404), so the UI cannot tell the user which it is.

## Mental model

- **Locations = shares.** The page is a mapped-drives / network-share explorer. The root is a list of **shares** (freeform locations, e.g. `gallery`, `reports`). `workspace` and `temp` are hidden (platform-internal). `uploads` appears **read-only** (form-upload bucket; browse/preview only).
- **Scope = which org's view.** The platform's standard org-scope selector normalizes the whole tree by scope. The user is always "in" one scope: a specific org, or **Global**. There is **no "all scopes"** view (it caused the original confusion). Switching scope re-roots the tree. Storage is physically `{location}/{scope}/...`, so "show scope X's tree" is a literal prefix listing.
- **Two separate layers of access:**
  - **Structural visibility** (what exists): a platform admin sees prefixes/files that exist in the selected scope **regardless of policy**, so files are never orphaned/invisible. Admin-only structural listing.
  - **Content access** (read/write/delete/list-contents): policy-governed. A new share/prefix gets a **seeded `admin_bypass` policy** (`{when: {user: is_platform_admin}}`) — visible, editable, revocable, exactly like Tables. That seeded policy is the **only** admin bypass; there is **no hardcoded evaluator bypass**.
- Within a selected scope, the shares root also surfaces **shares that have a policy even if empty**, so a freshly-policied share does not vanish until a file lands. Folder/prefix level stays purely structural (real files).

## Layout & components

Full-height, space-maximizing 3-pane explorer. Replaces the monolithic `FileBrowser.tsx`.

- **Top bar:** org-scope selector (Global / specific org), "New share" action, breadcrumbs for the current path.
- **Left — `ShareTree`:** shares at the root; expand to walk folders (lazy-loaded per prefix). Right-click a share/folder → context menu: Effective Access, Test Access, New folder / Upload here, New policy here.
- **Center — `FolderListing`:** contents of the selected folder (folders + files). Owns **Upload** (button + drag-drop) targeting the current folder/scope. Row actions: preview, download, delete, manage policy, test access. Right-click parity with the tree.
- **Right — `FilePreview` + `EffectiveAccessPanel`:** file preview (text / image / download-only), and for the selected item an Effective Access summary.

New components: `FilesExplorer` (page shell: scope + breadcrumbs + layout), `ShareTree`, `FolderListing`, `FilePreview`, `EffectiveAccessPanel`, `TestAccessModal`, `PolicyEditorModal`, `NewShareDialog`. Remove `FileBrowser.tsx` and the inline tester/editor wiring in `Files.tsx` that the new components replace.

## Responsive behavior (required, not an afterthought)

The 3-pane layout must adapt down to small/narrow screens without any pane or control being clipped or losing functionality. Treat this as a first-class acceptance criterion.

- **Wide (≥ `lg`):** all three panes side by side (tree | listing | preview/ACL). Panes use a min-height-0 flex/grid so each scrolls internally and the page never overflows the viewport (follow the project's table-scroll pattern — take min height needed, cap at page, scroll internally).
- **Medium (`md`):** drop to two panes — tree + listing — with the preview/ACL pane becoming a collapsible right drawer (toggled when a file is selected or Effective Access is opened). The tree may collapse to a narrower rail.
- **Small (`< md`):** single-pane, navigation-stack model. The tree collapses behind a hamburger/sheet; selecting a folder shows the listing full-width; selecting a file opens preview/ACL as a full-screen sheet/drawer with a back affordance. Breadcrumbs remain the primary "where am I / go up" control. Upload, Test Access, and the denied helper all remain reachable (via toolbar overflow / sheet, never hidden off-screen).
- **Modals** (`TestAccessModal`, `PolicyEditorModal`, `NewShareDialog`) size to viewport on small screens (full-screen sheet rather than a fixed-width dialog that overflows); their content scrolls internally.
- No horizontal page scroll at any breakpoint; long paths/filenames truncate with title/tooltip rather than forcing width.
- Use the project's existing responsive primitives (Tailwind breakpoints, the shadcn `Sheet`/`Drawer` components, the established `min-h-0` flex scroll pattern) — do not hand-roll a new responsive system.

## Effective Access & Test Access (Windows-ACL inspired)

For a selected share/folder/file, one panel (right pane) + modal (`TestAccessModal`) with two parts:

- **Effective Access (static, no principal):** the resolved policy cascade affecting this path — global → org → longest-prefix override — listing each *matching* rule, what action(s) it grants, and which rule wins. "What governs this item."
- **Test Access (per-principal):** a **user dropdown** (real users, searchable) → resolves all four actions (read / write / delete / list), each **Allowed/Denied** with the **deciding policy + rule** named. Location and path are prefilled and locked from the selection; scope comes from the current org selector.

This replaces the current single-line Allowed/Denied tester and the free-text User ID field.

## Backend changes

1. **Seed `admin_bypass` on policy creation.** When a share/prefix first gets a policy (via `set_file_policy` / `upsert_policy`, or a "New policy here" / "New share" action), seed an editable `admin_bypass` rule mirroring `make_seed_admin_bypass()`. Admins are allowed by a visible, revocable policy. **No hardcoded bypass added to `is_allowed`.**
2. **Structural list endpoint** (admin-only, NOT policy-gated): returns shares / prefixes / keys that physically exist in a given scope. Powers the tree so nothing is orphaned. Distinct from the existing policy-gated `/list`. Excludes `workspace`/`temp`; marks `uploads` read-only.
3. **Denied vs. not-found clarity.** The policy-gated read/list must return 403 for policy-denied and 404 for path-not-found, distinctly, so the UI can say which. Today they conflate. The page's "access denied" helper surfaces 403 with a one-click "add `admin_bypass` here" affordance (admin only).
4. **Discover-locations endpoint:** enumerate freeform shares (locations) present in a scope — for the shares root + "New share". Excludes `workspace`/`temp`; flags `uploads` as read-only. May be folded into (2) as a `depth=0` mode.

Scope resolution continues to use the canonical `_file_org_id` → `resolve_target_org` (unchanged). Cascade-with-override in `load_policy` (org→global) is unchanged. `FileMetadata`/`FilePolicy` classification (allow-listed in `IDENTITY_MODELS`, prefix-keyed resolver) is unchanged.

## Testing

- **Vitest** per new component: `ShareTree` (lazy nav, expand), `FolderListing` (upload button + drag-drop, row actions), `EffectiveAccessPanel` (renders resolved cascade), `TestAccessModal` (user dropdown, per-action results + deciding rule), `NewShareDialog`.
- **Backend unit + e2e:** structural-list endpoint (admin sees un-policied files; non-admin does not get this endpoint), seeded `admin_bypass` (created on first policy; admin allowed via it; revoking it denies the admin), 403-vs-404 distinction on read/list, discover-locations excludes reserved + flags uploads read-only.
- **Playwright happy-path** (`*.admin.spec.ts`): select scope → browse a share → upload a file → preview it → open Test Access, pick a user, see per-action results → navigate to an un-policied prefix → see the structural listing (not a dead "denied") → see the denied helper on a content read where policy denies.
- **Responsive:** the Playwright happy-path runs at desktop and a narrow (mobile-width) viewport, asserting no pane/control is clipped — tree reachable (sheet), listing usable, preview/ACL openable, upload + Test Access reachable, no horizontal page overflow. Capture `--screenshots` at both widths for visual review.
- The existing `files-app-direct.admin.spec.ts` (SDK upload/list/read/download/delete + no-workflow-execution) stays as-is — the SDK surface is unaffected.

## Out of scope / non-goals

- **True cross-org shared pool for regular users.** Storage is per-scope; non-admins are pinned to their own org and cannot address `scope=global`. A "global pool everyone can read/write" is NOT introduced here — only the per-scope model and admin-reachable global. (Separately worth a design if desired.)
- No change to the `files`/`useFiles` SDK shape or the v2 app runtime.
- No change to scope resolution (`resolve_target_org`) or the org→global policy cascade.

## Open questions

- Whether the structural-list and discover-locations endpoints are one endpoint with a depth/mode param or two. (Implementation-plan detail; default: one endpoint, `depth` param.)
- Exact "New share" semantics — does creating a share require an initial policy, or is the share implied by its first policy/file? (Lean: "New share" opens `NewShareDialog` that creates the first policy, which seeds `admin_bypass`; the share then appears in the root.)
