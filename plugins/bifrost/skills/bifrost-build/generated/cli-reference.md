# CLI Reference (generated — do not edit)

> Regenerate: `python api/scripts/skill-truth/generate.py`. CI enforces freshness.

## `agents`

```
Usage: agents [OPTIONS] COMMAND [ARGS]...

  Manage agents.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create  Create a new agent.
  delete  Soft-delete an agent.
  get     Get a single agent by UUID or name.
  list    List all agents.
  update  Update an agent.
```

### `agents create`

```
Usage: agents create [OPTIONS]

  Create a new agent.

  ``--system-prompt @file.md`` loads the prompt from disk. ``--tool-ids`` and
  ``--delegated-agent-ids`` resolve each entry via the ref resolver before the
  body is sent.

  Org targeting follows the unified ``--org`` standard: HOME (omit) scopes the
  agent to the caller's org, ``--global`` makes it global, ``--org <id|name>``
  scopes it to that org. (Non-admins may only create private agents in their
  own org regardless.)

Options:
  --name TEXT                     name  [required]
  --description TEXT              description
  --system-prompt TEXT            system_prompt  [required]
  --channels TEXT                 channels (repeat for multiple).
  --access-level [authenticated|everyone|role_based|private]
                                  access_level
  --tool-ids TEXT                 tool_ids (repeat for multiple; comma-split
                                  also accepted).
  --delegated-agent-ids TEXT      delegated_agent_ids (repeat for multiple;
                                  comma-split also accepted).
  --role-ids TEXT                 role_ids (repeat for multiple; comma-split
                                  also accepted).
  --knowledge-sources TEXT        knowledge_sources (repeat for multiple).
  --system-tools TEXT             system_tools (repeat for multiple).
  --mcp-connection-ids TEXT       mcp_connection_ids (repeat for multiple;
                                  comma-split also accepted).
  --llm-model TEXT                llm_model
  --llm-max-tokens INTEGER        llm_max_tokens
  --max-iterations INTEGER        max_iterations
  --max-token-budget INTEGER      max_token_budget
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `agents delete`

```
Usage: agents delete [OPTIONS] REF

  Soft-delete an agent.

  ``REF`` is a UUID or agent name. The server returns ``204 No Content`` on
  success; the CLI reports the resolved UUID.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `agents get`

```
Usage: agents get [OPTIONS] REF

  Get a single agent by UUID or name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `agents list`

```
Usage: agents list [OPTIONS]

  List all agents.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `agents update`

```
Usage: agents update [OPTIONS] REF

  Update an agent.

  ``REF`` is a UUID or agent name. Names are resolved via
  :class:`RefResolver`; ambiguous names fail loudly with the candidate list.
  The verb is **PUT** per the cli-mutation-surface audit correction.

  Passing ``--org``/``--global`` re-scopes the agent (HOME leaves the scope
  unchanged, since omitting org sends no ``organization_id``).

Options:
  --name TEXT                     name
  --description TEXT              description
  --system-prompt TEXT            system_prompt
  --channels TEXT                 channels (repeat for multiple).
  --access-level [authenticated|everyone|role_based|private]
                                  access_level
  --is-active / --no-is-active    is_active (tri-state; omit to leave
                                  unchanged).
  --tool-ids TEXT                 tool_ids (repeat for multiple; comma-split
                                  also accepted).
  --delegated-agent-ids TEXT      delegated_agent_ids (repeat for multiple;
                                  comma-split also accepted).
  --role-ids TEXT                 role_ids (repeat for multiple; comma-split
                                  also accepted).
  --knowledge-sources TEXT        knowledge_sources (repeat for multiple).
  --system-tools TEXT             system_tools (repeat for multiple).
  --mcp-connection-ids TEXT       mcp_connection_ids (repeat for multiple;
                                  comma-split also accepted).
  --clear-roles / --no-clear-roles
                                  clear_roles (tri-state; omit to leave
                                  unchanged).
  --llm-model TEXT                llm_model
  --llm-max-tokens INTEGER        llm_max_tokens
  --max-iterations INTEGER        max_iterations
  --max-token-budget INTEGER      max_token_budget
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

## `apps`

```
Usage: apps [OPTIONS] COMMAND [ARGS]...

  Manage applications.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create    Create a new application, optionally seeding npm dependencies.
  delete    Delete an application.
  get       Get a single application by slug, UUID, or name.
  list      List all applications (wrapped ``{applications, total}``...
  replace   Repoint an application's source directory.
  set-deps  Replace an application's npm dependencies.
  update    Update application metadata (patch-without-draft).
```

### `apps create`

```
Usage: apps create [OPTIONS]

  Create a new application, optionally seeding npm dependencies.

  ``--organization`` accepts a UUID or org name. ``--role-ids`` accepts
  repeated values or a comma-separated list; entries may be role names or
  UUIDs.

  When ``--deps`` is passed this runs as a two-call orchestration: the app is
  created first, then a ``PUT /dependencies`` applies the parsed dependency
  dict. If the deps call fails after the create succeeded, the command prints
  both the created app and the deps error, exits non-zero, and leaves the app
  in place — there is no rollback.

Options:
  --name TEXT          name  [required]
  --description TEXT   description
  --slug TEXT          slug  [required]
  --access-level TEXT  access_level
  --app-model TEXT     app_model
  --role-ids TEXT      role_ids (repeat for multiple; comma-split also
                       accepted).
  --organization TEXT  org ref (UUID or name) for organization_id.
  --deps TEXT          Dependencies as a JSON literal or @path to a
                       package.json / {name: version} file. Triggers a follow-
                       up PUT to /dependencies after the app is created.
  --json               Emit JSON instead of human-readable output.
  --help               Show this message and exit.
```

### `apps delete`

```
Usage: apps delete [OPTIONS] REF

  Delete an application.

  ``REF`` is a slug, UUID, or application name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `apps get`

```
Usage: apps get [OPTIONS] REF

  Get a single application by slug, UUID, or name.

  The public per-record endpoint is keyed by slug. For slug refs we hit ``GET
  /api/applications/{slug}`` directly. For UUID / name refs we resolve to a
  UUID then locate the matching record from the list payload so this command
  works with any ref shape :class:`RefResolver` accepts.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `apps list`

```
Usage: apps list [OPTIONS]

  List all applications (wrapped ``{applications, total}`` payload).

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `apps replace`

```
Usage: apps replace [OPTIONS] REF

  Repoint an application's source directory.

  ``REF`` is a slug, UUID, or application name. ``--repo-path`` must be the
  workspace-relative path to the new source directory. By default the path
  must already contain files; ``--force`` bypasses that check (and uniqueness
  / nesting checks) for repointing ahead of a push.

Options:
  --repo-path TEXT  Workspace-relative path to the new source directory (e.g.
                    apps/my-app-v2).  [required]
  --force           Bypass the uniqueness, nesting, and source-exists checks.
                    Use when repointing before files are pushed.
  --json            Emit JSON instead of human-readable output.
  --help            Show this message and exit.
```

### `apps set-deps`

```
Usage: apps set-deps [OPTIONS] REF

  Replace an application's npm dependencies.

  ``REF`` is a slug, UUID, or application name. The ``--deps`` value is either
  a JSON object literal or ``@path/to/package.json``; package.json's
  ``dependencies`` key is extracted automatically.

Options:
  --deps TEXT  Dependencies as a JSON literal or @path to a package.json /
               {name: version} file.  [required]
  --json       Emit JSON instead of human-readable output.
  --help       Show this message and exit.
```

### `apps update`

```
Usage: apps update [OPTIONS] REF

  Update application metadata (patch-without-draft).

  ``REF`` is a slug, UUID, or application name. Unset flags are omitted from
  the payload so the server only applies the fields the user explicitly
  passed. Per the audit this is PATCH directly on the live application —
  there's no draft-staging step.

  When ``--deps`` is passed this runs the same two-call orchestration as
  ``apps create --deps``: the metadata PATCH first, then a ``PUT
  /dependencies`` applies the parsed dict. If the deps call fails after the
  patch succeeded, the command prints both outcomes, exits non-zero, and
  leaves the metadata change in place — there is no rollback.

Options:
  --name TEXT          name
  --slug TEXT          slug
  --description TEXT   description
  --scope TEXT         scope
  --access-level TEXT  access_level
  --role-ids TEXT      role_ids (repeat for multiple; comma-split also
                       accepted).
  --deps TEXT          Dependencies as a JSON literal or @path to a
                       package.json / {name: version} file. Triggers a follow-
                       up PUT to /dependencies after the metadata patch.
                       Mirrors `apps create --deps`.
  --json               Emit JSON instead of human-readable output.
  --help               Show this message and exit.
```

## `claims`

```
Usage: claims [OPTIONS] COMMAND [ARGS]...

  Manage custom claims.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create  Create a custom claim.
  delete  Delete a custom claim by name.
  get     Get a custom claim by name.
  list    List custom claims (superusers see all orgs by default).
  update  Update a custom claim by name.
```

### `claims create`

```
Usage: claims create [OPTIONS]

  Create a custom claim.

Options:
  --name TEXT                     name  [required]
  --description TEXT              description
  --type TEXT                     type
  --query TEXT                    query as JSON literal or @path to a
                                  YAML/JSON file.  [required]
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `claims delete`

```
Usage: claims delete [OPTIONS] NAME

  Delete a custom claim by name.

Options:
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `claims get`

```
Usage: claims get [OPTIONS] NAME

  Get a custom claim by name.

Options:
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `claims list`

```
Usage: claims list [OPTIONS]

  List custom claims (superusers see all orgs by default).

Options:
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `claims update`

```
Usage: claims update [OPTIONS] NAME

  Update a custom claim by name.

Options:
  --description TEXT              description
  --type TEXT                     type
  --query TEXT                    query as JSON literal or @path to a
                                  YAML/JSON file.
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

## `configs`

```
Usage: configs [OPTIONS] COMMAND [ARGS]...

  Manage configuration values.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create  Create a new configuration value.
  delete  Delete a configuration value by UUID or key.
  get     Get a single configuration value by UUID or key.
  list    List all configuration values.
  set     Upsert a configuration value (catalog open question #2: yes).
  update  Update a configuration value.
```

### `configs create`

```
Usage: configs create [OPTIONS]

  Create a new configuration value.

Options:
  --key TEXT                      key  [required]
  --value TEXT                    value as JSON literal or @path to a
                                  YAML/JSON file.  [required]
  --config-type [string|int|bool|json|secret]
                                  config_type
  --description TEXT              description
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `configs delete`

```
Usage: configs delete [OPTIONS] REF

  Delete a configuration value by UUID or key.

  Secret-type configs require ``--confirm`` so a typo'd ``bifrost configs
  delete`` doesn't silently wipe an encrypted value that cannot be recovered
  from the server.

Options:
  --confirm  Required when deleting a secret-type config (safety guard).
  --json     Emit JSON instead of human-readable output.
  --help     Show this message and exit.
```

### `configs get`

```
Usage: configs get [OPTIONS] REF

  Get a single configuration value by UUID or key.

  The server does not expose a per-record GET endpoint for configs, so this
  resolves the ref via :class:`RefResolver` and locates the entry in the ``GET
  /api/config`` list payload.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `configs list`

```
Usage: configs list [OPTIONS]

  List all configuration values.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `configs set`

```
Usage: configs set [OPTIONS] KEY

  Upsert a configuration value (catalog open question #2: yes).

  Looks up an existing config matching ``(key, organization_id)`` by listing
  ``/api/config`` client-side — the endpoint does not accept a ``key`` query
  parameter, so filtering happens in the CLI. PUTs the existing row if found;
  POSTs otherwise. The result is idempotent from the caller's perspective.

  Org targeting follows the unified ``--org`` standard: HOME (omit) targets
  the caller's org, ``--global`` targets global, ``--org <id|name>`` targets
  that org. The scope filter only narrows the upsert to an explicit scope when
  one was given (GLOBAL or ORG); HOME falls back to key-only matching.

Options:
  --value TEXT                    Config value (plain string).  [required]
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --type [string|int|bool|json|secret]
                                  Config type (enum). On create, defaults to
                                  'string'. On update, omit to preserve the
                                  existing type.
  --description TEXT              Optional description of this config entry.
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `configs update`

```
Usage: configs update [OPTIONS] REF

  Update a configuration value.

  ``REF`` is a UUID or config key. Names are resolved via
  :class:`RefResolver`; ambiguous keys fail loudly with the candidate list.

  Omitting ``--value`` preserves the stored value (server-side omit-unset
  behaviour — particularly important for ``secret``-type configs, where the
  plaintext value is never returned and cannot be round-tripped).

Options:
  --value TEXT                    value as JSON literal or @path to a
                                  YAML/JSON file.
  --config-type [string|int|bool|json|secret]
                                  config_type
  --description TEXT              description
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

## `events`

```
Usage: events [OPTIONS] COMMAND [ARGS]...

  Manage event sources and subscriptions.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create-source        Create a new event source.
  get-source           Get a single event source by UUID or name.
  get-subscription     Get a single subscription by source ref +...
  list-sources         List all event sources (wrapped ``{items, total}``...
  list-subscriptions   List subscriptions for an event source.
  subscribe            Subscribe a workflow or agent to an event source.
  update-source        Update an event source.
  update-subscription  Update an event subscription.
```

### `events create-source`

```
Usage: events create-source [OPTIONS]

  Create a new event source.

  Flat-to-nested flags: ``--cron`` / ``--timezone`` / ``--schedule-enabled``
  collapse into the schedule config; ``--adapter`` / ``--webhook-integration``
  / ``--webhook-config`` collapse into the webhook config. At least one of
  each group is required when ``--source-type`` is ``schedule`` or
  ``webhook``, respectively — the API validates the shape.

  Org targeting follows the unified ``--org`` standard: HOME (omit) scopes the
  source to the caller's org, ``--global`` makes it global, ``--org
  <id|name>`` scopes it to that org.

Options:
  --name TEXT                     name  [required]
  --source-type [webhook|schedule|topic]
                                  source_type  [required]
  --event-type TEXT               event_type
  --cron TEXT                     Cron expression, e.g. '*/5 * * * *'
                                  (collapses into schedule config).
  --timezone TEXT                 Schedule timezone, e.g. 'UTC' (collapses
                                  into schedule config).
  --schedule-enabled / --no-schedule-enabled
                                  Whether the schedule is enabled (collapses
                                  into schedule config).
  --adapter TEXT                  Webhook adapter name (collapses into webhook
                                  config).
  --webhook-integration TEXT      Integration ref (UUID or name) for OAuth-
                                  based adapters (collapses into webhook
                                  config).
  --webhook-config TEXT           Webhook adapter config as JSON literal or
                                  @path/to/file.yaml (collapses into webhook
                                  config).
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `events get-source`

```
Usage: events get-source [OPTIONS] REF

  Get a single event source by UUID or name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `events get-subscription`

```
Usage: events get-subscription [OPTIONS] SOURCE_REF SUBSCRIPTION_ID

  Get a single subscription by source ref + subscription UUID.

  The server has no per-subscription GET endpoint, so this lists the source's
  subscriptions and filters client-side.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `events list-sources`

```
Usage: events list-sources [OPTIONS]

  List all event sources (wrapped ``{items, total}`` payload).

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `events list-subscriptions`

```
Usage: events list-subscriptions [OPTIONS] SOURCE_REF

  List subscriptions for an event source.

  ``SOURCE_REF`` is a UUID or event source name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `events subscribe`

```
Usage: events subscribe [OPTIONS] SOURCE_REF

  Subscribe a workflow or agent to an event source.

  ``SOURCE_REF`` is a UUID or event source name. Supply exactly one of
  ``--workflow`` or ``--agent`` (portable refs). ``target_type`` is inferred
  from which flag was used and overrides any ``--target-type`` the DTO
  generator may surface.

Options:
  --target-type TEXT        target_type
  --workflow TEXT           workflow ref (UUID or name) for workflow_id.
  --agent TEXT              agent ref (UUID or name) for agent_id.
  --event-type TEXT         event_type
  --filter-expression TEXT  filter_expression
  --input-mapping TEXT      input_mapping as JSON literal or @path to a
                            YAML/JSON file.
  --json                    Emit JSON instead of human-readable output.
  --help                    Show this message and exit.
```

### `events update-source`

```
Usage: events update-source [OPTIONS] REF

  Update an event source.

  ``REF`` is a UUID or event source name. Flat-to-nested flags behave the same
  as on ``create-source`` — if any flat schedule / webhook flag is supplied,
  the corresponding nested object is rebuilt and patched.

  Passing ``--org``/``--global`` re-scopes the source (HOME leaves the scope
  unchanged, since omitting org sends no ``organization_id``).

Options:
  --name TEXT                     name
  --is-active / --no-is-active    is_active (tri-state; omit to leave
                                  unchanged).
  --cron TEXT                     Cron expression, e.g. '*/5 * * * *'
                                  (collapses into schedule config).
  --timezone TEXT                 Schedule timezone, e.g. 'UTC' (collapses
                                  into schedule config).
  --schedule-enabled / --no-schedule-enabled
                                  Whether the schedule is enabled (collapses
                                  into schedule config).
  --adapter TEXT                  Webhook adapter name (collapses into webhook
                                  config).
  --webhook-integration TEXT      Integration ref (UUID or name) for OAuth-
                                  based adapters (collapses into webhook
                                  config).
  --webhook-config TEXT           Webhook adapter config as JSON literal or
                                  @path/to/file.yaml (collapses into webhook
                                  config).
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `events update-subscription`

```
Usage: events update-subscription [OPTIONS] SOURCE_REF SUBSCRIPTION_ID

  Update an event subscription.

  ``SOURCE_REF`` is a UUID or event source name; ``SUBSCRIPTION_ID`` is the
  subscription's UUID. Only filter / delivery fields are mutable —
  ``--workflow`` / ``--agent`` / ``--target-type`` are surfaced only so we can
  refuse the attempt with a clear error. Delete and recreate if you need to
  change the target.

Options:
  --event-type TEXT               event_type
  --filter-expression TEXT        filter_expression
  --is-active / --no-is-active    is_active (tri-state; omit to leave
                                  unchanged).
  --input-mapping TEXT            input_mapping as JSON literal or @path to a
                                  YAML/JSON file.
  --workflow TEXT                 Rejected: changing the target workflow
                                  requires delete + recreate.
  --agent TEXT                    Rejected: changing the target agent requires
                                  delete + recreate.
  --target-type [workflow|agent]  Rejected: changing the target type requires
                                  delete + recreate.
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

## `files`

```
Usage: files [OPTIONS] COMMAND [ARGS]...

  Read, write, list, search workspace files.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  delete    Delete a workspace file.
  exists    Check if a file exists.
  list      List files in a directory (default: location root).
  policies  Manage file access policies.
  read      Read a workspace file and write its contents to stdout.
  search    Search workspace file contents.
  write     Write to a workspace file.
```

### `files delete`

```
Usage: files delete [OPTIONS] PATH

  Delete a workspace file.

Options:
  --location TEXT  Storage location. Special: "workspace" (default), "temp",
                   "uploads". Custom names (e.g. "reports") are accepted;
                   "_repo", "_tmp", and "_apps" are blocked.
  --json           Emit JSON instead of human-readable output.
  --help           Show this message and exit.
```

### `files exists`

```
Usage: files exists [OPTIONS] PATH

  Check if a file exists. Exits 0 if yes, 1 if no (script-friendly).

Options:
  --location TEXT  Storage location. Special: "workspace" (default), "temp",
                   "uploads". Custom names (e.g. "reports") are accepted;
                   "_repo", "_tmp", and "_apps" are blocked.
  --json           Emit JSON instead of human-readable output.
  --help           Show this message and exit.
```

### `files list`

```
Usage: files list [OPTIONS] [DIRECTORY]

  List files in a directory (default: location root).

Options:
  --location TEXT  Storage location. Special: "workspace" (default), "temp",
                   "uploads". Custom names (e.g. "reports") are accepted;
                   "_repo", "_tmp", and "_apps" are blocked.
  --json           Emit JSON instead of human-readable output.
  --help           Show this message and exit.
```

### `files policies`

```
Usage: files policies [OPTIONS] COMMAND [ARGS]...

  Manage file access policies.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  delete  Delete the file policy for a path prefix.
  get     Get the file policy for a path prefix.
  list    List file policies for a location and optional org scope.
  set     Create or replace the file policy for a path prefix.
```

#### `files policies delete`

```
Usage: files policies delete [OPTIONS] PATH

  Delete the file policy for a path prefix.

Options:
  --location TEXT  Storage location. Special: "workspace" (default), "temp",
                   "uploads". Custom names (e.g. "reports") are accepted;
                   "_repo", "_tmp", and "_apps" are blocked.
  --scope TEXT     Organization UUID for org-scoped policies.
  --help           Show this message and exit.
```

#### `files policies get`

```
Usage: files policies get [OPTIONS] PATH

  Get the file policy for a path prefix.

Options:
  --location TEXT  Storage location. Special: "workspace" (default), "temp",
                   "uploads". Custom names (e.g. "reports") are accepted;
                   "_repo", "_tmp", and "_apps" are blocked.
  --scope TEXT     Organization UUID for org-scoped policies.
  --help           Show this message and exit.
```

#### `files policies list`

```
Usage: files policies list [OPTIONS]

  List file policies for a location and optional org scope.

Options:
  --location TEXT  Storage location. Special: "workspace" (default), "temp",
                   "uploads". Custom names (e.g. "reports") are accepted;
                   "_repo", "_tmp", and "_apps" are blocked.
  --scope TEXT     Organization UUID for org-scoped policies.
  --help           Show this message and exit.
```

#### `files policies set`

```
Usage: files policies set [OPTIONS] PATH

  Create or replace the file policy for a path prefix.

Options:
  --location TEXT  Storage location. Special: "workspace" (default), "temp",
                   "uploads". Custom names (e.g. "reports") are accepted;
                   "_repo", "_tmp", and "_apps" are blocked.
  --scope TEXT     Organization UUID for org-scoped policies.
  --file FILE      JSON/YAML policy document to store.  [required]
  --help           Show this message and exit.
```

### `files read`

```
Usage: files read [OPTIONS] PATH

  Read a workspace file and write its contents to stdout.

  Text files only. The SDK has `read_bytes` for binary; this CLI verb does
  not.

Options:
  --location TEXT  Storage location. Special: "workspace" (default), "temp",
                   "uploads". Custom names (e.g. "reports") are accepted;
                   "_repo", "_tmp", and "_apps" are blocked.
  --json           Emit JSON instead of human-readable output.
  --help           Show this message and exit.
```

### `files search`

```
Usage: files search [OPTIONS] QUERY

  Search workspace file contents.

Options:
  --regex                      Treat query as a regex.
  --case-sensitive
  --include TEXT               Glob restricting which files to search
                               (default: "**/*").
  --max-results INTEGER RANGE  Maximum results to return (default: 1000, max:
                               10000).  [1<=x<=10000]
  --json                       Emit JSON instead of human-readable output.
  --help                       Show this message and exit.
```

### `files write`

```
Usage: files write [OPTIONS] PATH [SOURCE]

  Write to a workspace file. Source: --content, --from-file, or `-` for stdin.

  Text files only. Pass --content "" to truncate an existing file.

Options:
  --content TEXT    Inline content to write.
  --from-file FILE  Read content from a local file.
  --location TEXT   Storage location. Special: "workspace" (default), "temp",
                    "uploads". Custom names (e.g. "reports") are accepted;
                    "_repo", "_tmp", and "_apps" are blocked.
  --json            Emit JSON instead of human-readable output.
  --help            Show this message and exit.
```

## `forms`

```
Usage: forms [OPTIONS] COMMAND [ARGS]...

  Manage forms.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create  Create a new form.
  delete  Delete (soft-delete) a form.
  get     Get a single form by UUID or name.
  list    List all forms.
  update  Update a form.
```

### `forms create`

```
Usage: forms create [OPTIONS]

  Create a new form.

  ``--workflow`` / ``--launch-workflow`` accept a UUID, name, or
  ``path::func`` ref. ``--form-schema`` accepts a JSON literal or
  ``@path/to/schema.yaml`` — the file is loaded and embedded as a dict.

  Org targeting follows the unified ``--org`` standard: HOME (omit) scopes the
  form to the caller's org, ``--global`` makes it global, ``--org <id|name>``
  scopes it to that org.

Options:
  --name TEXT                     name  [required]
  --description TEXT              description
  --workflow TEXT                 workflow ref (UUID or name) for workflow_id.
  --launch-workflow TEXT          workflow ref (UUID or name) for
                                  launch_workflow_id.
  --default-launch-params TEXT    default_launch_params as JSON literal or
                                  @path to a YAML/JSON file.
  --allowed-query-params TEXT     allowed_query_params (repeat for multiple).
  --form-schema TEXT              form_schema as JSON literal or @path to a
                                  YAML/JSON file.  [required]
  --access-level [authenticated|everyone|role_based]
                                  access_level
  --role-ids TEXT                 role_ids (repeat for multiple; comma-split
                                  also accepted).
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `forms delete`

```
Usage: forms delete [OPTIONS] REF

  Delete (soft-delete) a form.

  ``REF`` is a UUID or form name. Matches the API default (is_active=False);
  use the REST endpoint directly with ``?purge=true`` to hard-delete.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `forms get`

```
Usage: forms get [OPTIONS] REF

  Get a single form by UUID or name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `forms list`

```
Usage: forms list [OPTIONS]

  List all forms.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `forms update`

```
Usage: forms update [OPTIONS] REF

  Update a form.

  ``REF`` is a UUID or form name. Names are resolved via :class:`RefResolver`;
  ambiguous names fail loudly with the candidate list. Unset flags are omitted
  from the payload so only the supplied fields are patched.

  Passing ``--org``/``--global`` re-scopes the form (HOME leaves the scope
  unchanged, since omitting org sends no ``organization_id``).

Options:
  --name TEXT                     name
  --description TEXT              description
  --workflow TEXT                 workflow ref (UUID or name) for workflow_id.
  --launch-workflow TEXT          workflow ref (UUID or name) for
                                  launch_workflow_id.
  --default-launch-params TEXT    default_launch_params as JSON literal or
                                  @path to a YAML/JSON file.
  --allowed-query-params TEXT     allowed_query_params (repeat for multiple).
  --form-schema TEXT              form_schema as JSON literal or @path to a
                                  YAML/JSON file.
  --is-active / --no-is-active    is_active (tri-state; omit to leave
                                  unchanged).
  --access-level [authenticated|everyone|role_based]
                                  access_level
  --clear-roles / --no-clear-roles
                                  clear_roles (tri-state; omit to leave
                                  unchanged).
  --role-ids TEXT                 role_ids (repeat for multiple; comma-split
                                  also accepted).
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

## `integrations`

```
Usage: integrations [OPTIONS] COMMAND [ARGS]...

  Manage integrations.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  add-mapping     Create a mapping between an integration and an...
  create          Create a new integration.
  get             Get a single integration by UUID or name (with mappings...
  list            List all integrations (wrapped ``{items, total}``...
  update          Update an integration.
  update-mapping  Update an existing integration mapping.
```

### `integrations add-mapping`

```
Usage: integrations add-mapping [OPTIONS] INTEGRATION_REF

  Create a mapping between an integration and an organization.

  ``INTEGRATION_REF`` is a UUID or integration name. ``--organization`` is a
  UUID or org name (resolved via :class:`RefResolver`).

  ``--oauth-token-id`` is an opt-in flag outside the DTO-generated flag set —
  the DTO excludes ``oauth_token_id`` to avoid accidentally surfacing the UI-
  managed OAuth handshake data as a writable CLI field.

Options:
  --organization TEXT    org ref (UUID or name) for organization_id.
                         [required]
  --entity-id TEXT       entity_id  [required]
  --entity-name TEXT     entity_name
  --config TEXT          config as JSON literal or @path to a YAML/JSON file.
  --oauth-token-id TEXT  OAuth token UUID (opt-in; empty means leave unset).
  --json                 Emit JSON instead of human-readable output.
  --help                 Show this message and exit.
```

### `integrations create`

```
Usage: integrations create [OPTIONS]

  Create a new integration.

  ``--config-schema`` accepts a JSON literal or ``@path/to/schema.yaml``. The
  file's top level may be a list of schema items or a dict with a ``schema`` /
  ``config_schema`` / ``items`` list.

Options:
  --name TEXT               name  [required]
  --config-schema TEXT      config_schema (repeat for multiple).
  --entity-id TEXT          entity_id
  --entity-id-name TEXT     entity_id_name
  --default-entity-id TEXT  default_entity_id
  --json                    Emit JSON instead of human-readable output.
  --help                    Show this message and exit.
```

### `integrations get`

```
Usage: integrations get [OPTIONS] REF

  Get a single integration by UUID or name (with mappings + OAuth config).

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `integrations list`

```
Usage: integrations list [OPTIONS]

  List all integrations (wrapped ``{items, total}`` payload).

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `integrations update`

```
Usage: integrations update [OPTIONS] REF

  Update an integration.

  ``REF`` is a UUID or integration name. When ``--config-schema`` replaces the
  existing schema with one that drops keys, the command refuses unless
  ``--force-remove-keys`` is passed — removed keys cascade-delete related
  ``Config`` rows (integration-level defaults and per-org overrides).

Options:
  --force-remove-keys             Proceed even when the new --config-schema
                                  drops keys currently present on the
                                  integration (cascade-deletes related
                                  configs).
  --name TEXT                     name
  --list-entities-data-provider TEXT
                                  workflow ref (UUID or name) for
                                  list_entities_data_provider_id.
  --config-schema TEXT            config_schema (repeat for multiple).
  --entity-id TEXT                entity_id
  --entity-id-name TEXT           entity_id_name
  --default-entity-id TEXT        default_entity_id
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `integrations update-mapping`

```
Usage: integrations update-mapping [OPTIONS] INTEGRATION_REF

  Update an existing integration mapping.

  Resolves ``INTEGRATION_REF`` + ``--organization`` to the mapping UUID via
  ``GET /api/integrations/{id}/mappings/by-org/{org_id}``, then PUTs the
  update body. ``oauth_token_id`` is only sent when ``--oauth-token-id`` is
  explicitly passed — this preserves the server's existing token on unrelated
  updates (it's set by the OAuth flow, not by CLI users).

Options:
  --organization TEXT    organization ref (UUID or name) — identifies the
                         mapping to update.  [required]
  --entity-id TEXT       entity_id
  --entity-name TEXT     entity_name
  --config TEXT          config as JSON literal or @path to a YAML/JSON file.
  --oauth-token-id TEXT  OAuth token UUID (opt-in; omitted means leave
                         unchanged).
  --json                 Emit JSON instead of human-readable output.
  --help                 Show this message and exit.
```

## `orgs`

```
Usage: orgs [OPTIONS] COMMAND [ARGS]...

  Manage organizations.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create  Create a new organization.
  delete  Delete (soft-delete) an organization.
  get     Get a single organization by UUID or name.
  list    List all organizations.
  update  Update an organization.
```

### `orgs create`

```
Usage: orgs create [OPTIONS]

  Create a new organization.

Options:
  --name TEXT                   name  [required]
  --is-active / --no-is-active  is_active (tri-state; omit to leave
                                unchanged).
  --json                        Emit JSON instead of human-readable output.
  --help                        Show this message and exit.
```

### `orgs delete`

```
Usage: orgs delete [OPTIONS] REF

  Delete (soft-delete) an organization.

  ``REF`` is a UUID or organization name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `orgs get`

```
Usage: orgs get [OPTIONS] REF

  Get a single organization by UUID or name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `orgs list`

```
Usage: orgs list [OPTIONS]

  List all organizations.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `orgs update`

```
Usage: orgs update [OPTIONS] REF

  Update an organization.

  ``REF`` is a UUID or organization name. Names are resolved via
  :class:`RefResolver`; ambiguous names fail loudly with the candidate list.

Options:
  --name TEXT                   name
  --is-active / --no-is-active  is_active (tri-state; omit to leave
                                unchanged).
  --json                        Emit JSON instead of human-readable output.
  --help                        Show this message and exit.
```

## `policy-rule`

```
Usage: policy-rule [OPTIONS] COMMAND [ARGS]...

  Manage named, reusable policy rules.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create  Create a named policy rule.
  delete  Delete a named policy rule.
  get     Get a single policy rule by domain and name.
  list    List named policy rules.
  update  Update a named policy rule.
  usages  Show all file-policies and tables that reference a rule.
```

### `policy-rule create`

```
Usage: policy-rule create [OPTIONS]

  Create a named policy rule.

  ``--body`` accepts a JSON literal or ``@path/to/file.yaml``.

  Example:

  \b     bifrost policy-rule create --name read_all --domain file \
  --body '{"actions": ["read"], "when": null}'

  Org targeting follows the unified ``--org`` standard.

Options:
  --name TEXT                     name  [required]
  --domain TEXT                   domain  [required]
  --description TEXT              description
  --body TEXT                     body as JSON literal or @path to a YAML/JSON
                                  file.  [required]
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `policy-rule delete`

```
Usage: policy-rule delete [OPTIONS] {file|table} NAME

  Delete a named policy rule.

  ``DOMAIN`` is 'file' or 'table'. ``NAME`` is the rule's name. Fails with 409
  if the rule is read-only (built-in) or in use.

Options:
  --scope TEXT  Organization UUID for org-scoped rules.
  --json        Emit JSON instead of human-readable output.
  --help        Show this message and exit.
```

### `policy-rule get`

```
Usage: policy-rule get [OPTIONS] {file|table} NAME

  Get a single policy rule by domain and name.

  Uses the usages endpoint (which 404s when not found) to confirm the rule
  exists, then fetches the full record from the list.

Options:
  --scope TEXT  Organization UUID for org-scoped rules.
  --json        Emit JSON instead of human-readable output.
  --help        Show this message and exit.
```

### `policy-rule list`

```
Usage: policy-rule list [OPTIONS]

  List named policy rules.

Options:
  --domain [file|table]  Filter by domain ('file' or 'table').
  --scope TEXT           Organization UUID to filter by scope.
  --json                 Emit JSON instead of human-readable output.
  --help                 Show this message and exit.
```

### `policy-rule update`

```
Usage: policy-rule update [OPTIONS] {file|table} NAME

  Update a named policy rule.

  ``DOMAIN`` is 'file' or 'table'. ``NAME`` is the rule's name. Unset flags
  are omitted; the server preserves existing values.

Options:
  --name TEXT         name
  --description TEXT  description
  --body TEXT         body as JSON literal or @path to a YAML/JSON file.
  --scope TEXT        Organization UUID for org-scoped rules.
  --json              Emit JSON instead of human-readable output.
  --help              Show this message and exit.
```

### `policy-rule usages`

```
Usage: policy-rule usages [OPTIONS] {file|table} NAME

  Show all file-policies and tables that reference a rule.

Options:
  --scope TEXT  Organization UUID for org-scoped rules.
  --json        Emit JSON instead of human-readable output.
  --help        Show this message and exit.
```

## `requirements`

```
Usage: requirements [OPTIONS] COMMAND [ARGS]...

  Manage the workspace's Python requirements.txt (workers auto-recycle).

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  install  Install a package or recycle workers from current...
  list     List installed Python packages on the platform.
  remove   Remove a package from requirements.txt and recycle workers.
```

### `requirements install`

```
Usage: requirements install [OPTIONS] [SPEC]

  Install a package or recycle workers from current requirements.txt.

  Examples:

    bifrost requirements install                  # warm cache + recycle
    workers   bifrost requirements install reportlab        # append, then
    recycle   bifrost requirements install httpx==0.27.0    # pin version,
    then recycle

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `requirements list`

```
Usage: requirements list [OPTIONS]

  List installed Python packages on the platform.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `requirements remove`

```
Usage: requirements remove [OPTIONS] PACKAGE_NAME

  Remove a package from requirements.txt and recycle workers.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

## `roles`

```
Usage: roles [OPTIONS] COMMAND [ARGS]...

  Manage roles.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create  Create a new role.
  delete  Delete a role.
  get     Get a single role by UUID or name.
  list    List all roles.
  update  Update a role.
```

### `roles create`

```
Usage: roles create [OPTIONS]

  Create a new role.

Options:
  --name TEXT         name  [required]
  --description TEXT  description
  --permissions TEXT  permissions as JSON literal or @path to a YAML/JSON
                      file.
  --json              Emit JSON instead of human-readable output.
  --help              Show this message and exit.
```

### `roles delete`

```
Usage: roles delete [OPTIONS] REF

  Delete a role.

  ``REF`` is a UUID or role name. CASCADE removes all role assignments.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `roles get`

```
Usage: roles get [OPTIONS] REF

  Get a single role by UUID or name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `roles list`

```
Usage: roles list [OPTIONS]

  List all roles.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `roles update`

```
Usage: roles update [OPTIONS] REF

  Update a role.

  ``REF`` is a UUID or role name. Names are resolved via :class:`RefResolver`;
  ambiguous names fail loudly with the candidate list.

Options:
  --name TEXT         name
  --description TEXT  description
  --permissions TEXT  permissions as JSON literal or @path to a YAML/JSON
                      file.
  --json              Emit JSON instead of human-readable output.
  --help              Show this message and exit.
```

## `solution`

```
Usage: solution [OPTIONS] COMMAND [ARGS]...

  Manage Solution installs (installable surfaces).

Options:
  --help  Show this message and exit.

Commands:
  capture       Adopt loose _repo/ entities into an install (migration).
  deploy        Deploy the current Solution workspace (full replace,...
  export        Download a Solution's workspace zip (shareable or full...
  init          Scaffold a bifrost.solution.yaml descriptor.
  install       Install a Solution from a workspace zip (drag-and-drop...
  migrate-app   Migrate a v1 inline app dir to a scaffolded standalone_v2...
  pull          Pull captured entities into the local .bifrost/ manifest...
  scaffold-app  Scaffold a standalone_v2 React app (package.json, vite,...
  start         Run the app's dev server + local workflows (one origin).
  swap-slugs    Atomically exchange two apps' slugs (v1→v2 migration...
```

### `solution capture`

```
Usage: solution capture [OPTIONS] SOLUTION_ID

  Adopt loose _repo/ entities into an install (migration). --dry-run previews
  the dependency closure + outside references first.

Options:
  --workflow TEXT                 Workflow name or id (repeatable).
  --table TEXT                    Table name or id (repeatable).
  --app TEXT                      App name or id (repeatable).
  --form TEXT                     Form name or id (repeatable).
  --agent TEXT                    Agent name or id (repeatable).
  --claim TEXT                    Custom-claim name or id (repeatable).
  --config TEXT                   Config key (repeatable).
  --include-imports / --no-include-imports
                                  Also bundle the transitive modules/ import
                                  closure of captured workflows.  [default:
                                  no-include-imports]
  --dry-run                       Preview the dependency closure + outside
                                  references; capture nothing.
  --help                          Show this message and exit.
```

### `solution deploy`

```
Usage: solution deploy [OPTIONS] [PATH]

  Deploy the current Solution workspace (full replace, non-interactive).

Options:
  --solution TEXT                 Target install id (override when ambiguous).
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --force                         Apply even if the bundle version is older
                                  than the installed version (downgrade).
  --help                          Show this message and exit.
```

### `solution export`

```
Usage: solution export [OPTIONS] SOLUTION_REF

  Download a Solution's workspace zip (shareable or full backup).

Options:
  --mode [shareable|full]  shareable (code+schema, no password) or full
                           (+secrets+data, password required).  [default:
                           shareable]
  --password TEXT          Required for --mode full; encrypts the secrets
                           blob.
  --out TEXT               Output zip path (default: <slug>-<version>.zip in
                           the current directory).
  --help                   Show this message and exit.
```

### `solution init`

```
Usage: solution init [OPTIONS] [PATH]

  Scaffold a bifrost.solution.yaml descriptor.

Options:
  --slug TEXT                     Solution slug (definition identity).
                                  [required]
  --name TEXT                     Display name (defaults to slug).
  --version TEXT                  Bundle version recorded on the install at
                                  deploy time.  [default: 0.1.0]
  --global-repo-access / --no-global-repo-access
                                  [default: no-global-repo-access]
  --help                          Show this message and exit.
```

### `solution install`

```
Usage: solution install [OPTIONS] ZIP_PATH

  Install a Solution from a workspace zip (drag-and-drop equivalent).

Options:
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --set TEXT                      Config value KEY=VALUE (repeatable). Applied
                                  atomically with the deploy.
  --password TEXT                 Decryption password for a full-backup zip
                                  (required when the zip carries secrets).
  --replace-secrets               Overwrite existing config values when the
                                  zip carries conflicting secret values.
  --replace-data                  Overwrite existing table data when the zip
                                  carries conflicting rows.
  --help                          Show this message and exit.
```

### `solution migrate-app`

```
Usage: solution migrate-app [OPTIONS] SOURCE V2_SLUG

  Migrate a v1 inline app dir to a scaffolded standalone_v2 app: scaffold +
  port source + rewrite imports + install shadcn. STOPS before build/wire and
  prints a checklist of the judgment steps left to you.

Options:
  --title TEXT    App display title (default: the v2 slug).
  --api-url TEXT  Instance URL the app resolves `bifrost` from.
  --help          Show this message and exit.
```

### `solution pull`

```
Usage: solution pull [OPTIONS] [PATH]

  Pull captured entities into the local .bifrost/ manifest (does not touch
  source code).

Options:
  --solution TEXT                 Target install id (override when ambiguous).
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --help                          Show this message and exit.
```

### `solution scaffold-app`

```
Usage: solution scaffold-app [OPTIONS] SLUG

  Scaffold a standalone_v2 React app (package.json, vite, main.tsx, App.tsx).

Options:
  --path TEXT     App dir inside the solution workspace (default: apps/<slug>
                  under the solution root).
  --api-url TEXT  Instance URL the app resolves `bifrost` from (default:
                  $BIFROST_API_URL).
  --help          Show this message and exit.
```

### `solution start`

```
Usage: solution start [OPTIONS] [APP_SLUG]

  Run the app's dev server + local workflows (one origin).

Options:
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --port INTEGER                  Local origin port.  [default: 3000]
  --help                          Show this message and exit.
```

### `solution swap-slugs`

```
Usage: solution swap-slugs [OPTIONS] APP_A APP_B

  Atomically exchange two apps' slugs (v1→v2 migration cutover).

Options:
  --help  Show this message and exit.
```

## `tables`

```
Usage: tables [OPTIONS] COMMAND [ARGS]...

  Manage tables.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  create    Create a new table.
  delete    Delete a table and all its documents.
  get       Get a single table by UUID or name.
  list      List all tables (wrapped ``{tables, total}`` payload from the...
  policies  Manage table access policies.
  update    Update a table.
```

### `tables create`

```
Usage: tables create [OPTIONS]

  Create a new table.

  ``--schema`` accepts a JSON literal or ``@path/to/schema.yaml`` — the file
  is loaded and embedded as the table schema dict. ``--policies`` accepts the
  same shape and embeds row-level access policies; see
  ``docs/superpowers/specs/2026-04-30-table-policies-design.md``.

  Org targeting follows the unified ``--org`` standard: HOME (omit) scopes the
  table to the caller's org, ``--global`` makes it global, ``--org <id|name>``
  scopes it to that org.

Options:
  --name TEXT                     name  [required]
  --description TEXT              description
  --schema TEXT                   schema as JSON literal or @path to a
                                  YAML/JSON file.
  --policies TEXT                 policies as JSON literal or @path to a
                                  YAML/JSON file.
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `tables delete`

```
Usage: tables delete [OPTIONS] REF

  Delete a table and all its documents.

  ``REF`` is a UUID or table name. Cascade deletes the table's documents at
  the DB level — irreversible.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `tables get`

```
Usage: tables get [OPTIONS] REF

  Get a single table by UUID or name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `tables list`

```
Usage: tables list [OPTIONS]

  List all tables (wrapped ``{tables, total}`` payload from the API).

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `tables policies`

```
Usage: tables policies [OPTIONS] COMMAND [ARGS]...

  Manage table access policies.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  get  Get the access policies for a table.
  set  Set the access policies for a table.
```

#### `tables policies get`

```
Usage: tables policies get [OPTIONS] REF

  Get the access policies for a table.

  ``REF`` is a UUID or table name. Returns the ``policies`` field from the
  table record, which may contain inline policy objects and/or ``{"$ref":
  "rule-name"}`` references to named policy rules.

Options:
  --help  Show this message and exit.
```

#### `tables policies set`

```
Usage: tables policies set [OPTIONS] REF

  Set the access policies for a table.

  ``REF`` is a UUID or table name. ``--file`` must be a JSON or YAML document
  whose ``policies`` key (or top-level list) contains policy objects and/or
  ``{"$ref": "rule-name"}`` references to named policy rules. The document is
  round-tripped unchanged — ``$ref`` entries are stored and later resolved at
  query time.

Options:
  --file FILE  JSON/YAML policy document to store. May contain ``{"$ref":
               "rule-name"}`` entries.  [required]
  --help       Show this message and exit.
```

### `tables update`

```
Usage: tables update [OPTIONS] REF

  Update a table.

  ``REF`` is a UUID or table name. Names are resolved via
  :class:`RefResolver`; ambiguous names fail loudly with the candidate list.
  Unset flags are omitted from the payload so only the supplied fields are
  patched.

  If ``--name`` changes the table's current name, a prominent warning is
  printed to stderr — workflow SDK code that looks up tables by name will
  break on rename. No confirmation is required; the warning just nudges the
  caller to grep their workspace before committing.

Options:
  --name TEXT         name
  --description TEXT  description
  --schema TEXT       schema as JSON literal or @path to a YAML/JSON file.
  --policies TEXT     policies as JSON literal or @path to a YAML/JSON file.
  --json              Emit JSON instead of human-readable output.
  --help              Show this message and exit.
```

## `workflows`

```
Usage: workflows [OPTIONS] COMMAND [ARGS]...

  Manage workflows.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.

Commands:
  delete         Delete a workflow by removing its function from the...
  execute        Execute a registered workflow remotely and stream logs...
  get            Get a single workflow by UUID, name, or ``path::func`` ref.
  grant-role     Grant a role access to a workflow.
  list           List all workflows visible to the caller.
  list-orphaned  List all orphaned workflows (backing file deleted or...
  register       Register a decorated function from an existing workspace...
  remap          Move references from one workflow ID to another active...
  replace        Repoint an orphaned workflow to a new file location.
  revoke-role    Revoke a role's access from a workflow.
  update         Update a workflow's editable properties.
```

### `workflows delete`

```
Usage: workflows delete [OPTIONS] REF

  Delete a workflow by removing its function from the source file.

  ``REF`` is a UUID, workflow name, or ``path::func`` locator. Without
  ``--force``, the API performs a deactivation-protection pre-check and
  returns 409 if the workflow has dependents; pass ``--force`` to bypass.

Options:
  --force / --no-force  Skip the deactivation protection check and delete the
                        workflow even if it has dependent forms/apps/agents.
  --json                Emit JSON instead of human-readable output.
  --help                Show this message and exit.
```

### `workflows execute`

```
Usage: workflows execute [OPTIONS] REF

  Execute a registered workflow remotely and stream logs as it runs.

  ``REF`` is a workflow UUID, name, or ``path::func`` locator. The command:

  1. Resolves ``REF`` and posts to ``/api/workflows/execute`` (async — does
  not block the platform). 2. Connects to ``/ws/execution/{id}`` and prints
  log lines as they arrive. 3. Exits when the execution reaches a terminal
  status, after a final GET    to backfill any logs that emitted before the
  WebSocket connected and    to fetch the final result.

  Use ``bifrost run <file> --workflow <name>`` for local-file iteration —
  ``execute`` targets workflows already registered on the platform.

Options:
  --params TEXT       JSON object of input parameters (e.g. --params
                      '{"name":"World"}').
  --params-file FILE  Path to a JSON file with input parameters. Mutually
                      exclusive with --params.
  --org TEXT          Override execution org context (UUID or name). Requires
                      platform admin. Omit to use the caller's default org.
  --json              Emit JSON instead of human-readable output.
  --help              Show this message and exit.
```

### `workflows get`

```
Usage: workflows get [OPTIONS] REF

  Get a single workflow by UUID, name, or ``path::func`` ref.

  The server does not expose a per-record GET endpoint for workflows, so this
  resolves the ref via :class:`RefResolver` and locates the entry in the ``GET
  /api/workflows`` list payload.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `workflows grant-role`

```
Usage: workflows grant-role [OPTIONS] REF ROLE_REF

  Grant a role access to a workflow.

  ``REF`` is a workflow UUID / name / ``path::func``. ``ROLE_REF`` is a role
  UUID or role name. The underlying endpoint accepts a batch of role IDs —
  this command sends a single-element list for simplicity.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `workflows list`

```
Usage: workflows list [OPTIONS]

  List all workflows visible to the caller.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `workflows list-orphaned`

```
Usage: workflows list-orphaned [OPTIONS]

  List all orphaned workflows (backing file deleted or function removed).

  Orphaned workflows are workflows whose source file no longer exists or no
  longer contains the decorated function. They can be repointed with ``bifrost
  workflows replace``.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `workflows register`

```
Usage: workflows register [OPTIONS]

  Register a decorated function from an existing workspace ``.py`` file.

  The file must already exist in the workspace (written via ``bifrost push``
  or the file editor). This command indexes a ``@workflow`` / ``@tool`` /
  ``@data_provider`` function so it becomes executable via the API.

  Org targeting follows the unified ``--org`` standard: HOME (omit) scopes the
  workflow to the caller's org, ``--global`` makes it global, ``--org
  <id|name>`` scopes it to that org.

  ``--access-level`` and ``--role-ids`` set the workflow's access controls at
  registration time, mirroring the create-time surface for forms and apps.
  Role refs accept names or UUIDs and are resolved before the request.

Options:
  --path TEXT                     Workspace-relative path to the .py file
                                  containing the decorated function.
                                  [required]
  --function-name TEXT            Name of the decorated function to register.
                                  [required]
  --global                        Target global scope (org=NULL). Alias for
                                  --org global.
  --org, --organization, --scope TEXT
                                  Org UUID/name, or 'none'/'global' for global
                                  scope. Omit = your org. (--organization /
                                  --scope are synonyms.)
  --access-level [authenticated|everyone|role_based]
                                  Access level for the workflow. Omit to leave
                                  at default.
  --role-ids TEXT                 Role refs (UUID or name) for role_based
                                  access. Repeat the flag for multiple, or
                                  pass a comma-separated list.
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

### `workflows remap`

```
Usage: workflows remap [OPTIONS] SOURCE_REF

  Move references from one workflow ID to another active workflow ID.

Options:
  --to TEXT  Active workflow ref (UUID, name, or path::func) to receive
             references.  [required]
  --json     Emit JSON instead of human-readable output.
  --help     Show this message and exit.
```

### `workflows replace`

```
Usage: workflows replace [OPTIONS] REF

  Repoint an orphaned workflow to a new file location.

  ``REF`` is a UUID or workflow name (use ``bifrost workflows list-orphaned``
  to find orphaned UUIDs). The target file must exist in the workspace and
  contain a ``@workflow``, ``@tool``, or ``@data_provider`` decorated function
  with the given name. The workflow UUID is preserved so form/agent references
  remain intact.

Options:
  --path TEXT           Workspace-relative path to the .py file containing the
                        decorated function.  [required]
  --function-name TEXT  Name of the decorated function to point this workflow
                        at.  [required]
  --allow-type-change   Allow the decorator type to change (e.g. @workflow →
                        @data_provider). Off by default to prevent silently
                        breaking form bindings.
  --json                Emit JSON instead of human-readable output.
  --help                Show this message and exit.
```

### `workflows revoke-role`

```
Usage: workflows revoke-role [OPTIONS] REF ROLE_REF

  Revoke a role's access from a workflow.

  ``REF`` is a workflow UUID / name / ``path::func``. ``ROLE_REF`` is a role
  UUID or role name.

Options:
  --json  Emit JSON instead of human-readable output.
  --help  Show this message and exit.
```

### `workflows update`

```
Usage: workflows update [OPTIONS] REF

  Update a workflow's editable properties.

  ``REF`` is a UUID, workflow name, or ``path::func`` locator. See
  :mod:`bifrost.refs` for resolution rules.

Options:
  --organization-id TEXT          organization_id
  --access-level TEXT             access_level
  --clear-roles / --no-clear-roles
                                  clear_roles (tri-state; omit to leave
                                  unchanged).
  --role-ids TEXT                 role_ids (repeat for multiple; comma-split
                                  also accepted).
  --name TEXT                     name
  --description TEXT              description
  --category TEXT                 category
  --timeout-seconds INTEGER       timeout_seconds
  --tags TEXT                     tags (repeat for multiple).
  --endpoint-enabled / --no-endpoint-enabled
                                  endpoint_enabled (tri-state; omit to leave
                                  unchanged).
  --public-endpoint / --no-public-endpoint
                                  public_endpoint (tri-state; omit to leave
                                  unchanged).
  --json                          Emit JSON instead of human-readable output.
  --help                          Show this message and exit.
```

