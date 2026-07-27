# Solution Runtime Resource Access

Use this reference when Solution code needs anything outside its own install.
The `global_repo_access` name is historical and narrower than "all global
resources", but broader than "only the global `_repo`":

- It is **not** the install scope. Org install versus global install is chosen
  separately with `--org` / `--global`.
- It gates shared fallback for resource types that have a Solution-owned tier:
  `_repo` modules, loose workflows, tables, and files.
- For an org install, an allowed fallback searches the install org and then
  global. It is therefore not literally "global only".
- Config values, integrations/OAuth, and knowledge have no Solution-owned
  runtime value tier. They are shared instance resources and do not consult
  this flag.

The flag defaults to `false`.

## Runtime access matrix

| Resource | Standalone app / web SDK | Solution workflow | `global_repo_access: false` | `global_repo_access: true` |
|---|---|---|---|---|
| Python modules | Not applicable | Imports under the install first | No shared `_repo` import fallback | Shared `_repo` import fallback |
| Workflows | `useWorkflow` sends the app id | Nested/direct execute carries Solution scope | Own install only | Own install, then loose install-org/global workflow |
| Tables | `tables` / `useTable` send `X-Bifrost-App` | Python SDK appends Solution scope | Own Solution table only | Own table, then install-org, then global by **name** |
| Files | `files` / `useFiles` send `X-Bifrost-App` | Python SDK appends Solution scope | Own declared location only | Own, then install-org, then global reads in a declared location |
| Config values/secrets | No web-SDK surface | `config` SDK | Shared install-org/global cascade | Same; flag is not consulted |
| Integrations/OAuth | No web-SDK surface | `integrations` SDK | Shared org mapping, then integration/global defaults | Same; flag is not consulted |
| Knowledge | No web-SDK surface | `knowledge` SDK | Shared org/global namespace | Same; flag is not consulted |

Forms, agents, apps, event sources, and custom claims are installed entities,
not general shared-resource SDK lookups. Their `solution_id`, org scope,
`access_level`, roles, and resource-specific rules remain authoritative. Do not
infer access to them from `global_repo_access`.

## How Solution scope reaches the server

The app and workflow paths converge on the same request context:

- A deployed standalone app's `BifrostProvider` adds `X-Bifrost-App` to table,
  file, and workflow calls. Auth resolves that app to its active Solution.
- A Solution workflow's Python SDK carries its execution `solution_id` on
  table/file calls.
- Workflow execution derives scope from the authenticated request context,
  then the request's Solution/form/app identity for older SDK calls.
- A mismatched app header and `?solution=` signal is rejected; inactive installs
  are not executable.

This context selects the install. It does not bypass org scope, RBAC, row/file
policies, or external-user restrictions.

## Per-resource boundaries

### Modules and workflows

Module imports resolve the install's `_solutions/{id}/` source first. A sealed
Solution does not fall through to shared `_repo` modules.

Workflow refs follow the same ownership boundary:

1. Resolve the caller's own install by path, name, or UUID.
2. If shared fallback is disabled, stop.
3. If enabled, resolve a loose install-org/global workflow subject to normal
   workflow visibility and access rules.

A scoped caller never resolves a sibling Solution's workflow, including by
UUID. Prefer portable `path::function` refs in source; UUIDs are
environment-specific.

When an open Solution resolves a loose workflow, that workflow executes as the
loose row (`solution_id` is null), not as borrowed Solution-owned code. Its own
imports and SDK calls therefore use the ordinary org/global runtime. Treat
allowing loose-workflow fallback as a strong trust boundary, not a harmless
function alias.

### Tables

Own table names win. With shared fallback enabled, a miss searches loose
install-org then global tables by name. External table UUIDs stay hidden from
Solution context.

Shared fallback tables are read-only from the Solution. The flag does not allow
table creation or row mutation outside the install. Table policies still filter
or deny rows after the table resolves.

Denied/missing behavior differs slightly by SDK surface:

- The web transport receives the server's 404.
- Python `tables.query()` deliberately converts a 404 into an empty
  `DocumentList`; write/get operations keep their documented error/`None`
  behavior.

### Files

The location must be declared in `.bifrost/files.yaml`; `workspace` is never a
Solution runtime location. With shared fallback enabled, read/list/exists and
signed-read operations search own, install-org, then global tiers. File policies
apply independently at each tier.

Writes and deletes always target the Solution-owned tier. Enabling fallback does
not let an app or workflow modify loose org/global files.

### Config values and secrets

The manifest carries config **declarations** (key, type, required/default
metadata), not an isolated value store. Runtime `config.get()` reads the
instance's org/global config cascade regardless of `global_repo_access`.

`config.set()` and `config.delete()` mutate that shared instance namespace when
the caller's execution scope permits it. Treat those calls as changes to shared
environment state, not install-local state.

For an org install, setup status can require an org-specific value while runtime
lookup can still find a global fallback. If setup says "unset" but execution
works, inspect both tiers rather than assuming the SDK is unavailable.

### Integrations and OAuth

A Solution can declare that a connection is required, but the installed
environment supplies the Integration row, org mapping, OAuth provider/token,
and secret config. `integrations.get()` resolves the caller's org mapping first,
then integration/global defaults. This behavior is independent of
`global_repo_access`.

Integrations are intentionally server-side only in a standalone app: call them
from a Python workflow so OAuth tokens and decrypted secrets never enter browser
JavaScript. Mapping mutation APIs change shared instance state and require the
same care as config writes.

### Knowledge

Knowledge is also an org/global shared namespace with its own access rules. A
Solution declaration or install does not create a private knowledge tier, and
the flag does not seal knowledge access.

## Security and portability rules

- Shared fallback is an additional lookup tier, **not** an authorization
  bypass. Org boundaries, role checks, table/file policies, and external-user
  restrictions still apply.
- `global_repo_access: true` weakens self-containment. A package can depend on
  loose workflows, code, tables, or files that another environment does not
  have.
- Config values, integration mappings, OAuth tokens, knowledge content, table
  rows, and runtime files are environment state. Normal shareable packages
  carry declarations/schema, not those values.
- Config and integration names are shared keys. Two Solutions using the same
  key/name intentionally consume the same instance value.
- Keep secrets behind Python workflows. The web SDK exposes workflows, tables,
  and files; it does not expose config, integrations/OAuth, or knowledge.

## `bifrost solution start`

Local preview is connected to the selected live instance; it is not a data
sandbox.

- The generic `/api/*` proxy adds the bound Solution context and app/org auth
  headers. Table and file routes are handled by the normal upstream routers;
  the proxy does not implement a separate table namespace.
- Workflow execute is special: local functions resolve first and return a
  transient terminal response. If a ref is absent locally, a sealed Solution
  stops there; an open Solution may delegate the loose ref upstream.
- Remote table/file/config/integration writes are real instance changes.
- The access token is injected at runtime for preview/deployed mounts; it is not
  bundled into application source or build output.

Validate boundary-sensitive changes against both `solution start` and a
deployed install. They must agree on whether a loose workflow/resource fallback
is permitted even though local workflow executions are transient and deployed
executions are durable.
