# Bifrost operation reference

Generated from the canonical operation catalog. Use the stable intent
ID when reasoning; select the CLI or MCP binding available in the current
harness.

| Intent | CLI | MCP | Scope |
|---|---|---|---|
| `agents.list` | `bifrost agents list` | `bifrost_list_agents` | `agents.read` |
| `agents.get` | `bifrost agents get` | `bifrost_get_agent` | `agents.read` |
| `agents.create` | `bifrost agents create` | `bifrost_create_agent` | `agents.write` |
| `agents.update` | `bifrost agents update` | `bifrost_update_agent` | `agents.write` |
| `agents.delete` | `bifrost agents delete` | `bifrost_delete_agent` | `agents.write` |
| `forms.list` | `bifrost forms list` | `bifrost_list_forms` | `forms.read` |
| `forms.get` | `bifrost forms get` | `bifrost_get_form` | `forms.read` |
| `forms.create` | `bifrost forms create` | `bifrost_create_form` | `forms.write` |
| `forms.update` | `bifrost forms update` | `bifrost_update_form` | `forms.write` |
| `forms.delete` | `bifrost forms delete` | `bifrost_delete_form` | `forms.write` |
| `tables.list` | `bifrost tables list` | `bifrost_list_tables` | `tables.read` |
| `tables.get` | `bifrost tables get` | `bifrost_get_table` | `tables.read` |
| `tables.create` | `bifrost tables create` | `bifrost_create_table` | `tables.write` |
| `tables.update` | `bifrost tables update` | `bifrost_update_table` | `tables.write` |
| `tables.delete` | `bifrost tables delete` | `bifrost_delete_table` | `tables.write` |
| `apps.list` | `bifrost apps list` | `bifrost_list_apps` | `apps.read` |
| `apps.get` | `bifrost apps get` | `bifrost_get_app` | `apps.read` |
| `apps.create` | `bifrost apps create` | `bifrost_create_app` | `apps.write` |
| `apps.update` | `bifrost apps update` | `bifrost_update_app` | `apps.write` |
| `apps.delete` | `bifrost apps delete` | `bifrost_delete_app` | `apps.write` |
| `apps.dependencies.get` | `bifrost apps get-dependencies` | `bifrost_get_app_dependencies` | `apps.read` |
| `apps.dependencies.update` | `bifrost apps update-dependencies` | `bifrost_update_app_dependencies` | `apps.write` |
| `apps.validate` | `bifrost apps validate` | `bifrost_validate_app` | `apps.read` |
| `apps.publish` | `bifrost apps publish` | `bifrost_publish_app` | `apps.publish` |
| `apps.replace` | `bifrost apps replace` | `bifrost_replace_app` | `apps.write` |
| `events.sources.list` | `bifrost events list-sources` | `bifrost_list_event_sources` | `events.read` |
| `events.sources.get` | `bifrost events get-source` | `bifrost_get_event_source` | `events.read` |
| `events.sources.create` | `bifrost events create-source` | `bifrost_create_event_source` | `events.write` |
| `events.sources.update` | `bifrost events update-source` | `bifrost_update_event_source` | `events.write` |
| `events.sources.delete` | `bifrost events delete-source` | `bifrost_delete_event_source` | `events.write` |
| `events.subscriptions.list` | `bifrost events list-subscriptions` | `bifrost_list_event_subscriptions` | `events.read` |
| `events.subscriptions.get` | `bifrost events get-subscription` | `bifrost_get_event_subscription` | `events.read` |
| `events.subscriptions.create` | `bifrost events create-subscription` | `bifrost_create_event_subscription` | `events.write` |
| `events.subscriptions.update` | `bifrost events update-subscription` | `bifrost_update_event_subscription` | `events.write` |
| `events.subscriptions.delete` | `bifrost events delete-subscription` | `bifrost_delete_event_subscription` | `events.write` |
| `events.webhook_adapters.list` | `bifrost events list-webhook-adapters` | `bifrost_list_event_webhook_adapters` | `events.read` |
