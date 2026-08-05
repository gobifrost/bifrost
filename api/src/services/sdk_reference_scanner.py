"""
SDK Reference Scanner Service

Scans Python files for SDK calls (config.get, integrations.get) and validates
them against the database to identify missing configurations or integrations.

Used to create platform admin notifications when workflows reference
configs or integrations that don't exist.
"""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.log_safety import log_safe
from src.models.orm import Config, Integration, IntegrationMapping

logger = logging.getLogger(__name__)

# Regex patterns for SDK calls
# Matches: config.get("key") but NOT config.get("key", "default")
# The negative lookahead (?!\s*,) ensures we skip calls with a default value
CONFIG_PATTERN = re.compile(
    r'''(?:await\s+)?config\.get\s*\(\s*["']([^"']+)["']\s*(?!\s*,)\)''',
    re.MULTILINE
)

# Matches: integrations.get("name"), await integrations.get("name"), etc.
# integrations.get doesn't support default values, so no special handling needed
INTEGRATIONS_PATTERN = re.compile(
    r'''(?:await\s+)?integrations\.get\s*\(\s*["']([^"']+)["']''',
    re.MULTILINE
)


@dataclass
class SDKIssue:
    """Represents a missing SDK reference found in code."""

    file_path: str
    line_number: int
    issue_type: Literal["config", "integration"]
    key: str  # The missing config key or integration name


class SDKReferenceScanner:
    """
    Scans Python files for SDK usage and validates against stored data.

    Performs global validation (not org-scoped) since any org could run the code:
    - config.get("X") is valid if "X" exists in Config table for any org
    - integrations.get("Y") is valid if "Y" has any mapping in IntegrationMapping

    Only flags issues when the key/name doesn't exist at all in the system.
    """

    def __init__(self, db: AsyncSession):
        """Initialize scanner with database session."""
        self.db = db

    def extract_references(self, content: str) -> tuple[set[str], set[str]]:
        """
        Extract SDK references from Python code, ignoring commented lines.

        Args:
            content: Python file content

        Returns:
            Tuple of (config_keys, integration_names)
        """
        config_keys: set[str] = set()
        integration_names: set[str] = set()

        for line in content.split('\n'):
            # Find position of # that starts a comment (not inside a string)
            comment_pos = self._find_comment_position(line)

            # Check for config.get matches before comment
            for match in CONFIG_PATTERN.finditer(line):
                if comment_pos is None or match.start() < comment_pos:
                    config_keys.add(match.group(1))

            # Check for integrations.get matches before comment
            for match in INTEGRATIONS_PATTERN.finditer(line):
                if comment_pos is None or match.start() < comment_pos:
                    integration_names.add(match.group(1))

        return config_keys, integration_names

    def _find_comment_position(self, line: str) -> int | None:
        """
        Find the position of a # that starts a comment (not inside a string).

        Args:
            line: A single line of Python code

        Returns:
            Position of comment start, or None if no comment exists
        """
        in_single_quote = False
        in_double_quote = False
        i = 0
        while i < len(line):
            char = line[i]
            # Handle escape sequences
            if char == '\\' and i + 1 < len(line):
                i += 2
                continue
            # Toggle string state
            if char == "'" and not in_double_quote:
                in_single_quote = not in_single_quote
            elif char == '"' and not in_single_quote:
                in_double_quote = not in_double_quote
            # Check for comment outside strings
            elif char == '#' and not in_single_quote and not in_double_quote:
                return i
            i += 1
        return None

    async def get_all_config_keys(self) -> set[str]:
        """Get all config keys that exist in the system (any org or global)."""
        stmt = select(Config.key).distinct()
        result = await self.db.execute(stmt)
        return {row[0] for row in result.fetchall()}

    async def get_all_mapped_integrations(self) -> set[str]:
        """Get all integration names that have at least one org mapping."""
        stmt = (
            select(Integration.name)
            .distinct()
            .join(IntegrationMapping, Integration.id == IntegrationMapping.integration_id)
        )
        result = await self.db.execute(stmt)
        return {row[0] for row in result.fetchall()}

    def _find_line_number(self, lines: list[str], call_type: str, key: str) -> int:
        """
        Find the line number where an SDK call occurs.

        Args:
            lines: List of lines in the file
            call_type: Either "config.get" or "integrations.get"
            key: The key/name being looked up

        Returns:
            Line number (1-indexed), or 1 if not found
        """
        # Build pattern to find the specific call
        pattern = re.compile(rf'{call_type}\s*\(\s*["\']' + re.escape(key) + r'["\']')

        for i, line in enumerate(lines, start=1):
            if pattern.search(line):
                return i

        return 1  # Default to line 1 if not found

    async def scan_file(self, path: str, content: str) -> list[SDKIssue]:
        """
        Scan a file and return list of missing SDK references.

        Args:
            path: Relative file path (for reporting)
            content: Python file content

        Returns:
            List of SDKIssue for any missing references
        """
        config_refs, integration_refs = self.extract_references(content)

        # No references found - nothing to validate
        if not config_refs and not integration_refs:
            return []

        issues: list[SDKIssue] = []
        lines = content.split('\n')

        # Validate config references
        if config_refs:
            valid_configs = await self.get_all_config_keys()
            missing_configs = config_refs - valid_configs
            for key in missing_configs:
                line_num = self._find_line_number(lines, 'config.get', key)
                issues.append(SDKIssue(
                    file_path=path,
                    line_number=line_num,
                    issue_type="config",
                    key=key,
                ))
                logger.debug(
                    f"Missing config reference: {log_safe(key)} at "
                    f"{log_safe(path)}:{line_num}"
                )

        # Validate integration references
        if integration_refs:
            valid_integrations = await self.get_all_mapped_integrations()
            missing_integrations = integration_refs - valid_integrations
            for name in missing_integrations:
                line_num = self._find_line_number(lines, 'integrations.get', name)
                issues.append(SDKIssue(
                    file_path=path,
                    line_number=line_num,
                    issue_type="integration",
                    key=name,
                ))
                logger.debug(
                    f"Missing integration reference: {log_safe(name)} at "
                    f"{log_safe(path)}:{line_num}"
                )

        if issues:
            logger.info(
                f"Found {len(issues)} SDK issue(s) in {log_safe(path)}"
            )

        return issues

    async def scan_workspace(self, workspace_path: Path) -> list[SDKIssue]:
        """
        Scan all Python files in workspace for missing SDK references.

        Args:
            workspace_path: Path to workspace directory

        Returns:
            List of all SDKIssue found across all files
        """
        all_issues: list[SDKIssue] = []

        if not workspace_path.exists():
            logger.warning(f"Workspace path does not exist: {workspace_path}")
            return all_issues

        # Find all Python files, excluding __pycache__ and hidden dirs
        for py_file in workspace_path.rglob("*.py"):
            # Skip pycache and hidden directories
            if "__pycache__" in str(py_file) or any(
                part.startswith('.') for part in py_file.parts
            ):
                continue

            try:
                content = py_file.read_text(encoding='utf-8')
                relative_path = str(py_file.relative_to(workspace_path))
                issues = await self.scan_file(relative_path, content)
                all_issues.extend(issues)
            except (OSError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to read {py_file}: {e}")

        logger.info(
            f"Workspace scan complete: scanned {workspace_path}, "
            f"found {len(all_issues)} issue(s)"
        )
        return all_issues
