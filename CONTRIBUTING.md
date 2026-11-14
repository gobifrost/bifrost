# Contributing to Bifrost

Thanks for contributing. This doc is the friendly front door — it covers the *spirit* of how we work. The mechanical rules live in [`CLAUDE.md`](./CLAUDE.md) (which both humans and LLMs read), and the reviewer-side checks live in [`.claude/skills/reviewing-prs/`](./.claude/skills/reviewing-prs/).

## Contribution Scope

We welcome contributions grounded in actual use of Bifrost, work requested by maintainers, and verified security findings. AI assistance is welcome. The contributor is responsible for understanding the submission, checking its relevance, and answering review questions.

Before opening an issue or PR, identify which of these applies:

- **Actual use.** Describe the concrete task you were performing or a deployment you are actively preparing, the limitation you encountered, and its practical consequence. Include the version, existing workaround, and reproduction or measurements where relevant. Development, testing, deployment, upgrades, and operation of a real workflow qualify; an invented scenario does not. Features need a real use case, not a defect reproduction.
- **Maintainer-requested work.** Link the invitation and keep the submission within its scope. A measurement exercise does not authorize a redesign. An invitation to share a prototype does not establish feature acceptance.
- **Security vulnerability or hardening.** Follow SECURITY.md. Demonstrate a credible security impact or a verified gap against an applicable adopted security requirement. Actual exploitation or a customer incident is not required.

Unsolicited automated searches for non-security bugs, optimizations, refactors, or hypothetical use cases are outside our contribution scope. Keep those investigations in your own workspace or fork. A reproduction, benchmark, test suite, or completed PR does not by itself make a submission eligible.

Explain what Bifrost already supports and why it does not meet the need. Distinguish observations from hypotheses. Performance claims need representative measurements; a smaller dependency list or a different design is not proof of faster execution. Maintainers may choose measured maintenance work without first encountering a production incident.

Review agent-generated submissions deliberately before publishing. Do not configure agents to create issues, PRs, or replies autonomously. Consolidate related findings around their shared cause and practical impact. Do not turn one investigation into a stream of speculative follow-on issues.

A bug report does not need a PR. A PR needs the same relevance and evidence as an issue. Passing tests and complete implementation do not establish product acceptance or justify ongoing maintenance. Discuss substantial features or architectural changes before implementation.

Issues track reported problems and proposals; their existence, labels, assignment, or lack of rejection does not place them on the roadmap. Maintainers may decline work outside this scope without exhaustively investigating every hypothetical claim.


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

GitHub Issues tracks eligible reports, proposals, and maintainer-selected work. An open issue does not establish roadmap acceptance. Keep speculative ideas in [GitHub Discussions](https://github.com/gobifrost/bifrost/discussions) or Discord until there is a concrete use case and a decision to pursue them. See [Work Tracking](docs/work-tracking.md) for parent issues, dependencies, and the Project.

**Looking for something to pick up?**

- [`help wanted`](https://github.com/gobifrost/bifrost/issues?q=is%3Aissue+is%3Aopen+label%3A%22help+wanted%22) — issues I won't get to soon and would love help on.
- [`good first issue`](https://github.com/gobifrost/bifrost/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22) — ramp-up-friendly, smaller scope.
- Check the discussion for accepted scope and an existing owner before starting. Ask before implementing a substantial proposal.

**Claim an issue** by self-assigning from the issue sidebar. If you change your mind, unassign — no hard feelings.

**Filing a new issue?** Pick the right template from the issue picker:

- **Bug** — something is broken or behaves unexpectedly.
- **Feature** — a new capability or meaningful enhancement. Link a design doc from `docs/superpowers/specs/` if one exists.
- **Chore** — maintenance, tooling, deps, CI, refactor without user-visible change.
- **Blank issue** — eligible work that does not fit another template; explain its basis and evidence.

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

For eligible work, explain any uncertainty in the report or proposal. Keep speculative findings private; use Discussions or Discord to gauge interest before graduating an idea to an issue.
