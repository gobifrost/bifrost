"""Sync TUI — shows file and/or entity changes in an interactive list.

Users cycle actions (Push/Pull/Delete/Skip) per item with arrow keys,
then confirm or cancel the whole batch. Items with section="files" render
under a "Files" header; items with section="entities" render under an
"Entities" header. `bifrost sync/push/pull/watch` only emits file items;
`bifrost import` emits entity items.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, ListItem, ListView, Static

from bifrost.tui.theme import BifrostApp

# ── Action rendering ────────────────────────────────────────────────────

_ACTION_LABELS: dict[str, tuple[str, str]] = {
    "push": ("\u2191 Push", "bold #9ece6a"),
    "pull": ("\u2193 Pull", "bold #7aa2f7"),
    "delete": ("\u2715 Delete", "bold #f7768e"),
    "skip": ("\u00b7 Skip", "bold #6e7681"),
}

# Fixed column widths
_COL_ACTION = 10
_COL_WHY = 14
_COL_MODIFIED = 10
_COL_AUTHOR = 20
_FIXED_COLS = _COL_ACTION + _COL_WHY + _COL_MODIFIED + _COL_AUTHOR
_SPACING = 8  # 4 x 2-char separators
_MIN_ITEM_W = 30


# ── Result dataclass ────────────────────────────────────────────────────


@dataclass
class SyncResult:
    """Items grouped by chosen action."""

    push: list[dict[str, Any]] = field(default_factory=list)
    pull: list[dict[str, Any]] = field(default_factory=list)
    delete: list[dict[str, Any]] = field(default_factory=list)
    skip: list[dict[str, Any]] = field(default_factory=list)


# ── SyncRow widget ──────────────────────────────────────────────────────


class SyncRow(ListItem):
    """A single syncable item that can cycle through valid actions."""

    def __init__(self, item: dict[str, Any], item_col_width: int) -> None:
        super().__init__()
        self.item = item
        self._item_w = item_col_width
        self._action: str = item.get("default_action", "skip")
        self._valid_actions: list[str] = item.get("valid_actions", ["skip"])

    def compose(self) -> ComposeResult:
        yield Static(self._build_label())

    @property
    def action(self) -> str:
        return self._action

    def cycle_forward(self) -> None:
        idx = self._valid_actions.index(self._action)
        self._action = self._valid_actions[(idx + 1) % len(self._valid_actions)]
        self._update_label()

    def cycle_backward(self) -> None:
        idx = self._valid_actions.index(self._action)
        self._action = self._valid_actions[(idx - 1) % len(self._valid_actions)]
        self._update_label()

    def reset_to_default(self) -> None:
        self._action = self.item.get("default_action", "skip")
        self._update_label()

    def set_skip(self) -> None:
        if "skip" in self._valid_actions:
            self._action = "skip"
            self._update_label()

    def _update_label(self) -> None:
        label = self._build_label()
        # Replace children with a Static showing the label
        self.remove_children()
        self.mount(Static(label))

    def _build_label(self) -> Text:
        text = Text()

        # Item name column (dynamic width)
        name = self.item.get("name") or ""
        if len(name) > self._item_w:
            name = name[: self._item_w - 1] + "\u2026"
        text.append(name.ljust(self._item_w))
        text.append("  ")

        # Action column
        label_str, style = _ACTION_LABELS.get(self._action, (self._action, ""))
        text.append(label_str.ljust(_COL_ACTION), style=style)
        text.append("  ")

        # Why column
        why = self.item.get("why") or ""
        if len(why) > _COL_WHY:
            why = why[: _COL_WHY - 1] + "\u2026"
        text.append(why.ljust(_COL_WHY))
        text.append("  ")

        # Modified column
        modified = self.item.get("modified") or ""
        if len(modified) > _COL_MODIFIED:
            modified = modified[: _COL_MODIFIED - 1] + "\u2026"
        text.append(modified.ljust(_COL_MODIFIED))
        text.append("  ")

        # Author column
        author = self.item.get("author") or ""
        if len(author) > _COL_AUTHOR:
            author = author[: _COL_AUTHOR - 1] + "\u2026"
        text.append(author.ljust(_COL_AUTHOR))

        return text


# ── Section header ──────────────────────────────────────────────────────


class _SectionHeader(ListItem):
    """Non-selectable section divider."""

    def __init__(self, title: str) -> None:
        super().__init__()
        self.disabled = True
        self._title = title

    def compose(self) -> ComposeResult:
        text = Text(f"\u2500\u2500 {self._title} ", style="bold #6e7681")
        text.append(
            "\u2500" * 60,
            style="#30363d",
        )
        yield Static(text)


# ── SyncApp ─────────────────────────────────────────────────────────────


class SyncApp(BifrostApp[SyncResult | None]):
    """Sync review TUI.

    Renders items grouped into "Files" and "Entities" sections when both are
    present; a single section's header is suppressed to reduce visual noise.
    """

    CSS = """
    ListView {
        height: 1fr;
        margin: 0 2;
        border: none;
        padding: 0;
        background: #0d1117;
    }
    ListView > ListItem {
        padding: 0 1;
        height: 1;
        background: #0d1117;
    }
    ListView > ListItem.--highlight {
        background: #21262d;
    }
    #column-header {
        height: 1;
        margin: 1 2 0 2;
        padding: 0 2;
        color: #6e7681;
    }
    #status-bar {
        dock: bottom;
        height: 1;
        margin: 0 2;
        color: #6e7681;
    }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Confirm", show=True, priority=True),
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
        Binding("ctrl+c", "cancel", "Cancel", show=False, priority=True),
        Binding("ctrl+q", "cancel", "Cancel", show=False, priority=True),
        Binding("right,l", "cycle_forward", "\u2192 next action", show=True, priority=True),
        Binding("left,h", "cycle_backward", "\u2190 prev action", show=True, priority=True),
        Binding("a", "reset_all", "Reset all", show=True, priority=True),
        Binding("s", "skip_all", "Skip all", show=True, priority=True),
    ]

    def __init__(
        self,
        items: list[dict[str, Any]],
        file_count: int = 0,
        entity_count: int = 0,
        subtitle: str = "",
        title: str = "bifrost sync",
    ) -> None:
        super().__init__()
        self._items = items
        self._file_count = file_count
        self._entity_count = entity_count
        self.title = title
        if subtitle:
            self.sub_title = subtitle

        # Compute dynamic item column width
        try:
            import shutil
            tw = shutil.get_terminal_size().columns
        except Exception:
            tw = 120
        self._item_w = max(tw - _FIXED_COLS - _SPACING, _MIN_ITEM_W)

    def _build_header_text(self) -> Text:
        text = Text()
        text.append("Item".ljust(self._item_w), style="bold #6e7681")
        text.append("  ")
        text.append("Action".ljust(_COL_ACTION), style="bold #6e7681")
        text.append("  ")
        text.append("Why".ljust(_COL_WHY), style="bold #6e7681")
        text.append("  ")
        text.append("Modified".ljust(_COL_MODIFIED), style="bold #6e7681")
        text.append("  ")
        text.append("Author".ljust(_COL_AUTHOR), style="bold #6e7681")
        return text

    def _status_text(self) -> str:
        hints = "\u2191\u2193 navigate \u00b7 \u2190\u2192 cycle action \u00b7 Enter confirm \u00b7 Esc cancel"
        parts: list[str] = []
        if self._file_count:
            parts.append(f"{self._file_count} file{'s' if self._file_count != 1 else ''}")
        if self._entity_count:
            parts.append(
                f"{self._entity_count} entit{'ies' if self._entity_count != 1 else 'y'}"
            )
        if parts:
            sep = " \u00b7 "
            return f"  {sep.join(parts)} \u00b7 {hints}"
        return f"  {hints}"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(self._build_header_text(), id="column-header")

        # Group by section — headers only shown when both sections are present
        # (a single-section list doesn't need a divider).
        file_items = [i for i in self._items if i.get("section") == "files"]
        entity_items = [i for i in self._items if i.get("section") == "entities"]
        other_items = [
            i for i in self._items if i.get("section") not in ("files", "entities")
        ]

        show_headers = bool(file_items) and bool(entity_items)

        list_items: list[ListItem] = []
        if file_items:
            if show_headers:
                list_items.append(_SectionHeader("Files"))
            for item in file_items:
                list_items.append(SyncRow(item, self._item_w))
        if entity_items:
            if show_headers:
                list_items.append(_SectionHeader("Entities"))
            for item in entity_items:
                list_items.append(SyncRow(item, self._item_w))
        for item in other_items:
            list_items.append(SyncRow(item, self._item_w))

        yield ListView(*list_items)
        yield Static(self._status_text(), id="status-bar")
        yield Footer()

    def _get_focused_sync_row(self) -> SyncRow | None:
        lv = self.query_one(ListView)
        if lv.highlighted_child is not None and isinstance(
            lv.highlighted_child, SyncRow
        ):
            return lv.highlighted_child
        return None

    def action_cycle_forward(self) -> None:
        row = self._get_focused_sync_row()
        if row is not None:
            row.cycle_forward()

    def action_cycle_backward(self) -> None:
        row = self._get_focused_sync_row()
        if row is not None:
            row.cycle_backward()

    def action_reset_all(self) -> None:
        for row in self.query(SyncRow):
            row.reset_to_default()

    def action_skip_all(self) -> None:
        for row in self.query(SyncRow):
            row.set_skip()

    def action_confirm(self) -> None:
        result = SyncResult()
        for row in self.query(SyncRow):
            bucket = getattr(result, row.action, None)
            if bucket is not None:
                bucket.append(row.item)
            else:
                # Unknown action — treat as skip
                result.skip.append(row.item)
        self.exit(result)

    def action_cancel(self) -> None:
        self.exit(None)


# ── Convenience function ────────────────────────────────────────────────


async def interactive_sync(
    items: list[dict[str, Any]],
    file_count: int = 0,
    entity_count: int = 0,
    subtitle: str = "",
    title: str = "bifrost sync",
) -> SyncResult | None:
    """Show the sync TUI and return the result.

    Cancel if a terminal is unavailable; missing UI never approves actions.
    """
    if not items:
        return SyncResult()

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        return None

    app = SyncApp(items, file_count, entity_count, subtitle, title)
    return await app.run_async()
