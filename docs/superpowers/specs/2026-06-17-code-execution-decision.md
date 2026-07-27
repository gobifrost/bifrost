# Code Execution — Decision Record: bwrap rejected, `external` runner is the path

**Status:** Decided (2026-06-17). Supersedes the bwrap sandbox direction in the Chat V2 program spec sub-project 2.
**Author:** Jack Musick + Claude
**Outcome:** **Do not build an in-house bubblewrap (`bwrap`) sandbox.** Script execution is `trusted` (existing engine) now; untrusted execution, if/when needed, goes through an `external` runner protocol (Anthropic Managed Agents as the first reference runner). No code shipped from this exploration — the `code-exec-foundation` branch was abandoned.

## What we explored

The Chat V2 program spec (`2026-04-27-chat-v2-program-design.md` §3) and the bwrap findings doc (`2026-04-27-chat-v2-sandbox-bwrap-findings.md`) proposed sub-project 2 "Code Execution" as a bubblewrap sandbox shelled out from workers, to run **untrusted** code (community skill scripts, doc-generation shelling out to pandoc/soffice/pdftoppm) with zero SDK/credentials/network. A foundation slice was built on a worktree and then abandoned once this decision was made. None of it is on `main`.

## Why bwrap is rejected

1. **Not reasonably CI-testable.** bwrap needs unprivileged user namespaces; GitHub Actions `ubuntu-24.04` runners hit the AppArmor restriction (would need pinning to `22.04` or a pre-loaded profile), and the cloud providers that need host-level config (`kernel.apparmor_restrict_unprivileged_userns=0`, `user.max_user_namespaces`, Bottlerocket node user-data) can't be exercised in CI at all. The platform owner's hard requirement is "nothing we can't reasonably test in CI." bwrap fails that.

2. **Kernel-posture cost in our deployment.** Empirically (hardening-plan ADDENDUM T3, 2026-06-10), an in-container bwrap doing `mount(MS_SLAVE)` needs `CAP_SYS_ADMIN` — `seccomp=Unconfined` alone is insufficient. Running it in the trusted worker would re-grant `SYS_ADMIN` and drop `readOnlyRootFilesystem`, fighting the Phase-1 worker hardening that just removed those. The alternative — a dedicated kernel-relaxed sandbox-runner pod — is real infra (custom seccomp profile, separate deployment, RPC seam) and a support burden for every OSS deployment.

3. **Anthropic's "easy" path doesn't transfer.** Claude Code's `sandbox-runtime` is easy because it runs on a developer's own machine / CI runner where the user already holds the needed privileges. That is not a multi-tenant hardened server. Anthropic's *server-side* answer is **hosted Code Execution / Managed Agents** — they run the sandbox in their own infra, exposed over the API. The transferable lesson is "call a hosted runner," not "run bwrap in your cluster."

## The decision

**`script_execution: disabled | trusted | external`** (from the 2026-06-13 Agent Skill Bundles brainstorm; canonical in `2026-06-17-agent-skill-bundles-and-capabilities-design.md` Part A.5):

- **`disabled`** — scripts are inert files; `read_skill_asset` still works. Default for imported/community bundles.
- **`trusted`** — scripts run through the **existing Bifrost execution engine** with full `ExecutionContext`, SDK, logs, audit, timeout, and the same caller/org permission model as workflow code. The shipping model. No sandbox, by design.
- **`external`** — a configured external runner accepts script text + input JSON, runs with **no Bifrost credentials**, returns stdout/stderr/result. The untrusted escape hatch. **Anthropic Managed Agents is the first reference runner.** A "container mode we flag / call over the API," not a kernel sandbox we operate. **Protocol-only — not implemented until actually needed.**

bwrap, `sandbox-python`/`sandbox-node` runtimes, the in-cluster sandbox-runner pod, the `unshare -U` preflight, and `enableWeakerNestedSandbox` are all **out** — they were specific to the rejected bwrap path.

## Effect on dependent designs

- **Agent Skill Bundles** (`2026-06-17-agent-skill-bundles-and-capabilities-design.md`): Part A.5 is the source of truth. Part B ("Code Execution stays its own sub-project / bwrap substrate") is **superseded by this record** — there is no bwrap substrate. Untrusted skill scripts run via `external` when that runner exists.
- **Artifacts** (Part C): binary doc generation gets no bwrap sandbox. In `trusted` mode it runs in the existing engine; untrusted generation would go through `external`. File-first v1 (the shipping artifact tier) needs neither — a trusted workflow tool can produce files today.

## When the `external` runner is actually built

Build fresh against the runner protocol then — do not resurrect bwrap scaffolding. The health/graceful-fail story (runner reachable / authed / echoes correctly → advise → fail-closed) belongs **with that feature**, not now. Likely shape: per-org runner endpoint+credentials config, a structured health check, admin-visible status, fail-closed when misconfigured. Nothing ships ahead of the runner.

## References
- `2026-06-17-agent-skill-bundles-and-capabilities-design.md` — Part A.5 (script execution modes) is canonical.
- `2026-04-27-chat-v2-program-design.md` §3 — the original bwrap proposal (superseded for our deployment).
- `2026-04-27-chat-v2-sandbox-bwrap-findings.md` — empirical bwrap testing; kept as the record of *why* in-cluster bwrap is hard.
