# Contributing to Bifrost

Thanks for contributing. This doc is the friendly front door — it covers the *spirit* of how we work. The mechanical rules live in [`CLAUDE.md`](./CLAUDE.md) (which both humans and LLMs read), and the reviewer-side checks live in [`.claude/skills/reviewing-prs/`](./.claude/skills/reviewing-prs/).

## Before you open a PR

- [ ] Dev stack is running (`./debug.sh`) and your change works end-to-end in the browser at `http://localhost:3000`.
- [ ] Tests exist for the code you changed (see **Testing expectations** below).
- [ ] `pyright` + `ruff check` pass in `api/`; `npm run tsc` + `npm run lint` pass in `client/`.
- [ ] If you changed `api/shared/models.py` or API contracts, you re-ran `npm run generate:types` in `client/`.
- [ ] If you touched a sensitive path, you've called it out in the PR description.

## Testing expectations

All work ships with tests. The full matrix of what goes where lives in [`CLAUDE.md` → Testing & Quality](./CLAUDE.md#testing--quality) — read that for specifics. A few high-level notes:

**Backend.** Business logic belongs in `api/tests/unit/`. Anything that crosses an endpoint, queue, or DB boundary belongs in `api/tests/e2e/`. Always run via `./test.sh`, never pytest directly on the host — the Dockerized stack is part of the test.

**React components.** Ship a sibling `*.test.tsx` (vitest) covering the rendered behavior, not the implementation.

**Functional frontend modules.** New or modified `.ts` files under `client/src/lib/**` and `client/src/services/**` that export functions need a sibling `*.test.ts`. Storage adapters, auth helpers, API wrappers, and anything with a cross-tab/cross-window concern need tests that exercise that boundary specifically — regressions that only show up with two tabs open are the kind a future refactor will silently re-introduce. Pure type/constant re-export files and thin third-party SDK wrappers are exempt.

**User-facing features.** Add a happy-path Playwright spec in `client/e2e/`.

## Sensitive paths

Some areas of the codebase need a higher bar — auth, execution engine, multi-tenancy filters, migrations, secrets, manifest round-trip, audit logging. The canonical list (with rationale and reviewer focus areas) is [`.claude/skills/reviewing-prs/sensitive-paths.md`](./.claude/skills/reviewing-prs/sensitive-paths.md).

If your change touches any of those, expect:
- A manual review regardless of PR size.
- A reviewer asking about tests for the specific failure mode, not just "does it compile."
- Higher scrutiny on any code path that could cross tenant boundaries, leak secrets, or skip an audit.

Call it out in the PR description so the reviewer doesn't have to rediscover it.

## How to pick up work

All trackable work — bugs, features, chores, ideas — lives in [GitHub Issues](https://github.com/gobifrost/bifrost/issues). If it's not an issue, it's not on the roadmap.

**Looking for something to pick up?**

- [`help wanted`](https://github.com/gobifrost/bifrost/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) — issues I won't get to soon and would love help on.
- [`good first issue`](https://github.com/gobifrost/bifrost/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) — ramp-up-friendly, smaller scope.
- Unassigned issues without those labels are technically takeable, but I may already have them in my head — leave a comment first to avoid double work.

**Claim an issue** by self-assigning from the issue sidebar. If you change your mind, unassign — no hard feelings.

**Filing a new issue?** Pick the right template from the issue picker:

- **Bug** — something is broken or behaves unexpectedly.
- **Feature** — a new capability or meaningful enhancement. Link a design doc from `docs/superpowers/specs/` if one exists.
- **Chore** — maintenance, tooling, deps, CI, refactor without user-visible change.
- **Blank issue** — rapid capture for ideas or anything that doesn't fit the above.

## Commit & PR style

Conventional commits. The type in the subject should match what actually changed:

- `feat(apps):` — new user-facing capability
- `fix(embed):` — a bug fix, scoped to the affected area
- `refactor(execution):` — internal change, no behavior difference
- `docs(plans):` — plan docs, design notes, READMEs
- `test(forms):` — test-only change
- `chore(deps):` — dependency bumps, tooling

PR titles follow the same convention. Keep them under 70 characters; put the detail in the description. The description is the place to say *why* — a diff shows what changed but rarely why it had to change.

## When a rule doesn't fit

Rules are written against the common case. Sometimes the common case doesn't apply. If you're bending a rule deliberately, say so in the PR description with a one-line rationale ("skipping vitest coverage on `foo.ts` because it only re-exports types"). That turns rule-bending from a silent decision into a visible one, and lets a reviewer push back or agree on the spot.

Open an issue or a draft PR if you're unsure. Standards that survive are the ones where exceptions are documented, not hidden.
