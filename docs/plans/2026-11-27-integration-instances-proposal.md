# Integration Instances

Implementation design for #1443. Instances group independent connections under a shared integration definition.

## Ownership

The original Integration record is the shared definition and the fixed Default connection. Named instances are Integration records linked to that definition by `parent_integration_id`, with a stable `instance_key` and editable display name. Existing IDs and credential/config/mapping records remain in place; no account credentials are copied when an instance is added.

The definition owns its name, logo, description, setup instructions, workflow links, entity field labels, and config schema. Each instance owns config values/defaults, its default entity ID, its entity-list data provider, authentication method, OAuth provider/tokens, and organization mappings. Config values on named instances reference the shared schema fields. Schema edits therefore affect all instances and must protect stored values across all of them.

An instance key is unique within its definition and does not change when its display name changes. `default` is reserved for the original connection. It cannot be reassigned or deleted through instance management. Existing independently named integrations are not automatically combined.

## Resolution

Existing workflows continue selecting Default:

```python
from bifrost import integrations

connection = await integrations.get("AWS")
pbx_connection = await integrations.get("AWS", instance="pbxs")
optional_connection = await integrations.get(
    "AWS", instance="pbxs", on_missing="default"
)
```

The resolver selects the instance first, then applies existing organization reach and config/token inheritance within that instance. It never inherits from a sibling instance. `on_missing="fail"` is the default for an explicit selector; `on_missing="default"` permits the original connection only when the named instance is missing. Permission denials, missing/invalid credentials, OAuth failures, and provider errors are not fallback conditions. No selector always means Default.

Instance enumeration returns metadata, not credentials. Generated API clients and reusable modules can accept an instance selector rather than duplicating their source. Explicit selectors must fail on older servers that cannot identify instances, rather than silently accepting the default response.

## API And Portability

Instance management is nested under `/api/integrations/{definition_id}/instances`. Existing integration operations continue using the selected connection's UUID for defaults, mappings, OAuth, and tests. Shared-definition edits use the definition UUID. HTTP and worker-local SDK paths use the same instance resolution behavior.

Git manifests retain root and named instance identities and ownership references. Parent definitions are imported before children; child config values retain shared-schema links. Natural-key upserts preserve mapping/token/config state. Secrets remain excluded from portable source. Existing integration and Solution references continue resolving their original/default connection.

## Operator Experience

The integration page has an instance selector beside its heading, with Add Instance and Manage Instances actions. Selection changes defaults, authentication status, organization mappings, config overrides, and the test target together. A deep link retains the selected instance. Dialogs name the connection being changed, and shared-definition edits explicitly indicate that they apply to every instance.

Add Instance asks for a display name, stable key, and authentication method. It starts with empty credentials and mappings. Management permits display-name edits and deletion of named instances, with the deletion impact stated. Unknown or deleted instance links fail visibly instead of selecting Default. Switching instances must not show stale data or discard pending edits silently.

Authentication is OAuth or API Key / Custom Credentials. Config-based instances use existing config fields and do not show an OAuth setup warning. Config completeness is distinct from verified connectivity. OAuth instances retain token status and connection actions.

## Verification Boundaries

Verify old/default behavior, independent same-org credentials and mappings, explicit missing-instance fallback, authorization and secret masking, shared-schema deletion/retyping protections, provider cleanup, migration preservation, manifest round trips, CLI/MCP/SDK contract parity, and the rendered create/select/configure/delete journey. A rolling deployment must finish upgrading old application processes before named instances are created, because old binaries do not understand instance grouping.
