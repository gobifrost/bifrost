"""
GitHub integration contract models for Bifrost.
"""

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ==================== GIT & GITHUB MODELS ====================


class GitFileStatus(str, Enum):
    """Git file status"""
    MODIFIED = "M"      # Modified file
    ADDED = "A"         # New file (untracked or staged)
    DELETED = "D"       # Deleted file
    UNTRACKED = "U"     # Untracked file
    CONFLICTED = "C"    # File with merge conflicts


class FileChange(BaseModel):
    """Represents a changed file in Git"""
    path: str = Field(..., description="Relative path from workspace root")
    status: GitFileStatus = Field(..., description="Git status of the file")
    additions: int | None = Field(default=None, description="Number of lines added")
    deletions: int | None = Field(default=None, description="Number of lines deleted")

    model_config = ConfigDict(from_attributes=True)


class ConflictInfo(BaseModel):
    """Information about conflicts in a file (no markers written to disk)"""
    file_path: str = Field(..., description="Relative path to conflicted file")
    current_content: str = Field(..., description="Local version of the file")
    incoming_content: str = Field(..., description="Remote version of the file")
    base_content: str | None = Field(default=None, description="Common ancestor version (if available)")

    model_config = ConfigDict(from_attributes=True)


class ValidateTokenRequest(BaseModel):
    """Request to validate GitHub token"""
    token: str = Field(..., description="GitHub personal access token to validate")

    model_config = ConfigDict(from_attributes=True)


class GitHubConfigRequest(BaseModel):
    """Request to configure GitHub integration - token must already be saved via /validate"""
    repo_url: str = Field(..., min_length=1, description="GitHub repository URL (e.g., https://github.com/user/repo)")
    branch: str = Field(default="main", description="Branch to sync with")

    model_config = ConfigDict(from_attributes=True)


class GitHubConfigResponse(BaseModel):
    """Response after configuring GitHub"""
    configured: bool = Field(..., description="Whether GitHub is fully configured")
    token_saved: bool = Field(default=False, description="Whether a GitHub token has been validated and saved")
    repo_url: str | None = Field(default=None, description="Configured repository URL")
    branch: str | None = Field(default=None, description="Configured branch")
    backup_path: str | None = Field(default=None, description="Path to backup directory if workspace was backed up")

    model_config = ConfigDict(from_attributes=True)


class GitHubRepoInfo(BaseModel):
    """GitHub repository information"""
    name: str = Field(..., description="Repository name (owner/repo)")
    full_name: str = Field(..., description="Full repository name")
    description: str | None = Field(default=None, description="Repository description")
    url: str = Field(..., description="Repository URL")
    private: bool = Field(..., description="Whether repository is private")

    model_config = ConfigDict(from_attributes=True)


class DetectedRepoInfo(BaseModel):
    """Information about auto-detected existing repository"""
    full_name: str = Field(..., description="Repository full name (owner/repo)")
    branch: str = Field(..., description="Current branch")

    model_config = ConfigDict(from_attributes=True)


class GitHubReposResponse(BaseModel):
    """Response with list of GitHub repositories"""
    repositories: list[GitHubRepoInfo] = Field(..., description="List of accessible repositories")
    detected_repo: DetectedRepoInfo | None = Field(default=None, description="Auto-detected existing repository")

    model_config = ConfigDict(from_attributes=True)


class GitHubBranchInfo(BaseModel):
    """GitHub branch information"""
    name: str = Field(..., description="Branch name")
    protected: bool = Field(..., description="Whether branch is protected")
    commit_sha: str = Field(..., description="Latest commit SHA")

    model_config = ConfigDict(from_attributes=True)


class GitHubBranchesResponse(BaseModel):
    """Response with list of branches"""
    branches: list[GitHubBranchInfo] = Field(..., description="List of branches in repository")

    model_config = ConfigDict(from_attributes=True)


class WorkspaceAnalysisResponse(BaseModel):
    """Response with workspace analysis - simplified for replace-only strategy"""
    workspace_status: Literal["empty", "has_files_no_git", "is_git_repo", "is_different_git_repo"] = Field(
        ...,
        description="Current state of the workspace directory"
    )
    file_count: int = Field(..., description="Number of files in workspace (excluding .git)")
    existing_remote: str | None = Field(default=None, description="URL of existing Git remote (if any)")
    requires_confirmation: bool = Field(..., description="Whether user needs to confirm replacing workspace")
    backup_will_be_created: bool = Field(default=True, description="Indicates a backup will be created before replacing")

    model_config = ConfigDict(from_attributes=True)


class CreateRepoRequest(BaseModel):
    """Request to create a new GitHub repository"""
    name: str = Field(..., min_length=1, description="Repository name")
    description: str | None = Field(default=None, description="Repository description")
    private: bool = Field(default=True, description="Whether repository should be private")
    organization: str | None = Field(default=None, description="Organization name (if creating in an org)")

    model_config = ConfigDict(from_attributes=True)


class GitHubConfigEntity(BaseModel):
    """GitHub integration configuration stored in Config table"""
    status: Literal["disconnected", "token_saved", "configured"] = Field(
        ...,
        description="Integration status: disconnected (inactive), token_saved (validated), configured (ready)"
    )
    token_config_key: str | None = Field(default=None, description="Config key containing the encrypted GitHub token")
    repo_url: str | None = Field(default=None, description="Configured repository URL")
    production_branch: str | None = Field(default=None, description="Production branch to sync with")
    updated_at: datetime | None = Field(default=None, description="Last update timestamp")
    updated_by: str | None = Field(default=None, description="User who last updated configuration")

    model_config = ConfigDict(from_attributes=True)


class CreateRepoResponse(BaseModel):
    """Response after creating a new repository"""
    full_name: str = Field(..., description="Full repository name (owner/repo)")
    url: str = Field(..., description="Repository URL")
    clone_url: str = Field(..., description="HTTPS clone URL")

    model_config = ConfigDict(from_attributes=True)


class FetchFromGitHubResponse(BaseModel):
    """Response after fetching from remote"""
    success: bool = Field(..., description="Whether fetch was successful")
    commits_ahead: int = Field(default=0, description="Number of local commits ahead of remote")
    commits_behind: int = Field(default=0, description="Number of commits behind remote")
    error: str | None = Field(default=None, description="Error message if fetch failed")

    model_config = ConfigDict(from_attributes=True)


class CommitAndPushRequest(BaseModel):
    """Request to commit and push changes"""
    message: str = Field(..., min_length=1, description="Commit message")

    model_config = ConfigDict(from_attributes=True)


class CommitAndPushResponse(BaseModel):
    """Response after commit and push"""
    success: bool = Field(..., description="Whether operation succeeded")
    commit_sha: str | None = Field(default=None, description="SHA of created commit")
    files_committed: int = Field(..., description="Number of files committed")
    error: str | None = Field(default=None, description="Error message if operation failed")

    model_config = ConfigDict(from_attributes=True)


class GitHubSetupResponse(BaseModel):
    """Response after configuring GitHub integration"""
    job_id: str | None = Field(default=None, description="Job ID for tracking the setup operation (deprecated)")
    notification_id: str | None = Field(default=None, description="Notification ID for watching progress via WebSocket (deprecated)")
    status: str = Field(default="configured", description="Configuration status")

    model_config = ConfigDict(from_attributes=True)




class CommitInfo(BaseModel):
    """Information about a single commit"""
    sha: str = Field(..., description="Commit SHA")
    message: str = Field(..., description="Commit message")
    author: str = Field(..., description="Commit author")
    timestamp: str = Field(..., description="ISO 8601 timestamp")
    is_pushed: bool = Field(..., description="Whether commit is pushed to remote")

    model_config = ConfigDict(from_attributes=True)


class RepoStatusResponse(BaseModel):
    """Fast repo status check for CLI push pre-check."""
    git_configured: bool = Field(..., description="Whether GitHub integration is configured")
    dirty: bool = Field(..., description="Whether platform has uncommitted changes")
    dirty_since: str | None = Field(default=None, description="ISO timestamp when repo became dirty")


class GitRefreshStatusResponse(BaseModel):
    """
    Unified response after fetching and getting complete Git status.
    This combines fetch + status + commit history into a single response.
    """
    success: bool = Field(..., description="Whether refresh was successful")
    initialized: bool = Field(..., description="Whether Git repository is initialized")
    configured: bool = Field(..., description="Whether GitHub integration is configured")
    current_branch: str | None = Field(default=None, description="Current branch name")

    # Local changes
    changed_files: list[FileChange] = Field(default_factory=list, description="List of locally changed files")
    conflicts: list[ConflictInfo] = Field(default_factory=list, description="List of merge conflicts")
    merging: bool = Field(default=False, description="Whether repository is in merge state (MERGE_HEAD exists)")

    # Remote sync status
    commits_ahead: int = Field(default=0, description="Number of local commits ahead of remote (ready to push)")
    commits_behind: int = Field(default=0, description="Number of commits behind remote (ready to pull)")

    # Commit history
    commit_history: list[CommitInfo] = Field(default_factory=list, description="Recent commit history with pushed/unpushed status")

    # Metadata
    last_synced: str | None = Field(default=None, description="ISO timestamp of last successful sync")
    error: str | None = Field(default=None, description="Error message if sync failed")

    model_config = ConfigDict(from_attributes=True)


class CommitHistoryResponse(BaseModel):
    """Response with commit history and pagination"""
    commits: list[CommitInfo] = Field(default_factory=list, description="List of commits (newest first)")
    total_commits: int = Field(..., description="Total number of commits in the entire history")
    has_more: bool = Field(..., description="Whether there are more commits to load")

    model_config = ConfigDict(from_attributes=True)


class WorkflowReference(BaseModel):
    """A reference to an entity that uses a workflow."""
    type: str = Field(..., description="Entity type (form, app, agent)")
    id: str = Field(..., description="Entity ID")
    name: str = Field(..., description="Entity name")

    model_config = ConfigDict(from_attributes=True)


class OrphanInfo(BaseModel):
    """Information about a workflow that will become orphaned."""
    workflow_id: str = Field(..., description="Workflow UUID")
    workflow_name: str = Field(..., description="Workflow display name")
    function_name: str = Field(..., description="Python function name")
    last_path: str = Field(..., description="Last known file path")
    used_by: list[WorkflowReference] = Field(
        default_factory=list,
        description="Entities using this workflow"
    )

    model_config = ConfigDict(from_attributes=True)


class PreflightIssue(BaseModel):
    """A single issue found during preflight validation."""
    path: str = Field(...)
    line: int | None = Field(default=None)
    message: str = Field(...)
    severity: Literal["error", "warning"] = Field(...)
    category: Literal["syntax", "lint", "ref", "orphan", "manifest", "health"] = Field(...)
    fix_hint: str | None = Field(default=None, description="Actionable guidance for resolving this issue")
    auto_fixable: bool = Field(default=False, description="Whether this can be auto-fixed via cleanup endpoint")

    model_config = ConfigDict(from_attributes=True)


class PreflightResult(BaseModel):
    """Result of preflight validation."""
    valid: bool = Field(...)
    issues: list[PreflightIssue] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class GitJobResponse(BaseModel):
    """Response when a git operation is queued as a background job."""
    job_id: str = Field(..., description="Job ID for tracking progress via WebSocket")
    status: str = Field(default="queued", description="Job status")

    model_config = ConfigDict(from_attributes=True)


class GitOpRequest(BaseModel):
    """Base request for git operations. Accepts optional client-generated job_id."""
    job_id: str | None = Field(default=None, description="Client-generated job ID (avoids WebSocket race condition)")

    model_config = ConfigDict(from_attributes=True)


class SyncRequest(GitOpRequest):
    """Request to sync (pull + push + entity import)."""
    confirm_deletes: bool = Field(default=False, description="Confirm pending entity deletions and proceed with sync")


class CommitRequest(GitOpRequest):
    """Request to commit working tree changes."""
    message: str = Field(..., min_length=1, description="Commit message")


class ResolveRequest(GitOpRequest):
    """Request to resolve merge conflicts after a failed pull."""
    resolutions: dict[str, Literal["ours", "theirs"]] = Field(
        ..., description="Map of file path to resolution strategy"
    )


class DiffRequest(GitOpRequest):
    """Request to get a file diff."""
    path: str = Field(..., min_length=1, description="File path to diff")


class ChangedFile(BaseModel):
    """A file changed in the working tree (git status)."""
    path: str = Field(..., description="Relative path from workspace root")
    change_type: Literal["added", "modified", "deleted", "renamed", "untracked"] = Field(
        ..., description="Type of change"
    )
    display_name: str | None = Field(default=None, description="Human-readable entity name")
    entity_type: str | None = Field(default=None, description="Entity type: form, agent, app, workflow")

    model_config = ConfigDict(from_attributes=True)


class MergeConflict(BaseModel):
    """A file with a merge conflict after a failed pull."""
    path: str = Field(..., description="Relative path to conflicted file")
    ours_content: str | None = Field(default=None, description="Our (platform) version")
    theirs_content: str | None = Field(default=None, description="Their (git remote) version")
    display_name: str | None = Field(default=None, description="Human-readable entity name")
    entity_type: str | None = Field(default=None, description="Entity type")
    conflict_type: str | None = Field(default=None, description="both_modified, both_added, deleted_by_us, deleted_by_them")

    model_config = ConfigDict(from_attributes=True)


class FetchResult(BaseModel):
    """Result of a git fetch operation."""
    success: bool = Field(..., description="Whether fetch succeeded")
    commits_ahead: int = Field(default=0, description="Local commits ahead of remote")
    commits_behind: int = Field(default=0, description="Commits behind remote")
    remote_branch_exists: bool = Field(default=True, description="Whether the remote branch exists")
    error: str | None = Field(default=None, description="Error message if failed")

    model_config = ConfigDict(from_attributes=True)


class WorkingTreeStatus(BaseModel):
    """Working tree status (uncommitted changes)."""
    changed_files: list[ChangedFile] = Field(default_factory=list, description="Changed files")
    total_changes: int = Field(default=0, description="Total number of changes")
    conflicts: list[MergeConflict] = Field(default_factory=list, description="Unresolved merge/stash conflicts")
    commits_ahead: int = Field(default=0, description="Local commits ahead of remote")
    commits_behind: int = Field(default=0, description="Commits behind remote")
    merging: bool = Field(default=False, description="Whether repository is in merge state (MERGE_HEAD exists)")

    model_config = ConfigDict(from_attributes=True)


class EntityChange(BaseModel):
    """A single entity-level change tracked during commit or sync."""
    action: Literal["added", "updated", "removed", "keep"] = Field(..., description="Type of change")
    entity_type: str = Field(..., description="Entity type: workflow, form, agent, app, integration, config, table, event, organization, role")
    name: str = Field(..., description="Entity display name")
    path: str | None = Field(default=None, description="File path (for file-backed entities)")
    reason: str | None = Field(default=None, description="Reason for removal, e.g. 'file not found: workflows/contacts.py'")

    model_config = ConfigDict(from_attributes=True)


class CommitResult(BaseModel):
    """Result of a git commit operation."""
    success: bool = Field(..., description="Whether commit succeeded")
    commit_sha: str | None = Field(default=None, description="SHA of created commit")
    files_committed: int = Field(default=0, description="Number of files committed")
    error: str | None = Field(default=None, description="Error message if failed")
    preflight: PreflightResult | None = Field(default=None, description="Preflight validation result")
    entity_changes: list[EntityChange] = Field(default_factory=list, description="Entity-level changes during manifest regeneration")

    model_config = ConfigDict(from_attributes=True)


class PullResult(BaseModel):
    """Result of a git pull operation."""
    success: bool = Field(..., description="Whether pull succeeded")
    pulled: int = Field(default=0, description="Number of entities imported")
    commit_sha: str | None = Field(default=None, description="New HEAD commit SHA")
    conflicts: list[MergeConflict] = Field(default_factory=list, description="Merge conflicts if any")
    error: str | None = Field(default=None, description="Error message if failed")

    model_config = ConfigDict(from_attributes=True)


class PushResult(BaseModel):
    """Result of a git push operation."""
    success: bool = Field(..., description="Whether push succeeded")
    commit_sha: str | None = Field(default=None, description="Latest pushed commit SHA")
    pushed_commits: int = Field(default=0, description="Number of commits pushed")
    error: str | None = Field(default=None, description="Error message if failed")

    model_config = ConfigDict(from_attributes=True)


class ResolveResult(BaseModel):
    """Result of conflict resolution."""
    success: bool = Field(..., description="Whether resolution succeeded")
    pulled: int = Field(default=0, description="Number of entities imported after resolution")
    commits_ahead: int = Field(default=0, description="Local commits ahead of remote after resolve")
    commits_behind: int = Field(default=0, description="Commits behind remote after resolve")
    error: str | None = Field(default=None, description="Error message if failed")

    model_config = ConfigDict(from_attributes=True)


class SyncResult(BaseModel):
    """Result of a combined sync (pull + push) operation."""
    success: bool = Field(..., description="Whether sync succeeded")
    pull_success: bool = Field(default=True, description="Whether pull phase succeeded")
    push_success: bool = Field(default=True, description="Whether push phase succeeded")
    pulled: int = Field(default=0, description="Number of entities imported")
    pushed_commits: int = Field(default=0, description="Number of commits pushed")
    commit_sha: str | None = Field(default=None, description="Latest commit SHA after sync")
    conflicts: list[MergeConflict] = Field(default_factory=list, description="Merge conflicts if any")
    entities_imported: int = Field(default=0, description="Number of entities imported during sync")
    error: str | None = Field(default=None, description="Error message if failed")
    entity_changes: list[EntityChange] = Field(default_factory=list, description="Entity-level changes during sync")
    needs_delete_confirmation: bool = Field(default=False, description="Whether sync is blocked pending delete confirmation")
    pending_deletes: list[EntityChange] = Field(default_factory=list, description="Entities that will be deleted if confirmed")

    model_config = ConfigDict(from_attributes=True)


class AbortMergeResult(BaseModel):
    """Result of aborting a merge."""
    success: bool = Field(..., description="Whether abort succeeded")
    error: str | None = Field(default=None, description="Error message if failed")

    model_config = ConfigDict(from_attributes=True)


class DiffResult(BaseModel):
    """Result of a file diff operation."""
    path: str = Field(..., description="File path")
    head_content: str | None = Field(default=None, description="Content at HEAD (committed)")
    working_content: str | None = Field(default=None, description="Content in working tree")

    model_config = ConfigDict(from_attributes=True)


class DiscardRequest(GitOpRequest):
    """Request to discard working tree changes for specific files."""
    paths: list[str] = Field(..., min_length=1, description="File paths to discard changes for")


class DiscardResult(BaseModel):
    """Result of discarding working tree changes."""
    success: bool = Field(..., description="Whether discard succeeded")
    discarded: list[str] = Field(default_factory=list, description="Paths that were discarded")
    entity_changes: list[EntityChange] = Field(default_factory=list, description="Entity-level changes during re-import")
    error: str | None = Field(default=None, description="Error message if failed")

    model_config = ConfigDict(from_attributes=True)


class ConfirmDeletionsRequest(BaseModel):
    """Request to confirm or skip pending entity deletions after sync."""
    token: str = Field(..., description="Deletion token from SyncResult")

    model_config = ConfigDict(from_attributes=True)


class ConfirmDeletionsResult(BaseModel):
    """Result of confirming or skipping pending deletions."""
    success: bool = Field(..., description="Whether operation succeeded")
    deleted_count: int = Field(default=0, description="Number of entities deleted")
    entity_changes: list[EntityChange] = Field(default_factory=list, description="Entities that were deleted")
    error: str | None = Field(default=None, description="Error message if failed")

    model_config = ConfigDict(from_attributes=True)


class SyncPreviewRequest(BaseModel):
    """Request body for sync preview (currently empty, reserved for future filters)."""
    pass


class SyncExecuteRequest(BaseModel):
    """Request to execute a sync with conflict resolutions."""
    conflict_resolutions: dict[str, str] = Field(default_factory=dict)
    confirm_orphans: bool = False


# ==================== ORPHAN MANAGEMENT MODELS ====================


class OrphanedWorkflowInfo(BaseModel):
    """Orphaned workflow with metadata and references."""

    id: str = Field(..., description="Workflow UUID")
    name: str = Field(..., description="Workflow display name")
    function_name: str = Field(..., description="Python function name")
    last_path: str = Field(..., description="Last known file path")
    code: str | None = Field(default=None, description="Stored code snapshot")
    used_by: list[WorkflowReference] = Field(
        default_factory=list,
        description="Entities using this workflow"
    )
    orphaned_at: datetime | None = Field(default=None, description="When workflow became orphaned")

    model_config = ConfigDict(from_attributes=True)


class OrphanedWorkflowsResponse(BaseModel):
    """Response containing list of orphaned workflows."""

    workflows: list[OrphanedWorkflowInfo] = Field(
        default_factory=list,
        description="List of orphaned workflows"
    )

    model_config = ConfigDict(from_attributes=True)


class CompatibleReplacement(BaseModel):
    """Potential replacement for an orphaned workflow."""

    path: str = Field(..., description="File path containing the replacement function")
    function_name: str = Field(..., description="Function name")
    signature: str = Field(..., description="Human-readable function signature")
    compatibility: Literal["exact", "compatible"] = Field(
        ...,
        description="Compatibility level: exact (perfect match) or compatible (can be used)"
    )

    model_config = ConfigDict(from_attributes=True)


class CompatibleReplacementsResponse(BaseModel):
    """Response containing compatible replacements for an orphaned workflow."""

    replacements: list[CompatibleReplacement] = Field(
        default_factory=list,
        description="List of compatible replacement functions"
    )

    model_config = ConfigDict(from_attributes=True)


class ReplaceWorkflowRequest(BaseModel):
    """Request to replace an orphaned workflow with content from existing file."""

    source_path: str = Field(..., min_length=1, description="Path to file containing replacement function")
    function_name: str = Field(..., min_length=1, description="Name of function to use as replacement")
    allow_type_change: bool = Field(
        default=False,
        description="Allow the decorator type to change (workflow/tool/data_provider). Default false to prevent silent form-binding breakage.",
    )

    model_config = ConfigDict(from_attributes=True)


class ReplaceWorkflowResponse(BaseModel):
    """Response after replacing an orphaned workflow."""

    success: bool = Field(..., description="Whether replacement succeeded")
    workflow_id: str = Field(..., description="UUID of the updated workflow")
    new_path: str = Field(..., description="New file path for the workflow")

    model_config = ConfigDict(from_attributes=True)


class RemapWorkflowRequest(BaseModel):
    """Request to move references from one workflow row to another."""

    target_workflow_id: str = Field(
        ...,
        min_length=1,
        description="Active workflow UUID that should receive the references",
    )

    model_config = ConfigDict(from_attributes=True)


class RemapWorkflowResponse(BaseModel):
    """Response after remapping workflow references."""

    success: bool = Field(..., description="Whether remap succeeded")
    source_workflow_id: str = Field(..., description="UUID references were moved from")
    target_workflow_id: str = Field(..., description="UUID references were moved to")
    updated: dict[str, int] = Field(
        default_factory=dict,
        description="Counts of references updated by category",
    )

    model_config = ConfigDict(from_attributes=True)


class RecreateFileResponse(BaseModel):
    """Response after recreating file from orphaned workflow's code."""

    success: bool = Field(..., description="Whether file recreation succeeded")
    workflow_id: str = Field(..., description="UUID of the workflow")
    path: str = Field(..., description="Path where file was recreated")

    model_config = ConfigDict(from_attributes=True)


class DeactivateWorkflowResponse(BaseModel):
    """Response after deactivating an orphaned workflow."""

    success: bool = Field(..., description="Whether deactivation succeeded")
    workflow_id: str = Field(..., description="UUID of the deactivated workflow")
    warning: str | None = Field(
        default=None,
        description="Warning message if workflow is still referenced by other entities"
    )

    model_config = ConfigDict(from_attributes=True)
