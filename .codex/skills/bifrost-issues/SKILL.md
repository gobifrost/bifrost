---
name: bifrost-issues
description: Evaluate Bifrost ideas, issues, and pull requests with sequential product checks. Use before accepting scope, implementing work, opening a PR, or queueing a merge. Owns issue tracking, isolated worktrees, and authorized PR delivery.
---

# Bifrost product checks

Judge the need and the proposed implementation separately. Run the applicable steps in order. Stop when a check fails, give the deciding evidence, and rerun that check after revising the proposal or change. Use [the verdict format](references/verdict.md) for triage.

## Choose the mode

- **Idea** — evaluate a request, issue, design, or incoming PR for product acceptance. Run steps 0–3 and the UI design check when applicable. An incoming implementation also supplies evidence for steps 4–5 if reviewing its readiness. Give a recommendation without taking external action.
- **Build** — run steps 0–3 on the proposed scope before editing. Use [delivery setup](references/delivery.md) to track the work and enter an isolated worktree. Run steps 1–5 against what actually changed before opening or queueing a PR. Then use step 6 for authorized delivery.
- **PR operations** — for a status check, label, assignment, queue, or cleanup request, use [delivery](references/delivery.md) for the requested operation. Before queueing, establish that the change has passed the applicable checks.

## Run the checks

| Step | Pass when | Reference |
|---|---|---|
| 0. Current State | Existing work and latest status are accounted for. | [Current state](references/current-state.md) |
| 1. Need | Evidence establishes a user need, verified shipped defect, or maintainer mandate. | [Need](references/need.md) |
| 2. Fit | The proposal belongs in the chosen layer and builds on the existing path where possible. | [Fit](references/fit.md) |
| 3. Design | Standards, tenancy and compatibility are accounted for. | [Design](references/design.md) |
| 4. UI | For a proposal, the reference and shared components are identified. For a change, the rendered behavior is verified against them. | [UI](references/ui.md) |
| 5. Verify | Focused checks cover the changed behavior, its consumers and affected contracts. | [Bifrost testing](../bifrost-testing/SKILL.md) |
| 6. Deliver | The authorized issue, PR or merge operation is complete. | [Delivery](references/delivery.md) |

After Need passes, a defect fix that restores intended behavior and preserves contracts can cover steps 1–3 in one line each. Cite the evidence, name the path repaired, and identify the consumers checked. Judge the actual change and affected contracts regardless of the title or label.

Investigate material facts using the repo, accessible discussion, and primary standards or provider docs. Ask one concrete question only when the answer could change the verdict. If a later check already determines the outcome regardless of the answer, give that decision. Assess separable asks independently. For an RFC, list open decisions with a recommendation for each.

Product direction belongs to maintainers. A passing verdict carries no permission to implement, publish, close, or merge an item.
