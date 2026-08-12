# Solution Runtime Resource Access

Read this reference when Solution code needs anything outside its install.

`global_repo_access` is historical naming. It is not install scope and not blanket access to every global resource. It gates shared fallback for resource types that also have a Solution-owned tier: `_repo` modules, registered loose workflows, tables, and managed files.

Config values, integrations/OAuth, and knowledge are shared instance resources with their own org/global resolution regardless of this flag. Normal authentication, organization, role, policy, and external-user checks always remain active.

## Runtime matrix

| Resource | `global_repo_access: false` | `global_repo_access: true` | Write boundary |
|---|---|---|---|
| Python modules | Install source only | Install source, then eligible instance `_repo` module | Code changes through its owning source/deploy or direct `_repo` file write |
| Workflows | Own install only | Own install, then eligible registered loose install-org/global workflow | Each workflow executes as its own record/owner |
| Tables | Own Solution table only | Own table, then loose install-org/global table by name | Shared fallback table is read-only from Solution context |
| Managed files | Own declared location only | Own tier, then eligible install-org/global reads in the declared location | Writes/deletes target the Solution-owned tier |
| Config/secrets | Shared org/global cascade | Same | Mutations change shared environment state |
| Integrations/OAuth | Shared org mapping/defaults | Same | Mapping/token changes affect shared environment state |
| Knowledge | Shared org/global namespace | Same | No private Solution tier |

For an org install, eligible shared fallback searches the install org and then global. It does not search sibling Solutions or arbitrary organizations.

Forms, agents, apps, event sources, and claims are installed entities, not general shared SDK lookups. Never infer access to them from this flag.

## How context is carried

A deployed app's scaffolded provider sends its app identity on workflow, table, and file requests. The server resolves that app to its active Solution install. A Solution workflow carries its `solution_id` in execution context. Local `solution start` supplies equivalent install context through its proxy.

Do not hand-build app/Solution headers or mix an app identity with a different explicit Solution. Mismatches are rejected.

## Modules and workflows

Module imports resolve install-owned source first. A sealed Solution stops there. An open Solution may import eligible `_repo` modules.

Workflow resolution follows this order:

1. Resolve a matching workflow owned by the caller's install.
2. If shared fallback is disabled, stop.
3. Resolve an eligible registered loose workflow in the install org, then global.

Use portable `path::function` refs. A loose source file without a workflow registration cannot resolve. A caller cannot resolve a sibling Solution workflow, including by UUID.

When an open Solution invokes a loose workflow, it executes as that loose row (`solution_id` remains absent). Its imports and SDK calls use loose org/global context, not borrowed Solution ownership. Treat this as a trust boundary and permission the loose workflow explicitly.

## Tables

The install's own table name wins. With shared fallback enabled, a miss may resolve a loose install-org/global table by name. External table UUIDs stay hidden from Solution lookup.

Shared fallback tables are read-only from the Solution. Policies filter/deny rows after the table resolves. Python query behavior can translate some missing-table reads into an empty `DocumentList`; verify setup explicitly when absence matters.

## Managed files

The location must be declared in `.bifrost/files.yaml`; `workspace` is not a Solution runtime location.

With fallback enabled, read/list/exists and supported signed-read operations may search the Solution tier, then eligible install-org/global tiers. File policies apply at each tier. Writes and deletes always target Solution-owned storage and do not modify a shared fallback file.

Read `files.md` for the application/file-operation contract.

## Configs and integrations

Solution manifests declare config/integration requirements, not isolated values or credentials.

`config.get()` uses the instance's org/global config cascade. `config.set()` and `config.delete()` can mutate that shared namespace when authorized. An install setup check may require an org-specific value even when runtime lookup finds a global fallback; inspect both tiers when status and runtime disagree.

`integrations.get()` resolves the caller's org mapping and integration/global defaults. Keep it server-side. Mapping changes, OAuth flow, and token/config replacement change shared environment state.

## Knowledge

Knowledge is an org/global namespace with its own access rules. Installing a Solution does not create a private knowledge tier and `global_repo_access` does not seal or open it.

## Local preview

`bifrost solution start` runs local app/workflow source while proxying resource access to the selected live instance:

- local workflow refs execute transiently first;
- a missing local ref can fall back upstream only when shared fallback allows it;
- table/file/config/integration/knowledge reads and writes are real;
- policies and org/role boundaries remain active.

Validate boundary-sensitive behavior against both local preview and a deployed install. They should agree about fallback eligibility even though local workflow execution is transient and deployed execution is durable.

## Portability and security checklist

- Prefer install-owned resources when the Solution should work elsewhere without manual dependencies.
- Document every required loose module, registered workflow, shared table/file, config key, integration mapping, and knowledge namespace.
- Keep secrets behind workflows.
- Test an allowed and denied viewer, not only a platform admin.
- Verify shared tables are not mutated and shared files are not overwritten from Solution context.
- Report shared environment writes and reduced self-containment before deploy.
