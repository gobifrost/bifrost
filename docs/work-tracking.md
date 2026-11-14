# Work Tracking

GitHub owns scope and completion. The [Bifrost Work Project](https://github.com/orgs/gobifrost/projects/1) shows status, priority, workstream, and existing deferral metadata. Project membership, a lane, or assignment does not grant implementation or merge authority.

## Issues, Relationships, And Pull Requests

Use one issue for a concrete outcome. For a delivery spanning independently reviewable outcomes, use a parent issue with native sub-issues. Add a native blocked-by relationship only when completing one outcome is a real prerequisite for the other. Related subjects and work that can proceed in parallel are not blockers.

Keep current owners and acceptance decisions. Read issue discussion and linked delivery before creating a new issue. A partial fix does not complete an umbrella; record the remaining eligible scope and preserve credit for delivered work. Separate optional follow-ons from the parent's completion criteria. Private research and interest checks belong outside the issue backlog until selected or grounded under CONTRIBUTING.md.

Link implementation PRs to the issue they complete. Use Fixes #N only for that issue's full scope; use Part of #N for partial work. A tracking comment or checklist explains a relationship but is not a native dependency or a merge check.

For dependent changes, use a native GitHub PR stack when all branches are in the same repository and their branch ancestry supports it. GitHub does not support cross-fork stacks. Do not retarget a contributor's PR or copy their branch just to make the display look stacked. Until a legitimate stack exists, record the prerequisite in the linked issues, preserve the review owner, and land changes in dependency order through existing protected checks. Before queueing a dependent PR, verify its prerequisite outcomes and required code are delivered, or are included below it in a legitimate protected native stack. Issue links and closed checkboxes are not proof that the required code is present or verified. Independent PRs remain independent.

## Project And T3

Use Workstream to group work without inventing broad parent issues. Preserve existing manually selected status, priority, owners, and deferrals. Add missing open issues and PRs to the Project; standalone work does not need an artificial parent or blocker.

T3 retains the existing owner, evidence, decisions and scoped grants. Read native parent/blocker relationships and the Project before proposing work. Blocked implementation may still allow design discussion. An open prerequisite does not justify bypassing an approval or starting another owner. Do not wake owners or repeat reviews merely to rearrange metadata.

On completion, verify GitHub merge/closure, reconcile any open remainder, and update the existing record. Existing release/date/manual deferral rules and explicit contributor waits remain authoritative. Native issue relationships organize work; they do not themselves enforce PR merge order.
