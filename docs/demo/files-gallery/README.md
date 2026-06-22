# Files SDK gallery demo (scratch)

Throwaway scripts that stand up a working demo of the Files Web SDK on a **port-mode** debug stack, used to drive the feature end-to-end as a real user. Not tests, not shipped — a reproducible manual-shakeout harness.

## What it builds
- An inline `gallery` app (`/apps/gallery`) that uses `files.upload` / `useFiles` / `files.download` / `files.delete` directly (no workflow runs) — `gallery_layout.tsx` + `gallery_index.tsx`.
- Two orgs (A, B) + users **alice@gallery.example.com** (Org A, can write) and **bob@gallery.example.com** (Org B, read-only via an org override). Admin: **dev@gobifrost.com**. All password `password`.
- `shared/gallery` policies: global read/write (cascades), Org B read-only override. Seeds files into global / orgA / orgB trees to show per-org isolation.

## Run
```bash
# from the worktree root — port mode (Chrome/Playwright can't drive netbird):
BIFROST_FORCE_PORT=1 ./debug.sh up      # note the http://localhost:<port> URL
# edit BASE in setup.mjs to that URL, then:
node docs/demo/files-gallery/setup.mjs   # creates orgs/users/app/policies/files
node docs/demo/files-gallery/drive_app.mjs  # screenshots the app as admin/alice/bob -> /tmp/files-drive
```

## Gotchas (baked into the scripts)
- New users created via `/api/users` have **no password auth** — must `POST /api/auth/register` to enable it (the script does this).
- Presigned PUT URLs point at the in-cluster `seaweedfs:8333`, **unreachable from the host** — seed via `POST /api/files/write` (base64), not signed-URL + PUT.
- The `.test` TLD is rejected by the email validator — use `.example.com`.
- `shared` is **per-org storage**: alice and bob see different files; the global pool is only reachable by superusers (see the `project_files_sdk_status` memory).

This demo exposed that the admin Files **page** needs a redesign — see `docs/superpowers/specs/2026-06-22-files-explorer-redesign-design.md`.
