"""
Integration tests for workflow deactivation protection.

Tests the API behavior when saving files that would deactivate workflows:
1. Returns 409 with structured data when workflows would be deactivated
2. Allows save with force_deactivation=true flag
3. Preserves workflow identity (and execution history) when using replacements
"""

import sys
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Workflow, Execution
from src.models.orm.users import User
from src.core.constants import PROVIDER_ORG_ID


async def register_workflow_in_db(
    db_session: AsyncSession,
    path: str,
    function_name: str,
    wf_type: str = "workflow",
) -> Workflow:
    """Register a workflow directly in the DB and enrich via indexer.

    Creates a minimal Workflow ORM record, then runs the WorkflowIndexer
    to populate content-derived fields (name, description, etc.) from
    the file already stored in S3.

    Args:
        db_session: Async database session
        path: File path in workspace
        function_name: Python function name (the def name)
        wf_type: Workflow type - "workflow", "tool", or "data_provider"

    Returns:
        The enriched Workflow ORM object
    """
    from src.services.file_storage import FileStorageService
    from src.services.file_storage.indexers.workflow import WorkflowIndexer

    # Create minimal DB record
    wf = Workflow(
        id=uuid4(),
        name=function_name,
        function_name=function_name,
        path=path,
        type=wf_type,
        is_active=True,
    )
    db_session.add(wf)
    await db_session.flush()

    # Read file content and run indexer to enrich
    service = FileStorageService(db_session)
    content_tuple = await service.read_file(path)
    content = content_tuple[0]

    indexer = WorkflowIndexer(db_session)
    await indexer.index_python_file(path, content)
    await db_session.flush()

    # Re-fetch enriched record
    result = await db_session.execute(
        select(Workflow).where(Workflow.id == wf.id)
    )
    return result.scalar_one()


def get_file_storage_service(db_session):
    """
    Get a fresh FileStorageService with clean libcst state.

    This function clears the libcst module cache before importing FileStorageService
    to ensure the libcst parser is not in a corrupted state from previous tests.
    """
    # Remove all libcst modules from cache
    libcst_modules = [k for k in list(sys.modules.keys()) if k.startswith('libcst')]
    for mod in libcst_modules:
        del sys.modules[mod]

    # Also remove decorator_property_service to force reimport with fresh libcst
    service_key = 'src.services.decorator_property_service'
    if service_key in sys.modules:
        del sys.modules[service_key]

    # Remove file_storage_service to force reimport with fresh decorator_property_service
    storage_key = 'src.services.file_storage'
    if storage_key in sys.modules:
        del sys.modules[storage_key]

    # Now import FileStorageService fresh
    from src.services.file_storage import FileStorageService
    return FileStorageService(db_session)


@pytest_asyncio.fixture
async def clean_tables(db_session: AsyncSession):
    """Clean up test-created data using targeted deletes.

    Instead of truncating entire tables (which affects other concurrent tests),
    this fixture deletes only records created by this test module.

    Also resets Redis-using singletons to avoid connection leaks across event loops
    (each test function gets a fresh event loop with asyncio_default_fixture_loop_scope=function).
    """
    from sqlalchemy import delete
    from src.models import Workflow, FileIndex
    import src.services.notification_service as notification_module
    import src.core.redis_client as redis_module

    # Reset singletons BEFORE test runs to ensure fresh Redis connections on current event loop.
    # We can't call close() on old services because they were created on a closed event loop.
    # Just clear the singleton references - old connections are effectively dead anyway.
    notification_module._notification_service = None
    redis_module._redis_client = None

    yield

    # Clean up only test-specific records by path patterns
    # These patterns match the workflow files created in this test module
    test_paths = [
        "test_file.py",
        "test_workflow.py",
        "data_provider_file.py",
        "test_deactivation.py",
        "test_replacements.py",
        "test_force.py",
        "test_identity.py",
        "test_exec_history.py",
        "brand_new_file.py",
        "test_add_workflow.py",
        "test_data_provider.py",
    ]
    for path in test_paths:
        await db_session.execute(
            delete(Workflow).where(Workflow.path == path)
        )
        await db_session.execute(
            delete(FileIndex).where(FileIndex.path == path)
        )

    # Also clean up by name patterns used in tests
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("Test Workflow%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("First Workflow%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("Second Workflow%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name == "Test Data Provider")
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("Force Test%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("Identity Test%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("Exec History%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("Brand New%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("Original Workflow%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("New Workflow%"))
    )
    await db_session.execute(
        delete(Workflow).where(Workflow.name.like("Test Provider%"))
    )
    await db_session.commit()


# Sample workflow code templates
WORKFLOW_CODE_TEMPLATE = '''"""Sample workflow file."""
from bifrost import workflow

@workflow(name="{name}")
def {function_name}():
    """A test workflow."""
    pass
'''

TWO_WORKFLOW_CODE = '''"""Sample workflow file with two workflows."""
from bifrost import workflow

@workflow(name="First Workflow")
def first_workflow():
    """First test workflow."""
    pass

@workflow(name="Second Workflow")
def second_workflow():
    """Second test workflow."""
    pass
'''

RENAMED_WORKFLOW_CODE = '''"""Sample workflow file with renamed function."""
from bifrost import workflow

@workflow(name="First Workflow Renamed")
def first_workflow_renamed():
    """First test workflow, renamed."""
    pass

@workflow(name="Second Workflow")
def second_workflow():
    """Second test workflow."""
    pass
'''

EMPTY_WORKFLOW_FILE = '''"""Sample file with no workflows."""

def helper_function():
    """Not a workflow."""
    pass
'''


@pytest.mark.e2e
@pytest.mark.asyncio
class TestDeactivationProtection:
    """Integration tests for workflow deactivation protection."""

    async def test_save_returns_409_when_workflow_would_deactivate(
        self,
        db_session: AsyncSession,
        clean_tables,
    ):
        """
        Saving a file that removes/renames a workflow function returns 409.

        Scenario:
        1. Save a Python file with a @workflow decorator and register it
        2. Verify workflow is created in DB
        3. Save modified version that removes the function
        4. Assert pending_deactivations array is populated
        """
        storage = get_file_storage_service(db_session)
        path = "test_deactivation.py"

        # Step 1: Save initial workflow file
        initial_code = WORKFLOW_CODE_TEMPLATE.format(
            name="Test Workflow",
            function_name="test_workflow"
        )
        result = await storage.write_file(path, initial_code.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Verify no pending deactivations on initial save
        assert result.pending_deactivations is None or len(result.pending_deactivations) == 0

        # Register the workflow in DB (auto-discovery removed)
        workflow = await register_workflow_in_db(db_session, path, "test_workflow")
        await db_session.commit()

        # Step 2: Verify workflow was created in DB
        assert workflow is not None
        assert workflow.function_name == "test_workflow"
        # The indexer only sets name when it is NULL; since register_workflow_in_db
        # initialises name = function_name, the display name from the decorator is
        # not applied automatically.
        assert workflow.name == "test_workflow"

        # Step 3: Save modified version that removes the workflow
        result = await storage.write_file(path, EMPTY_WORKFLOW_FILE.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Step 4: Assert pending deactivations are returned
        assert result.pending_deactivations is not None
        assert len(result.pending_deactivations) == 1

        pending = result.pending_deactivations[0]
        assert pending.id == str(workflow.id)
        # name in the DB is set to function_name on creation; decorator display name
        # is not auto-applied (indexer only writes name when it is NULL).
        assert pending.name == "test_workflow"
        assert pending.function_name == "test_workflow"
        assert pending.path == path
        assert pending.decorator_type in ("workflow", "tool", "data_provider")

    async def test_save_returns_available_replacements_for_renamed_functions(
        self,
        db_session: AsyncSession,
        clean_tables,
    ):
        """
        When a workflow is renamed, available_replacements suggests the new function.

        Scenario:
        1. Save file with first_workflow and second_workflow, register both
        2. Save modified file that renames first_workflow to first_workflow_renamed
        3. Assert available_replacements includes the new function with similarity score
        """
        storage = get_file_storage_service(db_session)
        path = "test_replacements.py"

        # Step 1: Save initial two-workflow file
        result = await storage.write_file(path, TWO_WORKFLOW_CODE.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Register both workflows in DB (auto-discovery removed)
        await register_workflow_in_db(db_session, path, "first_workflow")
        await register_workflow_in_db(db_session, path, "second_workflow")
        await db_session.commit()

        # Verify workflows were created
        stmt = select(Workflow).where(Workflow.path == path, Workflow.is_active == True)  # noqa: E712
        wf_result = await db_session.execute(stmt)
        workflows = list(wf_result.scalars().all())

        assert len(workflows) == 2
        func_names = {wf.function_name for wf in workflows}
        assert func_names == {"first_workflow", "second_workflow"}

        # Step 2: Save modified version with renamed function
        result = await storage.write_file(path, RENAMED_WORKFLOW_CODE.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Step 3: Assert pending deactivations and replacements
        assert result.pending_deactivations is not None
        assert len(result.pending_deactivations) == 1  # first_workflow would be deactivated

        pending = result.pending_deactivations[0]
        assert pending.function_name == "first_workflow"

        # Assert replacement is suggested
        assert result.available_replacements is not None
        assert len(result.available_replacements) == 1

        replacement = result.available_replacements[0]
        assert replacement.function_name == "first_workflow_renamed"
        assert replacement.similarity_score > 0.5  # Should have good similarity

    async def test_force_flag_allows_deactivation(
        self,
        db_session: AsyncSession,
        clean_tables,
    ):
        """
        Using force_deactivation=True allows the save and deactivates workflow.

        Scenario:
        1. Save initial workflow file and register it
        2. Verify workflow is active
        3. Try to save file removing workflow (without force) - get pending deactivations
        4. Retry with force_deactivation=True
        5. Assert workflow is now inactive in DB
        """
        storage = get_file_storage_service(db_session)
        path = "test_force.py"

        # Step 1: Save initial workflow
        initial_code = WORKFLOW_CODE_TEMPLATE.format(
            name="Force Test Workflow",
            function_name="force_test_workflow"
        )
        await storage.write_file(path, initial_code.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Register the workflow in DB (auto-discovery removed)
        workflow = await register_workflow_in_db(db_session, path, "force_test_workflow")
        await db_session.commit()

        # Step 2: Verify workflow is active
        workflow_id = workflow.id
        assert workflow.is_active is True

        # Step 3: Try to remove workflow (without force) - should get pending deactivations
        result = await storage.write_file(path, EMPTY_WORKFLOW_FILE.encode("utf-8"), updated_by="test")
        await db_session.commit()

        assert result.pending_deactivations is not None
        assert len(result.pending_deactivations) == 1

        # Workflow should still be active (save didn't complete deactivation)
        await db_session.refresh(workflow)
        # Note: The workflow stays active because the file save returns early with pending_deactivations
        # but the metadata extraction doesn't complete

        # Step 4: Retry with force_deactivation=True
        result = await storage.write_file(
            path,
            EMPTY_WORKFLOW_FILE.encode("utf-8"),
            updated_by="test",
            force_deactivation=True
        )
        await db_session.commit()

        # No pending deactivations when forced
        assert result.pending_deactivations is None or len(result.pending_deactivations) == 0

        # Step 5: Verify workflow is now inactive
        stmt = select(Workflow).where(Workflow.id == workflow_id)
        wf_result = await db_session.execute(stmt)
        workflow = wf_result.scalar_one()

        assert workflow.is_active is False

    async def test_replacement_preserves_workflow_id(
        self,
        db_session: AsyncSession,
        clean_tables,
    ):
        """
        Using replacements transfers identity to new function name.

        Scenario:
        1. Save initial workflow file and register it
        2. Get workflow ID
        3. Rename function and provide replacement mapping
        4. Assert workflow ID is preserved with new function_name
        """
        storage = get_file_storage_service(db_session)
        path = "test_identity.py"

        # Step 1: Save initial workflow
        initial_code = WORKFLOW_CODE_TEMPLATE.format(
            name="Identity Test",
            function_name="old_function_name"
        )
        await storage.write_file(path, initial_code.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Register the workflow in DB (auto-discovery removed)
        original_workflow = await register_workflow_in_db(db_session, path, "old_function_name")
        await db_session.commit()

        # Step 2: Get original workflow ID
        original_id = original_workflow.id

        # Step 3: Save with renamed function AND replacement mapping
        new_code = WORKFLOW_CODE_TEMPLATE.format(
            name="Identity Test Renamed",
            function_name="new_function_name"
        )

        # First attempt without replacements to see the pending deactivation
        result = await storage.write_file(path, new_code.encode("utf-8"), updated_by="test")
        await db_session.commit()

        assert result.pending_deactivations is not None
        assert len(result.pending_deactivations) == 1
        assert result.pending_deactivations[0].id == str(original_id)

        # Now save with replacement mapping
        replacements = {str(original_id): "new_function_name"}
        result = await storage.write_file(
            path,
            new_code.encode("utf-8"),
            updated_by="test",
            replacements=replacements
        )
        await db_session.commit()

        # No pending deactivations when replacement is provided
        assert result.pending_deactivations is None or len(result.pending_deactivations) == 0

        # Step 4: Verify workflow ID is preserved with new function name
        stmt = select(Workflow).where(Workflow.id == original_id)
        wf_result = await db_session.execute(stmt)
        workflow = wf_result.scalar_one()

        assert workflow.function_name == "new_function_name"
        assert workflow.is_active is True
        # The indexer only updates name when it is NULL; since the workflow was
        # created with name=function_name ("old_function_name"), the display name
        # from the decorator is never written — name stays as the original value.
        assert workflow.name == "old_function_name"

    async def test_deactivation_includes_execution_history_info(
        self,
        db_session: AsyncSession,
        clean_tables,
    ):
        """
        Pending deactivations include execution history information.

        Scenario:
        1. Create workflow with execution record
        2. Try to remove workflow
        3. Assert pending deactivation has has_executions=True and last_execution_at set
        """
        storage = get_file_storage_service(db_session)
        path = "test_exec_history.py"

        # Step 1: Save initial workflow
        initial_code = WORKFLOW_CODE_TEMPLATE.format(
            name="Exec History Test",
            function_name="exec_history_workflow"
        )
        await storage.write_file(path, initial_code.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Register the workflow in DB (auto-discovery removed)
        workflow = await register_workflow_in_db(db_session, path, "exec_history_workflow")
        await db_session.commit()

        # Create a test user first (required for foreign key constraint)
        test_user = User(
            id=uuid4(),
            email=f"test_{uuid4().hex[:8]}@example.com",
            name="Test User",
            organization_id=PROVIDER_ORG_ID,
        )
        db_session.add(test_user)
        await db_session.flush()

        # Create an execution record
        # Execution uses workflow_name, not workflow_id
        # Database expects timezone-naive datetimes (TIMESTAMP WITHOUT TIME ZONE)
        from datetime import datetime, timezone
        from src.models.enums import ExecutionStatus
        now = datetime.now(timezone.utc)
        execution = Execution(
            id=uuid4(),
            workflow_name=workflow.function_name,
            status=ExecutionStatus.SUCCESS,
            started_at=now,
            completed_at=now,
            executed_by=test_user.id,
            executed_by_name="Test User",
        )
        db_session.add(execution)
        await db_session.commit()

        try:
            # Step 2: Try to remove workflow
            result = await storage.write_file(path, EMPTY_WORKFLOW_FILE.encode("utf-8"), updated_by="test")
            await db_session.commit()
        finally:
            # A leftover user makes the next registration non-first, which
            # breaks the session platform_admin fixture for later modules.
            await db_session.execute(delete(Execution).where(Execution.id == execution.id))
            await db_session.execute(delete(User).where(User.id == test_user.id))
            await db_session.commit()

        # Step 3: Assert execution history info
        assert result.pending_deactivations is not None
        assert len(result.pending_deactivations) == 1

        pending = result.pending_deactivations[0]
        assert pending.has_executions is True
        assert pending.last_execution_at is not None

    async def test_no_deactivation_protection_for_new_files(
        self,
        db_session: AsyncSession,
        clean_tables,
    ):
        """
        New files (no existing workflows) should not trigger deactivation protection.

        Scenario:
        1. Save a new workflow file to a path with no existing workflows
        2. Assert no pending_deactivations
        3. Register the workflow and verify it exists
        """
        storage = get_file_storage_service(db_session)
        path = "brand_new_file.py"

        # Verify no existing workflows at this path
        stmt = select(Workflow).where(Workflow.path == path)
        wf_result = await db_session.execute(stmt)
        assert wf_result.scalar_one_or_none() is None

        # Save new workflow file
        initial_code = WORKFLOW_CODE_TEMPLATE.format(
            name="Brand New Workflow",
            function_name="brand_new_workflow"
        )
        result = await storage.write_file(path, initial_code.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # No pending deactivations for new files
        assert result.pending_deactivations is None or len(result.pending_deactivations) == 0

        # Register the workflow in DB (auto-discovery removed)
        workflow = await register_workflow_in_db(db_session, path, "brand_new_workflow")
        await db_session.commit()

        # Workflow was created and enriched
        assert workflow is not None
        assert workflow.function_name == "brand_new_workflow"

    async def test_adding_new_workflow_does_not_trigger_deactivation(
        self,
        db_session: AsyncSession,
        clean_tables,
    ):
        """
        Adding a new workflow to an existing file should not trigger protection.

        Scenario:
        1. Save file with one workflow and register it
        2. Add a second workflow to the file
        3. Assert no pending_deactivations (original_workflow still present)
        4. Register the new workflow and verify both exist
        """
        storage = get_file_storage_service(db_session)
        path = "test_add_workflow.py"

        # Step 1: Save file with one workflow
        initial_code = WORKFLOW_CODE_TEMPLATE.format(
            name="Original Workflow",
            function_name="original_workflow"
        )
        await storage.write_file(path, initial_code.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Register the workflow in DB (auto-discovery removed)
        await register_workflow_in_db(db_session, path, "original_workflow")
        await db_session.commit()

        # Step 2: Add second workflow (keep the original)
        two_workflows = '''"""File with two workflows."""
from bifrost import workflow

@workflow(name="Original Workflow")
def original_workflow():
    """Original workflow."""
    pass

@workflow(name="New Workflow")
def new_workflow():
    """Newly added workflow."""
    pass
'''
        result = await storage.write_file(path, two_workflows.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # No pending deactivations when adding workflows
        assert result.pending_deactivations is None or len(result.pending_deactivations) == 0

        # Register the new workflow
        await register_workflow_in_db(db_session, path, "new_workflow")
        await db_session.commit()

        # Both workflows should exist and be active
        stmt = select(Workflow).where(Workflow.path == path, Workflow.is_active == True)  # noqa: E712
        wf_result = await db_session.execute(stmt)
        workflows = list(wf_result.scalars().all())

        assert len(workflows) == 2
        func_names = {wf.function_name for wf in workflows}
        assert func_names == {"original_workflow", "new_workflow"}

    async def test_data_provider_deactivation_protection(
        self,
        db_session: AsyncSession,
        clean_tables,
    ):
        """
        Data providers should also have deactivation protection.

        Scenario:
        1. Save file with @data_provider decorator and register it
        2. Verify data provider is created in DB
        3. Remove the data provider
        4. Assert pending_deactivations includes the data provider
        """
        storage = get_file_storage_service(db_session)
        path = "test_data_provider.py"

        # Step 1: Save data provider file
        dp_code = '''"""Sample data provider file."""
from bifrost import data_provider

@data_provider(name="Test Provider")
def test_provider():
    """A test data provider."""
    return []
'''
        result = await storage.write_file(path, dp_code.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Register the data provider in DB (auto-discovery removed)
        dp = await register_workflow_in_db(db_session, path, "test_provider", wf_type="data_provider")
        await db_session.commit()

        # Step 2: Verify data provider was created
        assert dp is not None
        assert dp.function_name == "test_provider"
        assert dp.type == "data_provider"

        # Step 3: Remove the data provider
        result = await storage.write_file(path, EMPTY_WORKFLOW_FILE.encode("utf-8"), updated_by="test")
        await db_session.commit()

        # Step 4: Assert pending deactivation for data provider
        assert result.pending_deactivations is not None
        assert len(result.pending_deactivations) == 1

        pending = result.pending_deactivations[0]
        assert pending.function_name == "test_provider"
        assert pending.decorator_type == "data_provider"
