"""
Workflow Orphan Service

Manages orphaned workflows - workflows whose backing files no longer exist or
no longer contain the workflow function. Orphaned workflows continue to work
(using their stored code snapshot) but can't be edited via files.

This service provides:
- Listing orphaned workflows with their references (forms/apps using them)
- Finding compatible replacements based on signature matching
- Replacing orphaned workflows with content from existing files
- Recreating files from orphaned workflow code
- Deactivating orphaned workflows
"""

import ast
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.models.orm.agents import Agent, AgentTool
from src.models.orm.forms import Form, FormField
from src.models.orm.workflows import Workflow

logger = logging.getLogger(__name__)


# =============================================================================
# Pydantic Models for API Responses
# =============================================================================


class WorkflowReference(BaseModel):
    """Reference to an entity that uses a workflow."""

    type: Literal["form", "app", "agent"]
    id: str
    name: str


class OrphanedWorkflow(BaseModel):
    """Orphaned workflow with metadata and references."""

    id: str
    name: str
    function_name: str
    last_path: str
    code: str | None
    used_by: list[WorkflowReference]
    orphaned_at: datetime | None


class Replacement(BaseModel):
    """Potential replacement for an orphaned workflow."""

    path: str
    function_name: str
    signature: str
    compatibility: Literal["exact", "compatible", "incompatible"]


class RemapWorkflowReferencesResult(BaseModel):
    """Result of moving references from one workflow row to another."""

    source_workflow_id: str
    target_workflow_id: str
    updated: dict[str, int]


# =============================================================================
# Internal Data Classes
# =============================================================================


@dataclass
class FunctionSignature:
    """Parsed function signature for compatibility checking."""

    name: str
    parameters: list[tuple[str, str | None, bool]]  # (name, type_annotation, has_default)
    return_type: str | None


# =============================================================================
# Service Implementation
# =============================================================================


class WorkflowOrphanService:
    """
    Manage orphaned workflows - replace, recreate, or deactivate.

    Orphaned workflows are workflows whose backing file has been deleted or
    modified to no longer contain the workflow function. They continue to work
    using their stored code snapshot but cannot be edited via files.
    """

    def __init__(self, db: AsyncSession):
        """
        Initialize the orphan service.

        Args:
            db: Database session
        """
        self.db = db

    async def _get_code(self, path: str) -> str | None:
        """Load file content via Redis→S3 cache chain."""
        from src.core.module_cache import get_module

        cached = await get_module(path)
        return cached["content"] if cached else None

    async def get_orphaned_workflows(self) -> list[OrphanedWorkflow]:
        """
        Get all orphaned workflows with their references.

        Returns:
            List of OrphanedWorkflow with usage information
        """
        stmt = select(Workflow).where(Workflow.is_orphaned.is_(True))
        result = await self.db.execute(stmt)
        workflows = result.scalars().all()

        orphans = []
        for wf in workflows:
            used_by = await self._get_workflow_references(wf.id)
            code = await self._get_code(wf.path)
            orphans.append(
                OrphanedWorkflow(
                    id=str(wf.id),
                    name=wf.name,
                    function_name=wf.function_name,
                    last_path=wf.path,
                    code=code,
                    used_by=used_by,
                    orphaned_at=wf.updated_at,
                )
            )

        return orphans

    async def get_compatible_replacements(self, workflow_id: UUID) -> list[Replacement]:
        """
        Find files/functions that could replace this workflow.

        Matches by signature compatibility - looks for functions with
        matching or compatible parameter signatures.

        Args:
            workflow_id: UUID of the orphaned workflow

        Returns:
            List of potential replacements sorted by compatibility

        Raises:
            ValueError: If workflow not found or not orphaned
        """
        # Get the orphaned workflow
        wf = await self.db.get(Workflow, workflow_id)
        if not wf:
            raise ValueError(f"Workflow {workflow_id} not found")
        if not wf.is_orphaned:
            raise ValueError(f"Workflow {workflow_id} is not orphaned")

        orphan_code = await self._get_code(wf.path)
        if not orphan_code:
            logger.warning(f"Orphaned workflow {log_safe(workflow_id)} has no code in file_index")
            return []

        # Parse original signature
        original_sig = self._parse_function_signature(orphan_code, wf.function_name)
        if not original_sig:
            logger.warning(f"Could not parse signature for {wf.function_name}")
            return []

        # Find all active workflow files with same type
        stmt = select(Workflow).where(
            Workflow.is_active.is_(True),
            Workflow.is_orphaned.is_(False),
            Workflow.type == wf.type,
        )
        result = await self.db.execute(stmt)
        candidates = result.scalars().all()

        replacements = []
        seen_functions: set[tuple[str, str]] = set()  # (path, function_name)

        for candidate in candidates:
            # Skip the orphaned workflow itself
            if candidate.id == workflow_id:
                continue

            # Skip duplicates
            key = (candidate.path, candidate.function_name)
            if key in seen_functions:
                continue
            seen_functions.add(key)

            candidate_code = await self._get_code(candidate.path)
            if not candidate_code:
                continue

            # Parse candidate signature
            candidate_sig = self._parse_function_signature(
                candidate_code, candidate.function_name
            )
            if not candidate_sig:
                continue

            # Check compatibility
            compatibility = self._check_signature_compatibility(
                original_sig, candidate_sig
            )
            if compatibility != "incompatible":
                replacements.append(
                    Replacement(
                        path=candidate.path,
                        function_name=candidate.function_name,
                        signature=self._signature_to_string(candidate_sig),
                        compatibility=compatibility,
                    )
                )

        # Sort by compatibility (exact first, then compatible)
        replacements.sort(key=lambda r: 0 if r.compatibility == "exact" else 1)

        return replacements

    async def _read_file(self, path: str) -> str | None:
        """Load file content from storage (Redis→S3 cache chain)."""
        return await self._get_code(path)

    async def replace_workflow(
        self,
        workflow_id: UUID,
        source_path: str,
        function_name: str,
        allow_type_change: bool = False,
    ) -> Workflow:
        """Repoint an orphaned workflow to a new file location.

        Validates that ``source_path`` exists and contains a
        ``@workflow``/``@tool``/``@data_provider``-decorated function named
        ``function_name``, then updates the row in place, preserving the UUID
        so all form/agent references remain intact.

        Raises:
            ValueError: workflow not found, not orphaned, file/function missing,
                        uniqueness collision, or decorator type mismatch.
        """
        wf = await self.db.get(Workflow, workflow_id)
        if not wf:
            raise ValueError(f"Workflow {workflow_id} not found")
        if not wf.is_orphaned:
            raise ValueError(f"Workflow {workflow_id} is not orphaned")

        # Validate file exists and contains the decorated function.
        code = await self._read_file(source_path)
        if not code:
            raise ValueError(
                f"File {source_path} not found in storage"
            )
        if not self.file_contains_workflow(code, function_name):
            raise ValueError(
                f"Function '{function_name}' not found in {source_path} "
                f"(must have a @workflow, @tool, or @data_provider decorator)"
            )

        # Guard against (path, function_name) already claimed by an active row.
        stmt = select(Workflow).where(
            Workflow.path == source_path,
            Workflow.function_name == function_name,
            Workflow.is_active.is_(True),
        )
        result = await self.db.execute(stmt)
        conflict = result.scalar_one_or_none()
        if conflict is not None:
            raise ValueError(
                f"{source_path}::{function_name} is already registered "
                f"(workflow id={conflict.id}). Delete or deregister it first."
            )

        # Guard decorator-type changes unless explicitly allowed.
        if not allow_type_change:
            new_type = self._detect_type(code, function_name)
            if new_type is not None and new_type != wf.type:
                raise ValueError(
                    f"Decorator type change detected: original type is '{wf.type}', "
                    f"new function has '{new_type}'. Pass allow_type_change=True to override."
                )

        wf.path = source_path
        wf.function_name = function_name
        wf.is_orphaned = False
        wf.updated_at = datetime.now(timezone.utc)

        await self.db.commit()
        await self.db.refresh(wf)

        logger.info(
            f"Replaced orphaned workflow {log_safe(workflow_id)} with {log_safe(source_path)}::{log_safe(function_name)}"
        )

        return wf

    def _detect_type(self, code: str, function_name: str) -> str | None:
        """Return the workflow-family type from the decorator on ``function_name``."""
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    for decorator in node.decorator_list:
                        dec_name = self._get_decorator_name(decorator)
                        if dec_name in ("workflow", "tool", "data_provider"):
                            return dec_name
        return None

    async def recreate_file(self, workflow_id: UUID) -> Workflow:
        """
        Recreate the file from orphaned workflow's stored code.

        This writes the workflow's code snapshot back to the filesystem at its
        last known path, then clears the orphaned flag.

        Note: This method updates the workflow record but does NOT write to S3.
        The caller should use FileStorageService.write_file() to persist the file.

        Args:
            workflow_id: UUID of the orphaned workflow

        Returns:
            Updated Workflow with file path and code to write

        Raises:
            ValueError: If workflow not found, not orphaned, or missing code/path
        """
        wf = await self.db.get(Workflow, workflow_id)
        if not wf:
            raise ValueError(f"Workflow {workflow_id} not found")
        if not wf.is_orphaned:
            raise ValueError(f"Workflow {workflow_id} is not orphaned")
        if not wf.path:
            raise ValueError(
                f"Workflow {workflow_id} is missing path for recreation"
            )
        code = await self._get_code(wf.path)
        if not code:
            raise ValueError(
                f"Workflow {workflow_id} has no code in file_index for recreation"
            )

        # Mark as not orphaned - the actual file write is caller's responsibility
        wf.is_orphaned = False
        wf.updated_at = datetime.now(timezone.utc)

        await self.db.commit()
        await self.db.refresh(wf)

        logger.info(f"Marked workflow {log_safe(workflow_id)} for file recreation at {log_safe(wf.path)}")

        return wf

    async def deactivate_workflow(self, workflow_id: UUID) -> tuple[Workflow, int]:
        """
        Deactivate an orphaned workflow.

        This marks the workflow as inactive. Forms and apps using it will need
        to be updated to use a different workflow.

        Args:
            workflow_id: UUID of the workflow to deactivate

        Returns:
            Tuple of (updated Workflow, number of references still using it)

        Raises:
            ValueError: If workflow not found
        """
        wf = await self.db.get(Workflow, workflow_id)
        if not wf:
            raise ValueError(f"Workflow {workflow_id} not found")

        # Get reference count before deactivating
        refs = await self._get_workflow_references(wf.id)
        ref_count = len(refs)

        wf.is_active = False
        wf.updated_at = datetime.now(timezone.utc)

        await self.db.commit()
        await self.db.refresh(wf)

        if ref_count > 0:
            logger.warning(
                f"Deactivated workflow {log_safe(workflow_id)} with {ref_count} active references"
            )
        else:
            logger.info(f"Deactivated workflow {log_safe(workflow_id)}")

        return wf, ref_count

    async def remap_workflow_references(
        self,
        source_workflow_id: UUID,
        target_workflow_id: UUID,
    ) -> RemapWorkflowReferencesResult:
        """Move references from one workflow row to an existing active row.

        Unlike :meth:`replace_workflow`, this does not repair or repoint the
        source workflow. It only rewrites consumers so the source row can remain
        historical/unreferenced.
        """
        if source_workflow_id == target_workflow_id:
            raise ValueError("source and target workflows must be different")

        source = await self.db.get(Workflow, source_workflow_id)
        if source is None:
            raise ValueError(f"Workflow {source_workflow_id} not found")

        target = await self.db.get(Workflow, target_workflow_id)
        if target is None:
            raise ValueError(f"Workflow {target_workflow_id} not found")
        if not target.is_active or target.is_orphaned:
            raise ValueError(f"Target workflow {target_workflow_id} is not active")
        if target.type != source.type:
            raise ValueError(
                f"Cannot remap {source.type} references to {target.type} workflow"
            )

        source_str = str(source_workflow_id)
        target_str = str(target_workflow_id)

        forms_result = await self.db.execute(
            update(Form)
            .where(Form.workflow_id == source_str)
            .values(workflow_id=target_str)
        )
        launch_forms_result = await self.db.execute(
            update(Form)
            .where(Form.launch_workflow_id == source_str)
            .values(launch_workflow_id=target_str)
        )
        form_fields_result = await self.db.execute(
            update(FormField)
            .where(FormField.data_provider_id == source_workflow_id)
            .values(data_provider_id=target_workflow_id)
        )

        agents_updated = await self._remap_agent_tool_references(
            source_workflow_id,
            target_workflow_id,
        )

        await self.db.commit()

        updated = {
            "forms": (forms_result.rowcount or 0) + (launch_forms_result.rowcount or 0),
            "form_fields": form_fields_result.rowcount or 0,
            "agents": agents_updated,
            "apps": 0,
        }
        logger.info(
            "Remapped workflow references from %s to %s: %s",
            log_safe(source_workflow_id),
            log_safe(target_workflow_id),
            updated,
        )
        return RemapWorkflowReferencesResult(
            source_workflow_id=source_str,
            target_workflow_id=target_str,
            updated=updated,
        )

    async def _remap_agent_tool_references(
        self,
        source_workflow_id: UUID,
        target_workflow_id: UUID,
    ) -> int:
        """Move agent_tools rows while avoiding duplicate composite keys."""
        result = await self.db.execute(
            select(AgentTool).where(AgentTool.workflow_id == source_workflow_id)
        )
        source_rows = list(result.scalars().all())
        updated = 0

        for row in source_rows:
            existing_result = await self.db.execute(
                select(AgentTool).where(
                    AgentTool.agent_id == row.agent_id,
                    AgentTool.workflow_id == target_workflow_id,
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                await self.db.delete(row)
            else:
                row.workflow_id = target_workflow_id
            updated += 1

        return updated

    # =========================================================================
    # Helper Methods
    # =========================================================================

    async def _get_workflow_references(self, workflow_id: UUID) -> list[WorkflowReference]:
        """
        Find all forms, apps, and agents that reference this workflow.

        Args:
            workflow_id: UUID of the workflow

        Returns:
            List of WorkflowReference describing each entity using this workflow
        """
        refs: list[WorkflowReference] = []
        workflow_id_str = str(workflow_id)

        # Check forms (workflow_id and launch_workflow_id are stored as strings)
        stmt = select(Form).where(
            or_(
                Form.workflow_id == workflow_id_str,
                Form.launch_workflow_id == workflow_id_str,
            )
        )
        result = await self.db.execute(stmt)
        for form in result.scalars():
            refs.append(
                WorkflowReference(
                    type="form",
                    id=str(form.id),
                    name=form.name,
                )
            )

        # Check form fields (data_provider_id)
        stmt = select(FormField).where(FormField.data_provider_id == workflow_id)
        result = await self.db.execute(stmt)
        form_ids_from_fields: set[UUID] = set()
        for field in result.scalars():
            form_ids_from_fields.add(field.form_id)

        # Load forms for these fields
        if form_ids_from_fields:
            stmt = select(Form).where(Form.id.in_(form_ids_from_fields))
            result = await self.db.execute(stmt)
            for form in result.scalars():
                # Avoid duplicates
                if not any(r.type == "form" and r.id == str(form.id) for r in refs):
                    refs.append(
                        WorkflowReference(
                            type="form",
                            id=str(form.id),
                            name=form.name,
                        )
                    )

        # Check apps (pages and components)
        app_refs = await self._get_app_references(workflow_id)
        refs.extend(app_refs)

        # Check agents (via agent_tools junction table)
        stmt = select(Agent).join(AgentTool).where(AgentTool.workflow_id == workflow_id)
        result = await self.db.execute(stmt)
        for agent in result.scalars():
            refs.append(
                WorkflowReference(
                    type="agent",
                    id=str(agent.id),
                    name=agent.name,
                )
            )

        return refs

    async def _get_app_references(self, workflow_id: UUID) -> list[WorkflowReference]:
        """
        Find all apps that reference this workflow.

        Note: The component engine has been removed. Apps no longer reference
        workflows through pages/components. Code engine apps reference workflows
        through their code files, which is not tracked in the database.

        This method now always returns an empty list.

        Args:
            workflow_id: UUID of the workflow

        Returns:
            Empty list (app workflow references are no longer tracked)
        """
        # Component engine removed - apps no longer have pages/components
        # that reference workflows in a trackable way
        return []

    def _props_contain_workflow(self, obj: dict | list | str | None, workflow_id: str) -> bool:
        """
        Recursively check if props contain a workflow/data provider reference.

        Args:
            obj: The object to search (dict, list, or primitive)
            workflow_id: The workflow ID string to find

        Returns:
            True if the workflow ID is found
        """
        if obj is None:
            return False

        if isinstance(obj, dict):
            # Check workflowId and dataProviderId keys
            if obj.get("workflowId") == workflow_id:
                return True
            if obj.get("dataProviderId") == workflow_id:
                return True
            # Recurse into values
            for value in obj.values():
                if self._props_contain_workflow(value, workflow_id):
                    return True

        elif isinstance(obj, list):
            for item in obj:
                if self._props_contain_workflow(item, workflow_id):
                    return True

        return False

    def _parse_function_signature(
        self, code: str, function_name: str
    ) -> FunctionSignature | None:
        """
        Parse a function signature from Python source code.

        Args:
            code: Python source code
            function_name: Name of the function to find

        Returns:
            FunctionSignature or None if not found or parse error
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return None

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    return self._extract_signature_from_node(node)

        return None

    def _extract_signature_from_node(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef
    ) -> FunctionSignature:
        """
        Extract signature information from an AST function node.

        Args:
            node: AST FunctionDef or AsyncFunctionDef

        Returns:
            FunctionSignature with parameter info
        """
        parameters: list[tuple[str, str | None, bool]] = []
        args = node.args

        # Get defaults - they align with the end of the args list
        defaults = args.defaults
        num_defaults = len(defaults)
        num_args = len(args.args)

        for i, arg in enumerate(args.args):
            param_name = arg.arg

            # Skip self, cls, context
            if param_name in ("self", "cls", "context"):
                continue

            # Skip ExecutionContext parameter
            if arg.annotation:
                annotation_str = self._annotation_to_string(arg.annotation)
                if "ExecutionContext" in annotation_str:
                    continue

            # Get type annotation
            type_annotation = None
            if arg.annotation:
                type_annotation = self._annotation_to_string(arg.annotation)

            # Check if has default
            default_index = i - (num_args - num_defaults)
            has_default = default_index >= 0

            parameters.append((param_name, type_annotation, has_default))

        # Get return type
        return_type = None
        if node.returns:
            return_type = self._annotation_to_string(node.returns)

        return FunctionSignature(
            name=node.name,
            parameters=parameters,
            return_type=return_type,
        )

    def _annotation_to_string(self, annotation: ast.AST) -> str:
        """Convert an AST annotation to string representation."""
        if isinstance(annotation, ast.Name):
            return annotation.id
        elif isinstance(annotation, ast.Constant):
            return str(annotation.value)
        elif isinstance(annotation, ast.Subscript):
            base = self._annotation_to_string(annotation.value)
            if isinstance(annotation.slice, ast.Tuple):
                args = ", ".join(
                    self._annotation_to_string(elt) for elt in annotation.slice.elts
                )
                return f"{base}[{args}]"
            else:
                arg = self._annotation_to_string(annotation.slice)
                return f"{base}[{arg}]"
        elif isinstance(annotation, ast.Attribute):
            return f"{self._annotation_to_string(annotation.value)}.{annotation.attr}"
        elif isinstance(annotation, ast.BinOp) and isinstance(annotation.op, ast.BitOr):
            left = self._annotation_to_string(annotation.left)
            right = self._annotation_to_string(annotation.right)
            return f"{left} | {right}"
        return "Any"

    def _signature_to_string(self, sig: FunctionSignature) -> str:
        """Convert a FunctionSignature to human-readable string."""
        params = []
        for name, type_ann, has_default in sig.parameters:
            if type_ann:
                p = f"{name}: {type_ann}"
            else:
                p = name
            if has_default:
                p += " = ..."
            params.append(p)

        param_str = ", ".join(params)
        if sig.return_type:
            return f"({param_str}) -> {sig.return_type}"
        return f"({param_str})"

    def _check_signature_compatibility(
        self, original: FunctionSignature, candidate: FunctionSignature
    ) -> Literal["exact", "compatible", "incompatible"]:
        """
        Check if two function signatures are compatible.

        Compatibility rules:
        - "exact": Same parameter names, types, and defaults
        - "compatible": Candidate accepts same or fewer required params,
                       return types are compatible
        - "incompatible": Cannot be used as replacement

        Args:
            original: Original function signature
            candidate: Candidate replacement signature

        Returns:
            Compatibility level
        """
        orig_params = original.parameters
        cand_params = candidate.parameters

        # Check for exact match
        if orig_params == cand_params and original.return_type == candidate.return_type:
            return "exact"

        # Get required parameters (no default)
        orig_required = [(n, t) for n, t, has_def in orig_params if not has_def]
        cand_required = [(n, t) for n, t, has_def in cand_params if not has_def]

        # Candidate must accept at least the original required params
        # Check by name - types don't need to match exactly
        orig_required_names = {n for n, _ in orig_required}
        cand_required_names = {n for n, _ in cand_required}

        # All original required params must exist in candidate
        # (candidate can have fewer required if it has defaults for those)
        cand_all_names = {n for n, _, _ in cand_params}

        if not orig_required_names.issubset(cand_all_names):
            return "incompatible"

        # Candidate shouldn't require more params than original provides
        orig_all_names = {n for n, _, _ in orig_params}
        if not cand_required_names.issubset(orig_all_names):
            return "incompatible"

        # Return types should be compatible (None matches anything)
        if (
            candidate.return_type
            and original.return_type
            and candidate.return_type != original.return_type
        ):
            # Check for compatible return types (e.g., str is compatible with str | None)
            if not self._types_compatible(original.return_type, candidate.return_type):
                return "incompatible"

        return "compatible"

    def _types_compatible(self, orig_type: str, cand_type: str) -> bool:
        """
        Check if two type strings are compatible.

        Args:
            orig_type: Original type annotation
            cand_type: Candidate type annotation

        Returns:
            True if compatible
        """
        # Normalize types
        orig_type = orig_type.replace(" ", "")
        cand_type = cand_type.replace(" ", "")

        if orig_type == cand_type:
            return True

        # Handle Optional/Union with None
        if "|None" in cand_type or "Optional[" in cand_type:
            base = cand_type.replace("|None", "").replace("Optional[", "").rstrip("]")
            if base == orig_type:
                return True

        if "|None" in orig_type or "Optional[" in orig_type:
            base = orig_type.replace("|None", "").replace("Optional[", "").rstrip("]")
            if base == cand_type:
                return True

        return False

    def file_contains_workflow(self, code: str, function_name: str) -> bool:
        """
        Check if a Python file contains a decorated workflow/tool/data_provider function.

        Args:
            code: Python source code
            function_name: Function name to look for

        Returns:
            True if the function exists with a workflow/tool/data_provider decorator
        """
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return False

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    # Check for workflow/tool/data_provider decorator
                    for decorator in node.decorator_list:
                        dec_name = self._get_decorator_name(decorator)
                        if dec_name in ("workflow", "tool", "data_provider"):
                            return True

        return False

    def _get_decorator_name(self, decorator: ast.AST) -> str | None:
        """Extract decorator name from AST node."""
        if isinstance(decorator, ast.Name):
            return decorator.id
        elif isinstance(decorator, ast.Call):
            if isinstance(decorator.func, ast.Name):
                return decorator.func.id
            elif isinstance(decorator.func, ast.Attribute):
                return decorator.func.attr
        return None
