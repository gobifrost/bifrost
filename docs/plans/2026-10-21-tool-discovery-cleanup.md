# Tool Names and Specialist Discovery

An instruction can describe an operation without naming its callable. The callable
name and input schema come from runtime discovery. A rejected guessed call must
execute nothing and receive the framework's corrective response.

The observed naming mismatch was mechanical: a registered tool named
`halopsa_submit_triage` in the `AI Ticketing` category became
`ai_ticketing_halopsa_submit_triage`, while instructions referred to the registered
name. The registry also repeated matching prefixes, such as
`halopsa_halopsa_submit_triage`. Normalize a matching category prefix once. Align
related instance tools under the `HaloPSA` category and `halopsa_*` names through
the supported workflow-update CLI, preserving workflow IDs and function locators.
Search named consumers before renaming; UUID bindings do not protect a consumer
that executes a workflow by name.

## Implementation Precedents

- [Pydantic AI on-demand capabilities](https://github.com/pydantic/pydantic-ai/blob/main/docs/capabilities/on-demand.md)
  advertise capability IDs and descriptions before loading instructions. Loading
  a capability and searching individual deferred tools are distinct mechanisms.
  Bifrost's instruction-only capabilities and filtered standalone toolset retain
  that separation.
- [pydantic-ai-skills](https://github.com/DougTrajano/pydantic-ai-skills/blob/main/pydantic_ai_skills/capability.py)
  uses native `load_capability` and supplies exact bundled-resource and script
  names after loading. Its `_bundled_files_section` explicitly addresses the
  mismatch between prose references and identifiers needed by calls.
- [pydantic-deepagents](https://github.com/vstorm-co/pydantic-deepagents/blob/main/pydantic_deep/features/skills/toolset.py)
  has an explicit list/load/resource/script sequence. Its load description tells
  the model to use exact resource and script names; missing-name handling reports
  the available identifiers. This is a separate implementation precedent, not a
  reason to add another discovery interface to Bifrost.

These sources show discover-then-use contracts. They do not establish that models
cannot attempt unknown calls, or that changing names improves task completion.

## Execution Guidance

Automatic mode uses discovered specialist tools in the current conversation by
default. An isolated child needs a concrete context-isolation or independent-review
requirement. Routine routing instructions should describe the specialist skill
and required evidence without directing a new child conversation. Automatic
discovery omits legacy `delegate_to_*` aliases; native `delegate_task` remains
available for the required isolated cases. The existing bindings still supply
its execution path, without changing Delegated mode or external MCP calls.

A permission denial applies to the operation under the original caller. Another
agent or tool is not an alternative authorization path. An uncertain write needs
verification before any repeat. Business approval, exact resource authority,
independent review, and finalization contracts remain in force.

## Verification and Rollout

Test matching-prefix normalization and the existing category-prefix behavior.
Force an unknown action call through the real framework, verify zero executor
calls, then discover and invoke the exact valid tool once. Retain the existing
graph-revocation, schema-binding, child-lifecycle, and chat-continuation coverage.

Keep private instance definitions and customer evidence out of this repository.
Deploy the core naming change before applying dependent instance metadata. Update
named consumers together with the rename, then read back names, workflow IDs,
function locators, permissions, agent bindings, and prompts. Use synthetic or
read-only verification, never real customer mutations as a replay fixture.
