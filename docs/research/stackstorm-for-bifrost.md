# StackStorm Learnings for Bifrost

Date: 2026-08-26

## Question

Should Bifrost embed or outsource automation to StackStorm, or should it adopt
specific StackStorm patterns while retaining Bifrost's execution and product
model?

## Executive conclusion

Bifrost should not embed StackStorm as its workflow or execution substrate.
StackStorm is a complete automation platform with its own API, auth/RBAC,
MongoDB persistence, RabbitMQ topology, scheduler, action runners, workflow
engine, content registry, CLI, UI, and operational services. Running it behind
Bifrost would create two authorities for definitions, identity, execution state,
authorization, audit, packaging, and lifecycle.

Bifrost should adopt four proven StackStorm ideas inside its existing model:

1. **Rules are first-class, evaluated bindings.** A trigger-to-target binding
   needs safe criteria, typed operators, deterministic input mapping, and a
   recorded decision. Bifrost already has the right durable lineage but its
   `EventSubscription.filter_expression` is currently inert.
2. **Execution policy belongs to the registered operation.** Concurrency,
   timeout, retry, and admission behavior should be discoverable policy rather
   than scattered worker configuration. This is already being advanced by the
   Celery-learnings epic and should remain one shared Bifrost policy model.
3. **Integration content is a deployable unit.** StackStorm packs validate the
   value of shipping actions, workflows, rules, sensors, configuration schema,
   dependencies, and tests together. Bifrost Solutions are the correct native
   equivalent and should be extended rather than adding a second pack format.
4. **Operators need causal history, not separate logs.** Bifrost should expose
   the whole source/event/rule-decision/delivery/execution chain as one trace.
   The database relationships mostly exist; decision evidence and a unified
   projection are the missing pieces.

The best first pilot is rule criteria on event subscriptions. It is bounded,
uses an already-advertised but unimplemented field, improves the event product
directly, and does not interfere with Bifrost's specialized workflow sandbox.

## Operator recommendation

Fund Milestone 1 and use it as the investment gate for the rest of the epic.
Do not approve the entire epic as one indivisible rewrite.

This is worthy of roadmap capacity for reasons that are visible in current
code, not because StackStorm is well known:

1. **It closes a real correctness gap.** Bifrost accepts and preserves a field
   named `filter_expression`, including examples such as priority filters, but
   runtime subscription selection never reads it. An operator can therefore
   configure what looks like a conditional automation and get unconditional
   execution. Removing that misleading contract or implementing safe criteria
   is necessary even if no other StackStorm lesson is adopted.
2. **It completes, rather than replaces, an existing architecture.** Event,
   delivery, workflow/agent reference, retry status, and terminal propagation
   are already durable. The pilot adds a decision between subscription lookup
   and delivery queueing; it does not require a new service, broker, database,
   worker, or execution engine.
3. **It improves safety at the highest-leverage boundary.** Filtering before
   execution prevents unwanted side effects, provider calls, agent spend, and
   noisy retries. The proposed criteria are bounded data with allow-listed
   operators, not user-supplied code.
4. **It creates the evidence needed for trustworthy operations.** Persisted
   match, no-match, and evaluator-error outcomes make “why did this run?” and
   “why did this not run?” answerable. Today absence of an execution is
   ambiguous and an inert filter can make execution surprising.
5. **It strengthens Bifrost's own product differentiation.** StackStorm's rule
   vocabulary is proven, while Bifrost retains its stronger organization,
   Solution, source-release, credential, and isolated-execution guarantees.

### Relative value and cost

| Investment | Expected value | Relative cost/risk | Recommendation |
| --- | --- | --- | --- |
| Milestone 1: criteria, decisions, authoring | High correctness, safety, and user-visible value | Medium; schema/contract/UI migration, but no new infrastructure | Fund now, subject to the production-data preflight. |
| Milestone 2: causal history | High operator value once real rule decisions exist | Medium; mostly read projection and UI, with authorization/query-plan risk | Fund if the pilot is used and operators still need cross-page diagnosis. |
| Milestone 3 / Issue 6: Solution validation | Medium-high release safety for event-heavy Solutions | Medium; touches portable/deploy validation | Fund from concrete Solution failures or adoption demand. |
| Milestone 3 / Issue 7: producer contract | Medium, potentially high for polling/streaming integrations | Medium-high; security, idempotency, and rate-limit surface | Defer until a named producer exists. |
| Milestone 4 / Issue 8: policy discovery | Medium operator clarity | Low-medium after the Celery policy registry lands | Reassess after that epic; do not duplicate its work. |
| StackStorm embedding or compatibility | Negative: duplicate control planes and state authorities | Very high operational and migration risk | Reject. |

### Preconditions

Before implementation, query each deployed environment for non-null
`event_subscriptions.filter_expression` values and owners. The result decides
whether the pilot can replace the field directly or needs an explicit,
operator-reviewed data migration. Repository examples alone are not production
evidence.

Confirm a product decision that structured criteria replace the expression
string. Supporting both indefinitely would create two semantic authorities and
is specifically not recommended.

### Stop conditions

Stop after the preflight or pilot if any of the following is true:

- existing expressions cannot be migrated without an open-ended expression
  language;
- target use cases require arbitrary code rather than bounded criteria;
- no current or committed event automation needs conditional routing;
- correct decision persistence would require a second state system;
- the pilot changes workflow/agent authorization or source-resolution rules;
- representative evaluation cost is material relative to event ingestion;
- operators do not value decision evidence enough to justify the UI surface.

If the pilot passes but later milestones lack a concrete consumer, keep the
criteria feature and stop. Milestone 1 is independently valuable; the epic does
not need to become a platform-wide imitation of StackStorm.

## StackStorm architecture in Bifrost terms

| StackStorm concept | Responsibility | Closest Bifrost concept | Assessment |
| --- | --- | --- | --- |
| Sensor | Poll/listen and emit a typed trigger | Webhook adapter, schedule source, internal topic producer, or scheduled workflow | Adapt the service contract; do not run arbitrary long-lived sensor plugins in the API process. |
| Trigger / TriggerInstance | Typed event definition and occurrence | EventSource / Event | Already a strong match. Bifrost adds organization and Solution scope. |
| Rule / RuleEnforcement | Criteria plus trigger-to-action mapping and decision | EventSubscription / EventDelivery | Largest semantic gap: Bifrost persists `filter_expression` but never evaluates it or records a filter decision. |
| Action | Registered invocable operation with typed inputs | Workflow, agent, PlatformJob definition, MCP tool | Keep Bifrost's separate domain types; a universal action wrapper would erase useful security and lifecycle distinctions. |
| Runner | Execution environment for an action type | Workflow process pool, agent worker, PlatformJob runner, MCP dispatch | Keep Bifrost. Its one-shot tenant isolation, source pinning, credential boundary, and durable job contracts are product guarantees. |
| Workflow / Orquesta | Graph orchestration over actions | Python workflow plus SDK/tool calls | Borrow authoring and observability ideas only. Replacing Python workflows would be a product rewrite and a regression for dynamic code. |
| Pack | Versioned actions, workflows, rules, sensors, config, dependencies, tests | Solution | Strong conceptual match. Prefer a documented translation and completeness checklist over format compatibility. |
| Policy | Pre-run concurrency and execution constraints | Execution policy and PlatformJobPolicy | Adopt in the canonical Bifrost policy registry; do not create an event-specific policy subsystem. |
| Inquiry | Paused execution awaiting a human response | No single canonical equivalent | Potential later product capability; it requires durable suspension/resumption and authorization, not a thin UI prompt. |
| Datastore | Scoped values and encrypted secrets | Configs and integration mappings/OAuth | Bifrost's tenant/integration model is stronger and should remain authoritative. |
| History/audit | Trigger, rule, action execution history plus audit logs | Event/EventDelivery, Execution/AgentRun, audit_logs | Data exists in fragments; causal decision evidence and one projection are missing. |

## What StackStorm would genuinely provide

- A mature event-driven automation vocabulary that cleanly separates event
  ingestion, typed triggers, matching, action invocation, and orchestration.
- Declarative criteria with explicit operators instead of executable user
  expressions.
- A large body of operational learning around action-runner lifecycle,
  concurrency policies, execution history, cancellation, and active-active
  services.
- A disciplined integration packaging convention with isolated Python
  dependencies and metadata-driven registration.
- Reusable integration content and a community exchange.

These strengths are worth learning from. They are not evidence that Bifrost
should run StackStorm itself.

## What Bifrost legitimately improves upon

### Tenant and source scope

Bifrost definitions and executions carry organization, Solution, workspace,
release, and caller context. Workflow dispatch pins authorized source evidence
and the worker verifies it before running. StackStorm primarily assumes a
registered pack and action already installed on its hosts.

### Execution isolation

Bifrost's workflow engine uses a template lifecycle and fresh execution process,
with cgroup-aware admission, deadlines, cancellation, runtime generation
fencing, scoped context, and credential scrubbing. A StackStorm runner is an
execution adapter, not an equivalent tenant-code sandbox.

### Product-specific durable work

PlatformJob provides typed/encrypted payloads, PostgreSQL leases and fencing,
deduplication, resource locks, progress, cancellation, caller visibility, and
shared REST/CLI/WebSocket projections. StackStorm action state should not become
a second authority for those guarantees.

### Portable product bundles

Bifrost Solutions combine platform entities with install ownership, scope,
release evidence, deploy/uninstall behavior, and source access rules. StackStorm
packs validate co-location and reuse, but adopting the pack format would discard
Bifrost-specific lifecycle semantics.

## Current Bifrost event path

```text
Webhook adapter / cron scheduler / internal topic producer
    -> EventSource
    -> immutable Event
    -> active EventSubscription rows
    -> EventDelivery per target
    -> workflow Execution or AgentRun
    -> delivery terminal status and retry-exhausted topic
```

Existing strengths:

- durable event and per-target delivery rows;
- organization and Solution scope;
- workflow and agent targets;
- deterministic input mapping;
- retry attempts and terminal propagation;
- WebSocket updates;
- source-specific webhook verification and rate limiting;
- internal lifecycle topics;
- portable manifest/Solution serialization.

Verified gaps:

- `EventSubscription.filter_expression` is written, returned, exported, and
  imported, but no runtime code reads it;
- a non-matching decision therefore cannot produce a durable `skipped` delivery
  with decision evidence;
- criteria syntax and operators are not validated at write time;
- the causal chain is spread across event, delivery, execution/agent, and audit
  APIs instead of exposed as one operator view;
- webhook adapters are built-in Python registrations, with no explicit contract
  for externally operated polling/event producers;
- action-like capabilities have multiple deliberate domain registries, but no
  single discovery projection explaining runner and policy semantics.

## Adopt / adapt / reject

### Adopt

- structured rule criteria with an allow-listed operator registry;
- recorded match/no-match/error decisions;
- resource-attribute concurrency policy where the canonical execution policy
  can support it;
- causal execution history from trigger through terminal action;
- a Solution completeness checklist inspired by packs.

### Adapt

- sensors into externally operated producers or scheduled Bifrost workflows
  that emit typed topics;
- action metadata into discovery projections over Bifrost's existing workflow,
  agent, PlatformJob, and tool definitions;
- pack dependency/config conventions into Solution validation;
- inquiries into a future durable pause/resume primitive with scoped approval.

### Reject

- embedding the StackStorm service topology;
- using MongoDB or StackStorm execution records as a second state authority;
- replacing Bifrost workflow execution with local/remote StackStorm runners;
- loading arbitrary long-lived sensor plugins into Bifrost services;
- translating Bifrost workflows to Orquesta;
- adopting the StackStorm pack filesystem as a second Solution format.

## Pilot gate

The rule-criteria pilot succeeds only if all of the following hold:

- criteria are data, never evaluated Python/Jinja/SQL/JSONPath code;
- invalid criteria are rejected before persistence and during import/deploy;
- the same evaluator is used for webhook, schedule, and topic events;
- matching creates and queues exactly one delivery per subscription;
- non-matching creates a terminal `skipped` delivery with safe decision evidence;
- evaluator errors fail closed and are observable without leaking event secrets;
- existing subscriptions with no criteria retain unconditional behavior;
- organization/Solution target resolution and execution authorization do not
  change;
- manifest, Solution, REST, CLI/MCP parity, and focused end-to-end tests cover
  the new contract;
- evaluation adds no broker or secondary persistence dependency.

## Primary sources

- StackStorm overview: <https://docs.stackstorm.com/overview.html>
- Actions and runners: <https://docs.stackstorm.com/actions.html>
- Sensors and triggers: <https://docs.stackstorm.com/sensors.html>
- Rules: <https://docs.stackstorm.com/rules.html>
- Workflows and Orquesta: <https://docs.stackstorm.com/workflows.html>
- Packs: <https://docs.stackstorm.com/packs.html>
- Execution policies: <https://docs.stackstorm.com/reference/policies.html>
- History and audit: <https://docs.stackstorm.com/reference/history.html>
- HA service topology: <https://docs.stackstorm.com/latest/reference/ha.html>
- StackStorm source and Apache-2.0 license: <https://github.com/StackStorm/st2>
