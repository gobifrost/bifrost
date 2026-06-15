# Python SDK Signatures (generated — do not edit)

> Regenerate: `python api/scripts/skill-truth/generate.py`. CI enforces freshness.

### agents

Agent execution operations.

**`agents.run(agent_name: str, input: dict[str, Any] | None = None, output_schema: dict[str, Any] | None = None, timeout: int = 1800) -> dict[str, Any] | str`**
  Run an agent and wait for the result.

### ai



**`ai.complete(prompt: str | None = None, messages: list[dict[str, str]] | None = None, system: str | None = None, response_format: type[T] | None = None, knowledge: list[str] | None = None, max_tokens: int | None = None, org_id: str | None = None, model: str | None = None, timeout: float | None = None) -> AIResponse | T`**

**`ai.get_model_info() -> dict[str, Any]`**

**`ai.stream(prompt: str | None = None, messages: list[dict[str, str]] | None = None, system: str | None = None, knowledge: list[str] | None = None, max_tokens: int | None = None, org_id: str | None = None, model: str | None = None) -> AsyncGenerator[AIStreamChunk, None]`**

### config



**`config.delete(key: str, scope: str | None = None) -> bool`**

**`config.get(key: str, default: Any = None, scope: str | None = None) -> Any`**

**`config.list(scope: str | None = None) -> ConfigData`**

**`config.set(key: str, value: Any, is_secret: bool = False, scope: str | None = None) -> None`**

### events

Event publishing operations (async).

**`events.emit(topic: str, data: dict, scope: str | None = None) -> dict`**

### executions



**`executions.get(execution_id: str) -> WorkflowExecution`**

**`executions.get_current_logs(execution_id: str | None = None, start: str = '0', count: int = 100) -> 'list[ExecutionLog]'`**

**`executions.list(workflow_name: str | None = None, status: str | None = None, start_date: str | None = None, end_date: str | None = None, limit: int = 50) -> list[WorkflowExecution]`**

### files



**`files.delete(path: str, location: str = 'workspace', mode: Mode = 'cloud', scope: str | None = None) -> None`**

**`files.exists(path: str, location: str = 'workspace', mode: Mode = 'cloud', scope: str | None = None) -> bool`**

**`files.get_signed_url(path: str, method: Literal['PUT', 'GET'] = 'PUT', content_type: str = 'application/octet-stream', location: str = 'uploads', scope: str | None = None) -> dict`**

**`files.list(directory: str = '', location: str = 'workspace', mode: Mode = 'cloud', scope: str | None = None) -> list[str]`**

**`files.read(path: str, location: str = 'workspace', mode: Mode = 'cloud', scope: str | None = None) -> str`**

**`files.read_bytes(path: str, location: str = 'workspace', mode: Mode = 'cloud', scope: str | None = None) -> bytes`**

**`files.search(query: str, case_sensitive: bool = False, is_regex: bool = False, include_pattern: str = '**/*', max_results: int = 1000) -> dict`**

**`files.write(path: str, content: str, location: str = 'workspace', mode: Mode = 'cloud', scope: str | None = None) -> None`**

**`files.write_bytes(path: str, content: bytes, location: str = 'workspace', mode: Mode = 'cloud', scope: str | None = None) -> None`**

### forms



**`forms.get(form_id: str) -> FormPublic`**

**`forms.list() -> list[FormPublic]`**

### integrations



**`integrations.delete_mapping(name: str, scope: str) -> bool`**

**`integrations.get(name: str, scope: str | None = None, oauth_scope: str | None = None) -> IntegrationData | None`**

**`integrations.get_mapping(name: str, scope: str | None = None, entity_id: str | None = None) -> IntegrationMappingResponse | None`**

**`integrations.list_mappings(name: str, scope: str | None = None) -> list[IntegrationMappingResponse] | None`**

**`integrations.upsert_mapping(name: str, scope: str, entity_id: str, entity_name: str | None = None, config: dict | None = None) -> IntegrationMappingResponse`**

### knowledge



**`knowledge.delete(key: str, namespace: str = 'default', scope: str | None = None) -> bool`**

**`knowledge.delete_namespace(namespace: str, scope: str | None = None) -> int`**

**`knowledge.get(key: str, namespace: str = 'default', scope: str | None = None) -> KnowledgeDocument | None`**

**`knowledge.list_namespaces(scope: str | None = None, include_global: bool = True) -> list[NamespaceInfo]`**

**`knowledge.search(query: str, namespace: str | list[str] = 'default', limit: int = 5, min_score: float | None = None, metadata_filter: dict[str, Any] | None = None, scope: str | None = None, fallback: bool = True) -> list[KnowledgeDocument]`**

**`knowledge.store(content: str, namespace: str = 'default', key: str | None = None, metadata: dict[str, Any] | None = None, scope: str | None = None) -> str`**

**`knowledge.store_many(documents: list[dict[str, Any]], namespace: str = 'default', scope: str | None = None, timeout: float | None = 300.0) -> list[str]`**

### organizations



**`organizations.create(name: str, domain: str | None = None, is_active: bool = True) -> Organization`**

**`organizations.delete(org_id: str) -> bool`**

**`organizations.get(org_id: str) -> Organization`**

**`organizations.list() -> list[Organization]`**

**`organizations.update(org_id: str, updates: Any) -> Organization`**

### roles



**`roles.assign_forms(role_id: str, form_ids: list[str]) -> None`**

**`roles.assign_users(role_id: str, user_ids: list[str]) -> None`**

**`roles.create(name: str, description: str = '') -> Role`**

**`roles.delete(role_id: str) -> None`**

**`roles.get(role_id: str) -> Role`**

**`roles.list() -> list[Role]`**

**`roles.list_forms(role_id: str) -> list[str]`**

**`roles.list_users(role_id: str) -> list[str]`**

**`roles.update(role_id: str, updates: Any) -> Role`**

### tables



**`tables.count(table: str, where: dict[str, Any] | None = None, scope: str | None = None) -> int`**

**`tables.create(name: str, description: str | None = None, table_schema: dict[str, Any] | None = None, scope: str | None = None, app: str | None = None) -> TableInfo`**

**`tables.delete(table_id: str) -> bool`**

**`tables.delete_batch(table: str, doc_ids: list[str], scope: str | None = None) -> BatchDeleteResult`**

**`tables.delete_document(table: str, doc_id: str, scope: str | None = None) -> bool`**

**`tables.get(table: str, doc_id: str, scope: str | None = None) -> DocumentData | None`**

**`tables.insert(table: str, data: dict[str, Any], id: str | None = None, scope: str | None = None, created_by: str | None = None) -> DocumentData`**

**`tables.insert_batch(table: str, documents: list[dict[str, Any]], scope: str | None = None, created_by: str | None = None) -> BatchResult`**

**`tables.list(scope: str | None = None, app: str | None = None) -> list[TableInfo]`**

**`tables.query(table: str, where: dict[str, Any] | None = None, order_by: str | None = None, order_dir: str = 'asc', limit: int = 100, offset: int = 0, scope: str | None = None) -> DocumentList`**

**`tables.update(table: str, doc_id: str, data: dict[str, Any], scope: str | None = None, updated_by: str | None = None) -> DocumentData | None`**

**`tables.upsert(table: str, id: str, data: dict[str, Any], scope: str | None = None, created_by: str | None = None, updated_by: str | None = None) -> DocumentData`**

**`tables.upsert_batch(table: str, documents: list[dict[str, Any]], scope: str | None = None, created_by: str | None = None, updated_by: str | None = None) -> BatchResult`**

### users



**`users.create(email: str, name: str, is_superuser: bool = False, org_id: str | None = None, is_active: bool = True) -> UserPublic`**

**`users.delete(user_id: str) -> bool`**

**`users.get(user_id: str) -> UserPublic | None`**

**`users.list(org_id: str | None = None, include_inactive: bool = False) -> list[UserPublic]`**

**`users.update(user_id: str, updates: Any) -> UserPublic`**

### workflows



**`workflows.cancel(execution_id: str) -> None`**
  Cancel a Scheduled workflow execution.

**`workflows.execute(workflow: str, input_data: dict[str, Any] | None = None, org_id: str | None = None, run_as: str | None = None, scheduled_at: datetime | None = None, delay_seconds: int | None = None) -> str`**

**`workflows.get(execution_id: str) -> WorkflowExecution`**

**`workflows.list() -> list[WorkflowMetadata]`**

