"""
File content search for browser-based code editor.
Provides fast full-text search with regex support.
Platform admin resource - no org scoping.

Search queries the database directly via the ``file_index`` table for code,
modules, and any other text files persisted under ``_repo/``. Form and agent
content is no longer stored as per-UUID YAML — it lives in the manifest and is
not part of the editor search surface.
"""

import re
import time
import logging
import importlib
from typing import Any, List

import regex as bounded_regex
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import SearchRequest, SearchResponse, SearchResult
from src.models.orm.file_index import FileIndex

logger = logging.getLogger(__name__)

# Maximum results per entity type to prevent overwhelming queries
MAX_RESULTS_PER_TYPE = 500
MAX_REGEX_PATTERN_LENGTH = 512
REGEX_SEARCH_TIMEOUT_SECONDS = 0.05
_REGEX_PARSER = importlib.import_module("re._parser")
_REPEAT_OPS = {"MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"}


def _op_name(op: object) -> str:
    """Return a stable name for a regex parser opcode."""
    return str(getattr(op, "name", op))


def _repeat_child(arg: Any) -> list[Any]:
    return list(arg[2])


def _repeat_is_optional(arg: Any) -> bool:
    return arg[0] == 0 and arg[1] == 1


def _subpattern_child(arg: Any) -> list[Any]:
    return list(arg[-1])


def _branch_children(arg: Any) -> list[list[Any]]:
    return [list(branch) for branch in arg[1]]


def _unwrap_subpatterns(tokens: list[Any]) -> list[Any]:
    while len(tokens) == 1 and _op_name(tokens[0][0]) == "SUBPATTERN":
        tokens = _subpattern_child(tokens[0][1])
    return tokens


def _regex_tokens_are_single_repeat(tokens: list[Any]) -> bool:
    tokens = _unwrap_subpatterns(tokens)
    return len(tokens) == 1 and _op_name(tokens[0][0]) in _REPEAT_OPS


def _token_prefix_signature(tokens: list[Any]) -> object:
    tokens = _unwrap_subpatterns(tokens)
    if not tokens:
        return ("EMPTY",)
    op, arg = tokens[0]
    op_name = _op_name(op)
    if op_name == "LITERAL":
        return ("LITERAL", arg)
    if op_name == "IN":
        return ("IN", tuple(arg))
    if op_name in _REPEAT_OPS:
        return ("REPEAT", _token_prefix_signature(_repeat_child(arg)))
    return (op_name,)


def _branch_has_overlapping_alternatives(branches: list[list[Any]]) -> bool:
    seen: set[object] = set()
    for branch in branches:
        signature = _token_prefix_signature(branch)
        if signature in seen:
            return True
        seen.add(signature)
    return False


def _regex_tokens_have_overlapping_branch(tokens: list[Any]) -> bool:
    for op, arg in tokens:
        op_name = _op_name(op)
        if op_name == "BRANCH" and _branch_has_overlapping_alternatives(
            _branch_children(arg)
        ):
            return True
        if op_name == "SUBPATTERN" and _regex_tokens_have_overlapping_branch(
            _subpattern_child(arg)
        ):
            return True
        if op_name in _REPEAT_OPS and _regex_tokens_have_overlapping_branch(
            _repeat_child(arg)
        ):
            return True
    return False


def _regex_tokens_have_risky_repeat(tokens: list[Any]) -> bool:
    for op, arg in tokens:
        op_name = _op_name(op)
        if op_name in _REPEAT_OPS:
            child = _repeat_child(arg)
            if (
                (_regex_tokens_are_single_repeat(child) and not _repeat_is_optional(arg))
                or _regex_tokens_have_overlapping_branch(child)
            ):
                return True
            if _regex_tokens_have_risky_repeat(child):
                return True
        elif op_name == "SUBPATTERN" and _regex_tokens_have_risky_repeat(
            _subpattern_child(arg)
        ):
            return True
        elif op_name == "BRANCH" and any(
            _regex_tokens_have_risky_repeat(branch)
            for branch in _branch_children(arg)
        ):
            return True
    return False


def _validate_regex_pattern(pattern: str) -> None:
    """Reject regex patterns that are too large or likely to cause backtracking."""
    if len(pattern) > MAX_REGEX_PATTERN_LENGTH:
        raise ValueError(
            f"Regex pattern exceeds {MAX_REGEX_PATTERN_LENGTH} characters"
        )
    tokens = list(_REGEX_PARSER.parse(pattern))
    if _regex_tokens_have_risky_repeat(tokens):
        raise ValueError("Regex pattern uses nested quantifiers")


def _search_content(
    content: str,
    path: str,
    query: str,
    case_sensitive: bool,
    is_regex: bool,
) -> List[SearchResult]:
    """
    Search content string for matches.

    Args:
        content: Text content to search
        path: File path for results
        query: Search query (text or regex pattern)
        case_sensitive: Whether to match case-sensitively
        is_regex: Whether query is a regex pattern

    Returns:
        List of SearchResult objects
    """
    results: List[SearchResult] = []

    try:
        # Build regex pattern
        if is_regex:
            pattern = query
            _validate_regex_pattern(pattern)
        else:
            # Escape special regex characters for literal search
            pattern = re.escape(query)

        # Compile regex with appropriate flags. Literal search stays on the
        # stdlib engine with re.escape(); explicit regex mode uses a
        # timeout-capable engine to bound user-provided pattern execution.
        flags = 0 if case_sensitive else re.IGNORECASE
        if is_regex:
            regex = bounded_regex.compile(pattern, flags)
        else:
            regex = re.compile(pattern, flags)

        # Split into lines
        lines = content.split('\n')

        # Search each line
        for line_num, line in enumerate(lines, start=1):
            # Find all matches in this line
            if is_regex:
                matches = regex.finditer(
                    line,
                    timeout=REGEX_SEARCH_TIMEOUT_SECONDS,
                )
            else:
                matches = regex.finditer(line)

            for match in matches:
                # Get context lines (previous and next)
                context_before = lines[line_num - 2] if line_num > 1 else None
                context_after = lines[line_num] if line_num < len(lines) else None

                results.append(SearchResult(
                    file_path=path,
                    line=line_num,
                    column=match.start(),
                    match_text=line,
                    context_before=context_before,
                    context_after=context_after
                ))

    except TimeoutError as e:
        logger.warning(f"Regex search timed out in {path}: {e}")
    except (bounded_regex.error, re.error) as e:
        logger.warning(f"Error searching {path}: {e}")

    return results


async def search_files_db(
    db: AsyncSession,
    request: SearchRequest,
    root_path: str = ""
) -> SearchResponse:
    """
    Search files for content matching the query using database queries.

    Searches the file_index for workflow Python code, module Python code, and
    any other indexed text content.

    Args:
        db: Database session
        request: SearchRequest with query and options
        root_path: Path prefix filter (empty = all files)

    Returns:
        SearchResponse with results and metadata

    Raises:
        ValueError: If query is invalid regex
    """
    start_time = time.time()

    # Validate regex if enabled
    if request.is_regex:
        try:
            _validate_regex_pattern(request.query)
            flags = 0 if request.case_sensitive else re.IGNORECASE
            bounded_regex.compile(request.query, flags)
        except ValueError as e:
            raise ValueError(f"Invalid regex pattern: {str(e)}") from e
        except (bounded_regex.error, re.error) as e:
            raise ValueError(f"Invalid regex pattern: {str(e)}") from e

    all_results: List[SearchResult] = []
    files_searched = 0

    # Build file pattern filter if specified
    like_pattern = None
    if request.include_pattern:
        # Convert glob pattern to SQL LIKE pattern
        # e.g., "**/*.py" -> "%.py", "workflows/*.py" -> "workflows/%.py"
        like_pattern = request.include_pattern.replace("**/*", "%").replace("**", "%").replace("*", "%")

    # 1. Search all code files via file_index (workflows, modules, all Python)
    fi_conditions = [
        FileIndex.content.isnot(None),
    ]
    if root_path:
        fi_conditions.append(FileIndex.path.like(f"{root_path}%"))
    if like_pattern:
        fi_conditions.append(FileIndex.path.like(like_pattern))
    code_stmt = (
        select(FileIndex.path, FileIndex.content)
        .where(*fi_conditions)
        .limit(MAX_RESULTS_PER_TYPE)
    )
    code_result = await db.execute(code_stmt)
    for row in code_result:
        files_searched += 1
        if row.content:
            results = _search_content(
                row.content,
                row.path,
                request.query,
                request.case_sensitive,
                request.is_regex,
            )
            all_results.extend(results)
            if len(all_results) >= request.max_results:
                break

    # Truncate results if needed
    truncated = len(all_results) > request.max_results
    results = all_results[:request.max_results]

    # Calculate search time
    search_time_ms = int((time.time() - start_time) * 1000)

    return SearchResponse(
        query=request.query,
        total_matches=len(results),
        files_searched=files_searched,
        results=results,
        truncated=truncated,
        search_time_ms=search_time_ms
    )
