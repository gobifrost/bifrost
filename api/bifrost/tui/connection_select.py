"""Interactive connection selector for CLI auth resolution."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, ListItem, ListView, Static

from bifrost.tui.theme import BifrostApp


class ConnectionSelectApp(BifrostApp[str | None]):
    """Full-screen single-selection prompt for stored Bifrost connections."""

    CSS = """
    #hint {
        margin: 1 2 0 2;
        color: #6e7681;
    }
    ListView {
        height: 1fr;
        margin: 1 2;
        border: none;
        background: #0d1117;
    }
    ListView > ListItem {
        padding: 0 2;
        height: 1;
        background: #0d1117;
    }
    ListView > ListItem.--highlight {
        background: #21262d;
        color: #e6edf3;
    }
    """

    BINDINGS = [
        Binding("enter", "confirm", "Use", show=True, priority=True),
        Binding("escape", "cancel", "Cancel", show=True, priority=True),
        Binding("ctrl+c", "cancel", "Cancel", show=False, priority=True),
        Binding("ctrl+q", "cancel", "Cancel", show=False, priority=True),
    ]

    def __init__(self, urls: list[str]) -> None:
        super().__init__()
        self._urls = [url.rstrip("/") for url in urls]
        self.title = "Select Bifrost connection"
        self.sub_title = "This will be saved as your default connection"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static("Choose the default connection for commands in directories without .env.", id="hint")
        yield ListView(*(ListItem(Static(url)) for url in self._urls))
        yield Footer()

    def on_mount(self) -> None:
        list_view = self.query_one(ListView)
        list_view.index = 0
        list_view.focus()

    def action_confirm(self) -> None:
        list_view = self.query_one(ListView)
        index = list_view.index
        if index is None or index < 0 or index >= len(self._urls):
            return
        self.exit(self._urls[index])

    def action_cancel(self) -> None:
        self.exit(None)


def select_default_connection(urls: list[str]) -> str | None:
    """Run the Textual selector synchronously."""
    if not urls:
        return None
    app = ConnectionSelectApp(urls)
    return app.run()
