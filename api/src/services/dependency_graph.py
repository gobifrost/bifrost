"""
Dependency Graph Service

BFS-based graph traversal for entity dependency visualization.
Builds a bidirectional dependency graph from workflows, forms, apps, and agents.

This service is query-time (not precomputed) since the dependency canvas
is rarely accessed and complexity is bounded by the depth limit.
"""

from __future__ import annotations

import logging
from collections import deque
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.orm import (
    Agent,
    AgentTool,
    Application,
    Form,
    FormField,
    Workflow,
)
from src.models.orm.file_index import FileIndex

if TYPE_CHECKING:
    from src.services.authorization.enforce import Caller

logger = logging.getLogger(__name__)


def _extract_workflows_from_props(obj: dict | list | str | int | None) -> set[UUID]:
    """
    Recursively find all workflowId and dataProviderId values in a nested dict/list.

    Handles all nested patterns including:
    - props.workflowId
    - props.onClick.workflowId
    - props.rowActions[].onClick.workflowId
    - Any other nested structure

    Args:
        obj: Nested dict, list, or primitive

    Returns:
        Set of workflow UUIDs found
    """
    workflows: set[UUID] = set()

    if isinstance(obj, dict):
        # Check for workflowId key
        if wf_id := obj.get("workflowId"):
            if isinstance(wf_id, str):
                try:
                    workflows.add(UUID(wf_id))
                except ValueError as e:
                    # Non-UUID workflowId values (e.g. portable refs) — skip
                    logger.debug(f"workflowId not a UUID, skipping: {e}")

        # Check for dataProviderId key
        if dp_id := obj.get("dataProviderId"):
            if isinstance(dp_id, str):
                try:
                    workflows.add(UUID(dp_id))
                except ValueError as e:
                    # Non-UUID dataProviderId values — skip
                    logger.debug(f"dataProviderId not a UUID, skipping: {e}")

        # Recurse into all values
        for value in obj.values():
            workflows.update(_extract_workflows_from_props(value))

    elif isinstance(obj, list):
        for item in obj:
            workflows.update(_extract_workflows_from_props(item))

    return workflows


EntityType = Literal["workflow", "form", "app", "agent"]


class GraphNode:
    """Node in the dependency graph."""

    def __init__(
        self,
        id: str,
        type: EntityType,
        name: str,
        org_id: UUID | None = None,
    ):
        self.id = id
        self.type = type
        self.name = name
        self.org_id = org_id

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "type": self.type,
            "name": self.name,
            "org_id": str(self.org_id) if self.org_id else None,
        }


class GraphEdge:
    """Edge in the dependency graph."""

    def __init__(self, source: str, target: str, relationship: str):
        self.source = source
        self.target = target
        self.relationship = relationship

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "target": self.target,
            "relationship": self.relationship,
        }


class DependencyGraph:
    """Result of dependency graph traversal."""

    def __init__(self, root_id: str):
        self.nodes: dict[str, GraphNode] = {}
        self.edges: list[GraphEdge] = []
        self.root_id = root_id

    def add_node(self, node: GraphNode) -> None:
        """Add a node to the graph if not already present."""
        if node.id not in self.nodes:
            self.nodes[node.id] = node

    def add_edge(self, source: str, target: str, relationship: str) -> None:
        """Add an edge to the graph, avoiding duplicates."""
        # Check for duplicate edges
        for edge in self.edges:
            if edge.source == source and edge.target == target:
                return
        self.edges.append(GraphEdge(source, target, relationship))

    def to_dict(self) -> dict:
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "edges": [edge.to_dict() for edge in self.edges],
            "root_id": self.root_id,
        }


class DependencyGraphService:
    """
    Service for building entity dependency graphs.

    Performs BFS traversal from a root entity, following relationships
    in both directions (uses/used_by) up to a configurable depth.
    """

    def __init__(self, db: AsyncSession, *, caller: Caller | None = None):
        self.db = db
        self.caller = caller

    def _in_original_reach(self, statement, model):
        """Apply the original caller's reach before a graph row is exposed."""
        if self.caller is None:
            return statement
        from src.services.authorization.enforce import user_reach

        predicate = user_reach(self.caller).where(model.organization_id)
        return statement if predicate is None else statement.where(predicate)

    async def _build_workflow_lookup(self) -> dict[str, UUID]:
        """Build lookup mapping any ref format -> workflow UUID.

        Handles: UUID string, workflow name, path::function_name portable ref
        """
        result = await self.db.execute(
            self._in_original_reach(
                select(
                    Workflow.id, Workflow.name, Workflow.path, Workflow.function_name
                ).where(Workflow.is_active.is_(True)),
                Workflow,
            )
        )
        lookup: dict[str, UUID] = {}
        for wf_id, wf_name, wf_path, wf_fn_name in result.all():
            lookup[str(wf_id)] = wf_id
            lookup[wf_name] = wf_id
            if wf_path and wf_fn_name:
                lookup[f"{wf_path}::{wf_fn_name}"] = wf_id
        return lookup

    async def build_graph(
        self,
        entity_type: EntityType,
        entity_id: UUID,
        depth: int = 2,
    ) -> DependencyGraph:
        """
        Build a dependency graph starting from the specified entity.

        Args:
            entity_type: Type of the root entity
            entity_id: UUID of the root entity
            depth: Maximum traversal depth (1-5)

        Returns:
            DependencyGraph with nodes and edges
        """
        depth = max(1, min(5, depth))  # Clamp to 1-5

        root_key = f"{entity_type}:{entity_id}"
        graph = DependencyGraph(root_key)

        # BFS queue: (entity_type, entity_id, current_depth)
        queue: deque[tuple[EntityType, UUID, int]] = deque()
        queue.append((entity_type, entity_id, 0))
        visited: set[str] = set()

        while queue:
            current_type, current_id, current_depth = queue.popleft()
            node_key = f"{current_type}:{current_id}"

            if node_key in visited:
                continue
            visited.add(node_key)

            # Fetch entity and add as node
            node = await self._fetch_entity_node(current_type, current_id)
            if node is None:
                continue
            graph.add_node(node)

            # Stop exploring if at max depth
            if current_depth >= depth:
                continue

            # Get dependencies in both directions
            dependencies = await self._get_dependencies(current_type, current_id)

            for dep_type, dep_id, relationship in dependencies:
                dep_key = f"{dep_type}:{dep_id}"

                dependency_node = await self._fetch_entity_node(dep_type, dep_id)
                if dependency_node is None:
                    continue

                # An edge is visible only when both endpoint rows passed the SQL
                # reach gate. Do this before adding it to avoid leaking a hidden
                # dependency identifier through graph topology.
                if relationship == "uses":
                    graph.add_edge(node_key, dep_key, "uses")
                else:  # used_by
                    graph.add_edge(dep_key, node_key, "uses")

                # Queue for exploration if not visited
                if dep_key not in visited:
                    queue.append((dep_type, dep_id, current_depth + 1))

        return graph

    async def _app_uses_workflow(
        self,
        app: Application,
        workflow_id: UUID,
    ) -> bool:
        """Check if an application uses a specific workflow by scanning file_index."""
        from src.services.app_dependencies import parse_dependencies

        if not app.repo_path:
            return False

        prefix = app.repo_prefix
        result = await self.db.execute(
            select(FileIndex.content).where(
                FileIndex.path.startswith(prefix),
            )
        )
        wf_id_str = str(workflow_id)

        # Build portable ref for the target workflow
        wf_meta = await self.db.execute(
            select(Workflow.path, Workflow.function_name, Workflow.name).where(
                Workflow.id == workflow_id
            )
        )
        row = wf_meta.one_or_none()
        portable_ref = f"{row[0]}::{row[1]}" if row and row[0] and row[1] else None

        for (content,) in result.all():
            if content:
                refs = parse_dependencies(content)
                if (
                    wf_id_str in refs
                    or (portable_ref and portable_ref in refs)
                    or (row and row[2] in refs)
                ):
                    return True
        return False

    async def _fetch_entity_node(
        self,
        entity_type: EntityType,
        entity_id: UUID,
    ) -> GraphNode | None:
        """Fetch entity details and create a GraphNode."""
        if entity_type == "workflow":
            result = await self.db.execute(self._in_original_reach(
                select(Workflow).where(Workflow.id == entity_id), Workflow
            ))
            entity = result.scalar_one_or_none()
            if entity:
                return GraphNode(
                    id=f"workflow:{entity_id}",
                    type="workflow",
                    name=entity.name,
                    org_id=entity.organization_id,
                )

        elif entity_type == "form":
            result = await self.db.execute(self._in_original_reach(
                select(Form).where(Form.id == entity_id), Form
            ))
            entity = result.scalar_one_or_none()
            if entity:
                return GraphNode(
                    id=f"form:{entity_id}",
                    type="form",
                    name=entity.name,
                    org_id=entity.organization_id,
                )

        elif entity_type == "app":
            result = await self.db.execute(self._in_original_reach(
                select(Application).where(Application.id == entity_id), Application
            ))
            entity = result.scalar_one_or_none()
            if entity:
                return GraphNode(
                    id=f"app:{entity_id}",
                    type="app",
                    name=entity.name,
                    org_id=entity.organization_id,
                )

        elif entity_type == "agent":
            result = await self.db.execute(self._in_original_reach(
                select(Agent).where(Agent.id == entity_id), Agent
            ))
            entity = result.scalar_one_or_none()
            if entity:
                return GraphNode(
                    id=f"agent:{entity_id}",
                    type="agent",
                    name=entity.name,
                    org_id=entity.organization_id,
                )

        return None

    async def _get_dependencies(
        self,
        entity_type: EntityType,
        entity_id: UUID,
    ) -> list[tuple[EntityType, UUID, str]]:
        """
        Get all dependencies for an entity in both directions.

        Returns list of (entity_type, entity_id, relationship) tuples.
        relationship is "uses" (this entity uses target) or "used_by" (target uses this).
        """
        dependencies: list[tuple[EntityType, UUID, str]] = []

        if entity_type == "workflow":
            # Workflows are USED BY forms, apps, and agents
            # Query entities directly for reverse lookups
            workflow_lookup = await self._build_workflow_lookup()

            # Check forms that reference this workflow
            forms_result = await self.db.execute(
                self._in_original_reach(
                    select(Form.id, Form.workflow_id, Form.launch_workflow_id).where(
                        Form.is_active.is_(True)
                    ),
                    Form,
                )
            )
            for form_id, workflow_ref, launch_ref in forms_result.all():
                for ref in (workflow_ref, launch_ref):
                    if ref and workflow_lookup.get(str(ref)) == entity_id:
                        dependencies.append(("form", form_id, "used_by"))
                        break

            field_result = await self.db.execute(
                self._in_original_reach(
                    select(FormField.form_id)
                    .join(Form, Form.id == FormField.form_id)
                    .where(FormField.data_provider_id == entity_id),
                    Form,
                )
            )
            for form_id in field_result.scalars().all():
                dependencies.append(("form", form_id, "used_by"))

            # Check apps that might reference this workflow (via code file dependencies)
            # This is expensive but dependency graph is rarely called
            apps_result = await self.db.execute(
                self._in_original_reach(select(Application), Application)
            )
            for app in apps_result.scalars().all():
                if await self._app_uses_workflow(app, entity_id):
                    dependencies.append(("app", app.id, "used_by"))

            # Check agents directly (via agent_tools)
            result = await self.db.execute(
                self._in_original_reach(
                    select(AgentTool.agent_id)
                    .join(Agent, Agent.id == AgentTool.agent_id)
                    .where(AgentTool.workflow_id == entity_id),
                    Agent,
                )
            )
            agent_ids = result.scalars().all()
            for agent_id in agent_ids:
                dependencies.append(("agent", agent_id, "used_by"))

        elif entity_type == "form":
            # Forms USE workflows
            result = await self.db.execute(
                self._in_original_reach(
                    select(Form)
                    .options(selectinload(Form.fields))
                    .where(Form.id == entity_id),
                    Form,
                )
            )
            form = result.scalar_one_or_none()
            if form:
                workflow_lookup = await self._build_workflow_lookup()
                # Main workflow
                if form.workflow_id:
                    wf_id = workflow_lookup.get(str(form.workflow_id))
                    if wf_id:
                        dependencies.append(("workflow", wf_id, "uses"))

                # Launch workflow
                if form.launch_workflow_id:
                    wf_id = workflow_lookup.get(str(form.launch_workflow_id))
                    if wf_id:
                        dependencies.append(("workflow", wf_id, "uses"))

                # Data provider workflows from fields
                for field in form.fields:
                    if field.data_provider_id:
                        dependencies.append(
                            ("workflow", field.data_provider_id, "uses")
                        )

        elif entity_type == "app":
            # Apps USE workflows via hook calls in source code
            # Scan file_index for apps/{slug}/* files and parse for workflow refs
            from src.services.app_dependencies import parse_dependencies

            app_result = await self.db.execute(
                self._in_original_reach(
                    select(Application).where(Application.id == entity_id), Application
                )
            )
            app = app_result.scalar_one_or_none()
            if app and app.repo_path:
                prefix = app.repo_prefix
                fi_result = await self.db.execute(
                    select(FileIndex.content).where(
                        FileIndex.path.startswith(prefix),
                    )
                )
                # Collect all workflow refs from all files
                all_refs: set[str] = set()
                for (content,) in fi_result.all():
                    if content:
                        all_refs.update(parse_dependencies(content))

                if all_refs:
                    lookup = await self._build_workflow_lookup()
                    for ref in all_refs:
                        if ref in lookup:
                            dependencies.append(("workflow", lookup[ref], "uses"))

        elif entity_type == "agent":
            # Agents USE workflows (via agent_tools)
            result = await self.db.execute(
                self._in_original_reach(
                    select(AgentTool.workflow_id)
                    .join(Agent, Agent.id == AgentTool.agent_id)
                    .where(AgentTool.agent_id == entity_id),
                    Agent,
                )
            )
            workflow_ids = result.scalars().all()
            for wf_id in workflow_ids:
                dependencies.append(("workflow", wf_id, "uses"))

        # Deduplicate dependencies
        seen: set[str] = set()
        unique_deps: list[tuple[EntityType, UUID, str]] = []
        for dep in dependencies:
            key = f"{dep[0]}:{dep[1]}:{dep[2]}"
            if key not in seen:
                seen.add(key)
                unique_deps.append(dep)

        return unique_deps


async def compute_relationship_availability(
    db: AsyncSession,
    *,
    caller: Caller | None = None,
    workflow_ids: list[UUID] | None = None,
    form_ids: list[UUID] | None = None,
    agent_ids: list[UUID] | None = None,
    app_ids: list[UUID] | None = None,
) -> dict[str, bool]:
    """Return whether each requested entity has at least one graph relationship.

    This mirrors the persisted relationship sources used by
    DependencyGraphService without running one graph query per row.
    """
    from sqlalchemy import or_
    from src.services.app_dependencies import parse_dependencies

    workflow_ids = workflow_ids or []
    form_ids = form_ids or []
    agent_ids = agent_ids or []
    app_ids = app_ids or []

    requested_workflows = set(workflow_ids)
    requested_forms = set(form_ids)
    requested_agents = set(agent_ids)
    requested_apps = set(app_ids)
    availability: dict[str, bool] = {
        **{f"workflow:{workflow_id}": False for workflow_id in requested_workflows},
        **{f"form:{form_id}": False for form_id in requested_forms},
        **{f"agent:{agent_id}": False for agent_id in requested_agents},
        **{f"app:{app_id}": False for app_id in requested_apps},
    }
    if not availability:
        return {}

    if caller is not None:
        service = DependencyGraphService(db, caller=caller)
        for entity_type, entity_ids in (
            ("workflow", requested_workflows),
            ("form", requested_forms),
            ("agent", requested_agents),
            ("app", requested_apps),
        ):
            for entity_id in entity_ids:
                graph = await service.build_graph(entity_type, entity_id)
                availability[f"{entity_type}:{entity_id}"] = bool(graph.edges)
        return availability

    lookup = await DependencyGraphService(db)._build_workflow_lookup()

    def resolve_workflow_ref(ref: object) -> UUID | None:
        if ref is None:
            return None
        if isinstance(ref, UUID):
            return ref
        return lookup.get(str(ref))

    if requested_forms or requested_workflows:
        form_query = select(Form.id, Form.workflow_id, Form.launch_workflow_id).where(
            Form.is_active.is_(True)
        )
        if requested_forms and not requested_workflows:
            form_query = form_query.where(Form.id.in_(requested_forms))
        form_result = await db.execute(form_query)
        for form_id, workflow_ref, launch_ref in form_result.all():
            for ref in (workflow_ref, launch_ref):
                workflow_id = resolve_workflow_ref(ref)
                if workflow_id is None:
                    continue
                if form_id in requested_forms:
                    availability[f"form:{form_id}"] = True
                if workflow_id in requested_workflows:
                    availability[f"workflow:{workflow_id}"] = True

        field_query = select(FormField.form_id, FormField.data_provider_id).where(
            FormField.data_provider_id.isnot(None)
        )
        if requested_forms and not requested_workflows:
            field_query = field_query.where(FormField.form_id.in_(requested_forms))
        field_result = await db.execute(field_query)
        for form_id, workflow_ref in field_result.all():
            workflow_id = resolve_workflow_ref(workflow_ref)
            if workflow_id is None:
                continue
            if form_id in requested_forms:
                availability[f"form:{form_id}"] = True
            if workflow_id in requested_workflows:
                availability[f"workflow:{workflow_id}"] = True

    if requested_agents or requested_workflows:
        tool_query = select(AgentTool.agent_id, AgentTool.workflow_id)
        predicates = []
        if requested_agents:
            predicates.append(AgentTool.agent_id.in_(requested_agents))
        if requested_workflows:
            predicates.append(AgentTool.workflow_id.in_(requested_workflows))
        if predicates:
            tool_query = tool_query.where(
                predicates[0] if len(predicates) == 1 else predicates[0] | predicates[1]
            )
        tool_result = await db.execute(tool_query)
        for agent_id, workflow_id in tool_result.all():
            if agent_id in requested_agents:
                availability[f"agent:{agent_id}"] = True
            if workflow_id in requested_workflows:
                availability[f"workflow:{workflow_id}"] = True

    if requested_apps or requested_workflows:
        app_query = select(Application.id, Application.repo_path)
        if requested_apps and not requested_workflows:
            app_query = app_query.where(Application.id.in_(requested_apps))
        apps_result = await db.execute(app_query)
        app_prefixes = {
            app_id: f"{repo_path.rstrip('/')}/"
            for app_id, repo_path in apps_result.all()
            if repo_path
        }
        if app_prefixes:
            file_predicates = [
                FileIndex.path.startswith(prefix) for prefix in app_prefixes.values()
            ]
            files_result = await db.execute(
                select(FileIndex.path, FileIndex.content).where(or_(*file_predicates))
            )
            refs_by_app: dict[UUID, set[str]] = {
                app_id: set() for app_id in app_prefixes
            }
            for path, content in files_result.all():
                if not content:
                    continue
                for app_id, prefix in app_prefixes.items():
                    if path.startswith(prefix):
                        refs_by_app[app_id].update(parse_dependencies(content))
                        break
            for app_id, refs in refs_by_app.items():
                matched_workflows = {lookup[ref] for ref in refs if ref in lookup}
                if app_id in requested_apps and matched_workflows:
                    availability[f"app:{app_id}"] = True
                for workflow_id in matched_workflows & requested_workflows:
                    availability[f"workflow:{workflow_id}"] = True

    return availability
