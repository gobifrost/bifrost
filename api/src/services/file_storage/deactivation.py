"""
Deactivation Protection Service for File Storage.

Detects workflows that would be deactivated by file saves and provides
replacement suggestions.
"""

import logging
from difflib import SequenceMatcher
from typing import TYPE_CHECKING
from uuid import UUID as UUID_type

from sqlalchemy import select, update, or_
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe

from .models import PendingDeactivationInfo, AvailableReplacementInfo

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class DeactivationProtectionService:
    """Service for detecting and preventing unintentional workflow deactivations."""

    def __init__(self, db: AsyncSession):
        self.db = db

    def compute_similarity(self, old_name: str, new_name: str) -> float:
        """
        Compute similarity score between old and new function names.

        Uses SequenceMatcher for basic similarity plus bonus for shared word parts.

        Args:
            old_name: Original function name
            new_name: New function name

        Returns:
            Similarity score between 0.0 and 1.0
        """
        # Basic sequence matching
        ratio = SequenceMatcher(None, old_name.lower(), new_name.lower()).ratio()

        # Bonus for common word parts (split by underscore for snake_case)
        old_parts = set(old_name.lower().split("_"))
        new_parts = set(new_name.lower().split("_"))
        if old_parts and new_parts:
            overlap = len(old_parts & new_parts) / max(len(old_parts), len(new_parts))
        else:
            overlap = 0.0

        return (ratio * 0.7) + (overlap * 0.3)

    async def find_affected_entities(
        self,
        workflow_id: str,
    ) -> list[dict[str, str]]:
        """
        Find forms, agents, and apps that reference a workflow.

        Args:
            workflow_id: UUID of the workflow

        Returns:
            List of affected entities with entity_type, id, name, reference_type
        """
        from src.models import Form, FormField, Agent, AgentTool

        affected: list[dict[str, str]] = []

        # Find forms that reference this workflow
        # Forms reference workflows via workflow_id (main) and launch_workflow_id
        form_stmt = select(Form).where(
            Form.is_active == True,  # noqa: E712
            or_(
                Form.workflow_id == workflow_id,
                Form.launch_workflow_id == workflow_id,
            )
        )
        form_result = await self.db.execute(form_stmt)
        forms = form_result.scalars().all()

        for form in forms:
            ref_types = []
            if form.workflow_id == workflow_id:
                ref_types.append("workflow")
            if form.launch_workflow_id == workflow_id:
                ref_types.append("launch_workflow")

            affected.append({
                "entity_type": "form",
                "id": str(form.id),
                "name": form.name,
                "reference_type": ", ".join(ref_types),
            })

        # Find form fields that use this workflow as a data provider
        field_stmt = select(FormField).where(
            FormField.data_provider_id == UUID_type(workflow_id)
        )
        field_result = await self.db.execute(field_stmt)
        form_fields = field_result.scalars().all()

        # Get unique form IDs from fields and fetch form names
        form_ids_from_fields = {field.form_id for field in form_fields}
        for form_id in form_ids_from_fields:
            # Skip if we already have this form
            if any(e["entity_type"] == "form" and e["id"] == str(form_id) for e in affected):
                continue

            form_stmt = select(Form).where(Form.id == form_id)
            form_result = await self.db.execute(form_stmt)
            form = form_result.scalar_one_or_none()
            if form:
                affected.append({
                    "entity_type": "form",
                    "id": str(form.id),
                    "name": form.name,
                    "reference_type": "data_provider",
                })

        # Find agents that use this workflow as a tool
        agent_stmt = (
            select(Agent)
            .join(AgentTool, Agent.id == AgentTool.agent_id)
            .where(
                Agent.is_active == True,  # noqa: E712
                AgentTool.workflow_id == UUID_type(workflow_id),
            )
        )
        agent_result = await self.db.execute(agent_stmt)
        agents = agent_result.scalars().all()

        for agent in agents:
            affected.append({
                "entity_type": "agent",
                "id": str(agent.id),
                "name": agent.name,
                "reference_type": "tool",
            })

        # Note: The component engine has been removed. Apps no longer reference
        # workflows through pages/components. Code engine apps reference workflows
        # through their code files, which is not tracked in the database.

        return affected

    async def detect_pending_deactivations(
        self,
        path: str,
        new_function_names: set[str],
        new_decorator_info: dict[str, tuple[str, str]],  # function_name -> (decorator_type, display_name)
    ) -> tuple[list[PendingDeactivationInfo], list[AvailableReplacementInfo]]:
        """
        Detect workflows that would be deactivated by saving a file.

        Compares existing active workflows at this path against the new
        function names found in the file content.

        Args:
            path: File path being saved
            new_function_names: Set of function names with decorators in new content
            new_decorator_info: Mapping of function_name to (decorator_type, display_name)

        Returns:
            Tuple of (pending_deactivations, available_replacements)
        """
        from src.models import Workflow, Execution

        pending_deactivations: list[PendingDeactivationInfo] = []
        available_replacements: list[AvailableReplacementInfo] = []

        # Get all active workflows at this path. Scope to _repo/ rows
        # (solution_id IS NULL): saving a WORKSPACE file must never consider a
        # solution-managed workflow at the same path for deactivation — deploy is
        # its sole writer (Codex #14).
        stmt = select(Workflow).where(
            Workflow.path == path,
            Workflow.is_active == True,  # noqa: E712
            Workflow.solution_id.is_(None),
        )
        result = await self.db.execute(stmt)
        existing_workflows = list(result.scalars().all())

        # Find workflows that would be deactivated
        existing_function_names = {wf.function_name for wf in existing_workflows}

        for wf in existing_workflows:
            if wf.function_name not in new_function_names:
                # This workflow would be deactivated

                # Check for execution history
                # Note: Executions are linked by workflow_name, not workflow_id
                exec_stmt = (
                    select(Execution)
                    .where(Execution.workflow_name == wf.function_name)
                    .order_by(Execution.started_at.desc())
                    .limit(1)
                )
                exec_result = await self.db.execute(exec_stmt)
                last_exec = exec_result.scalar_one_or_none()

                # Find affected entities
                affected_entities = await self.find_affected_entities(str(wf.id))

                pending_deactivations.append(PendingDeactivationInfo(
                    id=str(wf.id),
                    name=wf.name,
                    function_name=wf.function_name,
                    path=wf.path,
                    description=wf.description,
                    decorator_type=wf.type or "workflow",
                    has_executions=last_exec is not None,
                    last_execution_at=last_exec.started_at.isoformat() if last_exec else None,
                    endpoint_enabled=wf.endpoint_enabled or False,
                    affected_entities=affected_entities,
                ))

        # Find available replacements (new functions not in existing)
        if pending_deactivations:
            new_only_functions = new_function_names - existing_function_names

            for func_name in new_only_functions:
                decorator_type, display_name = new_decorator_info.get(
                    func_name, ("workflow", func_name)
                )

                # Calculate best similarity score against any pending deactivation
                best_score = 0.0
                for pd in pending_deactivations:
                    score = self.compute_similarity(pd.function_name, func_name)
                    best_score = max(best_score, score)

                # Only include if there's some similarity (threshold 0.2)
                if best_score >= 0.2:
                    available_replacements.append(AvailableReplacementInfo(
                        function_name=func_name,
                        name=display_name,
                        decorator_type=decorator_type,
                        similarity_score=round(best_score, 2),
                    ))

            # Sort by similarity descending
            available_replacements.sort(key=lambda x: x.similarity_score, reverse=True)

        return pending_deactivations, available_replacements

    async def apply_workflow_replacements(
        self,
        replacements: dict[str, str],
    ) -> None:
        """
        Apply workflow identity replacements.

        For each mapping of old_workflow_id -> new_function_name, update the
        existing workflow record to use the new function name while preserving
        the ID (and thus execution history, schedules, etc.).

        Args:
            replacements: Mapping of workflow_id -> new_function_name
        """
        from src.models import Workflow

        for old_id, new_function_name in replacements.items():
            try:
                workflow_uuid = UUID_type(old_id)
            except ValueError:
                logger.warning(f"Invalid workflow ID in replacement: {log_safe(old_id)}")
                continue

            # Update the workflow's function_name
            # The rest of the metadata will be updated by the indexing pass
            stmt = (
                update(Workflow)
                .where(Workflow.id == workflow_uuid)
                .values(function_name=new_function_name)
            )
            await self.db.execute(stmt)
            logger.info(
                f"Applied replacement: workflow {log_safe(old_id)} -> "
                f"function {log_safe(new_function_name)}"
            )

    async def deactivate_workflows_by_id(
        self,
        workflow_ids: list[str],
    ) -> int:
        """
        Deactivate specific workflows by their IDs.

        Used for selective per-workflow deactivation when the user chooses
        to deactivate some workflows while mapping others to replacements.

        Args:
            workflow_ids: List of workflow UUID strings to deactivate

        Returns:
            Number of workflows deactivated
        """
        from src.models import Workflow

        if not workflow_ids:
            return 0

        uuids = []
        for wid in workflow_ids:
            try:
                uuids.append(UUID_type(wid))
            except ValueError:
                logger.warning(
                    f"Invalid workflow ID in deactivation list: {log_safe(wid)}"
                )
                continue

        if not uuids:
            return 0

        stmt = (
            update(Workflow)
            .where(
                Workflow.id.in_(uuids),
                Workflow.is_active == True,  # noqa: E712
            )
            .values(is_active=False)
        )
        result = await self.db.execute(stmt)
        count = result.rowcount if result.rowcount else 0

        if count > 0:
            logger.info(f"Selectively deactivated {count} workflow(s) by ID")

        return count

    async def deactivate_removed_workflows(
        self,
        path: str,
        remaining_function_names: set[str],
    ) -> int:
        """
        Deactivate workflows that are no longer in a file.

        Called when force_deactivation=True to mark workflows as inactive
        when they've been removed from a file.

        Args:
            path: File path that was updated
            remaining_function_names: Set of function names still in the file

        Returns:
            Number of workflows deactivated
        """
        from src.models import Workflow

        # Find active workflows at this path that are not in the remaining
        # functions. Scope to _repo/ rows (solution_id IS NULL): a workspace file
        # save must never deactivate a solution-managed workflow sharing this path
        # (deploy owns those rows — Codex #14).
        if remaining_function_names:
            stmt = (
                update(Workflow)
                .where(
                    Workflow.path == path,
                    Workflow.is_active == True,  # noqa: E712
                    Workflow.solution_id.is_(None),
                    ~Workflow.function_name.in_(remaining_function_names),
                )
                .values(is_active=False)
            )
        else:
            # No functions remain - deactivate all _repo/ workflows at this path
            stmt = (
                update(Workflow)
                .where(
                    Workflow.path == path,
                    Workflow.is_active == True,  # noqa: E712
                    Workflow.solution_id.is_(None),
                )
                .values(is_active=False)
            )
        result = await self.db.execute(stmt)
        count = result.rowcount if result.rowcount else 0

        if count > 0:
            logger.info(
                f"Deactivated {count} workflow(s) from {log_safe(path)} "
                f"via force_deactivation"
            )

        return count
