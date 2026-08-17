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
| `workflows.list` | `bifrost workflows list` | `bifrost_list_workflows` | `workflows.read` |
| `workflows.get` | `bifrost workflows get` | `bifrost_get_workflow` | `workflows.read` |
| `workflows.validate` | `bifrost workflows validate` | `bifrost_validate_workflow` | `workflows.read` |
| `workflows.register` | `bifrost workflows register` | `bifrost_register_workflow` | `workflows.write` |
| `workflows.execute` | `bifrost workflows execute` | `bifrost_execute_workflow` | `workflows.execute` |
| `workflows.update` | `bifrost workflows update` | `bifrost_update_workflow` | `workflows.write` |
| `workflows.delete` | `bifrost workflows delete` | `bifrost_delete_workflow` | `workflows.write` |
| `workflows.roles.grant` | `bifrost workflows grant-role` | `bifrost_grant_workflow_role` | `workflows.write` |
| `workflows.roles.revoke` | `bifrost workflows revoke-role` | `bifrost_revoke_workflow_role` | `workflows.write` |
| `integrations.list` | `bifrost integrations list` | `bifrost_list_integrations` | `integrations.read` |
| `integrations.get` | `bifrost integrations get` | `bifrost_get_integration` | `integrations.read` |
| `integrations.create` | `bifrost integrations create` | `bifrost_create_integration` | `integrations.write` |
| `integrations.update` | `bifrost integrations update` | `bifrost_update_integration` | `integrations.write` |
| `integrations.mappings.create` | `bifrost integrations create-mapping` | `bifrost_create_integration_mapping` | `integrations.write` |
| `integrations.mappings.update` | `bifrost integrations update-mapping` | `bifrost_update_integration_mapping` | `integrations.write` |
| `executions.list` | `bifrost workflows list-executions` | `bifrost_list_workflow_executions` | `executions.read` |
| `executions.get` | `bifrost workflows get-execution` | `bifrost_get_workflow_execution` | `executions.read` |
| `knowledge.search` | `bifrost knowledge search` | `bifrost_search_knowledge` | `knowledge.read` |
| `roles.list` | `bifrost roles list` | `bifrost_list_roles` | `roles.read` |
| `roles.get` | `bifrost roles get` | `bifrost_get_role` | `roles.read` |
| `roles.create` | `bifrost roles create` | `bifrost_create_role` | `roles.write` |
| `roles.update` | `bifrost roles update` | `bifrost_update_role` | `roles.write` |
| `roles.delete` | `bifrost roles delete` | `bifrost_delete_role` | `roles.write` |
| `organizations.list` | `bifrost organizations list` | `bifrost_list_organizations` | `organizations.read` |
| `organizations.get` | `bifrost organizations get` | `bifrost_get_organization` | `organizations.read` |
| `organizations.create` | `bifrost organizations create` | `bifrost_create_organization` | `organizations.write` |
| `organizations.update` | `bifrost organizations update` | `bifrost_update_organization` | `organizations.write` |
| `organizations.delete` | `bifrost organizations delete` | `bifrost_delete_organization` | `organizations.write` |
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
| `workspace.files.list` | `bifrost files list` | `bifrost_list_files` | `files.content.read` |
| `workspace.files.search` | `bifrost files search` | `bifrost_search_files` | `files.content.read` |
| `workspace.files.read` | `bifrost files read` | `bifrost_read_file` | `files.content.read` |
| `workspace.files.stat` | `bifrost files stat` | `bifrost_stat_file` | `files.content.read` |
| `workspace.files.exists` | `bifrost files exists` | `bifrost_exists_file` | `files.content.read` |
| `workspace.files.write` | `bifrost files write` | `bifrost_write_file` | `files.content.write` |
| `workspace.files.patch` | `bifrost files patch` | `bifrost_patch_file` | `files.content.write` |
| `workspace.files.delete` | `bifrost files delete` | `bifrost_delete_file` | `files.content.write` |
