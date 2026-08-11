# Product

<!-- impeccable:product-schema 1 -->

> Product truth captured from the owner's explicit Code Builder recovery brief
> and the existing repository on 2026-08-07. Items still requiring a product
> decision are called out as open rather than inferred.

## Platform

web

## Users

- Managed service providers creating and delivering integration services for
  many customer organizations.
- Customer builders who need to create useful applications, workflows, forms,
  agents, data tables, and supporting files without leaving Bifrost.
- MSP operators, reviewers, and support staff who help customers, collaborate
  on private work, control spend, and promote approved releases.
- Platform administrators who configure AI and sandbox providers, permissions,
  budgets, health, and deployment readiness.

## Product Purpose

Bifrost makes custom integration development an operationally scalable MSP
service. Success means a customer can build a complete, portable Solution in a
first-class native experience, return to the same conversation and preview,
collaborate with their provider, and publish an approved revision without
giving a coding sandbox broad platform credentials.

## Positioning

Bifrost joins full-stack Solution authoring, runtime data and integrations,
portable Agent Skills, deployment, and ongoing MSP support in the same
multi-tenant platform. The native Builder, CLI, and external MCP coding
harnesses use the same authoring capabilities, so customers are not trapped in
one chat surface and providers can support what customers create.

## Operating Context

- Ordinary users start with their own work and explicitly shared work.
- Provider staff can deliberately switch to an all-customer support view and
  filter by organization, owner, status, or review need without cluttering
  their normal workspace.
- A build is a durable PlatformJob. Its conversation, source revisions,
  preview state, progress, logs, costs, and approval history must survive a
  browser session or scheduler restart.
- Hosters configure a managed coding-sandbox provider from the admin UI and
  receive live readiness feedback before enabling Builder access. Cloudflare
  is the recommended production provider; a local provider supports
  development and deliberate self-hosting.
- Generated applications remain available at transparent `/apps/{slug}`
  routes. Existing trusted V1 and V2 applications keep their current behavior.

## Capabilities and Constraints

- Builders can author complete Solutions: applications, workflows, forms,
  Agent Skill bundles and assets, tables and policies, configuration or
  integration requirements, runtime files, and access relationships.
- `SKILL.md`, when present in an agent bundle, is the agent's instruction
  source. Solution-managed bundles are browsable but not edited independently
  of their Solution revision.
- Builder execution is provider-neutral, isolated, metered, cancellable, and
  least-privileged. A sandbox receives only a job-bound lease/capability and no
  general Bifrost, AI-provider, or Cloudflare credential.
- PlatformJob is the single durable orchestration, progress, retry,
  cancellation, deduplication, and resource-protection system. Builder does not
  introduce a second coordinator or public job endpoint.
- Role scopes follow one consistent, Graph-inspired naming system. Human
  Builder permissions and machine job leases are separate concepts.
- Tenant isolation is mandatory. Administrative or impersonation access is
  explicit, auditable, and kept out of the default personal catalog.
- Production setup must not require a second public port. Any unavoidable DNS
  or account prerequisite must be explained and validated in the admin UI.
- No silent managed-to-local fallback is permitted; changing execution
  providers is an administrator decision visible to users.
- Open: final commercial quota defaults and Cloudflare markup/allocation rules.
- Open: whether MSPs may define per-customer visual branding for Builder.

## Brand Commitments

- Product name: Bifrost.
- Voice: direct, technically credible, calm under failure, and explicit about
  what the system is doing or needs from an administrator.
- The Builder should feel comparable in fluency and polish to established app
  builders while remaining recognizably part of Bifrost's existing product UI.

## Evidence on Hand

- Builder product design:
  `docs/superpowers/specs/2026-07-25-private-solution-builder-design.md`
- Agent Skill model:
  `docs/superpowers/specs/2026-06-17-agent-skill-bundles-and-capabilities-design.md`
- Historical Builder status:
  `docs/superpowers/specs/2026-07-27-private-solution-builder-status.md`
- Recovery architecture and acceptance ledger:
  `docs/superpowers/specs/2026-08-07-code-builder-recovery.md`
- Existing visual tokens and component conventions: `client/src/index.css` and
  `client/src/components/ui/`
- No customer testimonials, public pricing claims, or Builder performance
  benchmarks are approved for invention.

## Product Principles

1. Start with the user's work; reveal provider-wide scale deliberately.
2. A build is a recoverable collaboration, not a disposable prompt response.
3. Native and external builders share one complete Solution capability model.
4. Show readiness, progress, cost, and failure recovery where decisions occur.
5. Preserve portability and vendor choice without weakening sandbox isolation.

## Accessibility & Inclusion

Builder must remain operable with keyboard and assistive technology, preserve
logical focus and reading order across its resizable workbench, respect reduced
motion, and target WCAG 2.2 Level AA for the shipped web interface.
