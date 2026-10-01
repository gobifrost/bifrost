"""The plain-language next step returned with every page."""

from __future__ import annotations


def guidance(
    *,
    returned: int,
    seen_before: int,
    files: int,
    has_more: bool,
    output_mode: str,
    next_cursor: str | None,
    scope_label: str,
) -> str:
    unit = "files" if output_mode == "files" else "matches"
    if returned == 0 and not has_more:
        if seen_before:
            return f"Complete: no further {unit} after the first {seen_before}. There are no more results."
        return (
            f"No matches in {scope_label}. Check spelling, set is_regex for patterns, "
            "or widen include_pattern/source."
        )
    span = f"{unit} {seen_before + 1}-{seen_before + returned}"
    where = "" if output_mode == "files" else f" across {files} file{'s' if files != 1 else ''}"
    if not has_more:
        return f"Complete: showing {span}{where} in {scope_label}. There are no more results."
    return (
        f"Showing {span}{where} in {scope_label}; more results exist. To get the next page, "
        f'repeat this exact search with cursor="{next_cursor}". To narrow instead, set '
        'include_pattern or solution_id, or use output_mode="files" to list matching files first.'
    )
