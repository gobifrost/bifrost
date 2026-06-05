from __future__ import annotations

from bifrost import cli


class _Cp1252Stdout:
    encoding = "cp1252"

    def __init__(self) -> None:
        self.parts: list[str] = []

    def write(self, text: str) -> int:
        text.encode(self.encoding)
        self.parts.append(text)
        return len(text)

    def flush(self) -> None:
        pass


class _Utf8Stdout:
    encoding = "utf-8"

    def __init__(self) -> None:
        self.parts: list[str] = []

    def write(self, text: str) -> int:
        text.encode(self.encoding)
        self.parts.append(text)
        return len(text)

    def flush(self) -> None:
        pass


class _ReconfigurableCp1252Stream:
    """Mimics a Windows console: starts cp1252, can be reconfigured to utf-8.

    A real text stream crashes when asked to write a glyph its encoding can't
    represent. Once reconfigured to utf-8 it accepts anything, which is exactly
    what `_ensure_utf8_stdio` relies on.
    """

    def __init__(self) -> None:
        self.encoding = "cp1252"
        self.parts: list[str] = []

    def reconfigure(self, *, encoding: str, errors: str) -> None:
        self.encoding = encoding
        self.errors = errors

    def write(self, text: str) -> int:
        text.encode(self.encoding)
        self.parts.append(text)
        return len(text)

    def flush(self) -> None:
        pass


def test_sync_summary_prints_on_windows_cp1252_stdout(monkeypatch) -> None:
    stdout = _Cp1252Stdout()
    monkeypatch.setattr("sys.stdout", stdout)

    cli._print_sync_summary("1 pushed")

    assert "".join(stdout.parts) == "  OK 1 pushed\n"


def test_check_glyph_uses_unicode_on_utf8(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdout", _Utf8Stdout())
    assert cli._check_glyph() == "✓"


def test_check_glyph_degrades_on_cp1252(monkeypatch) -> None:
    monkeypatch.setattr("sys.stdout", _Cp1252Stdout())
    assert cli._check_glyph() == "OK"


def test_ensure_utf8_stdio_lets_glyphs_print_on_windows(monkeypatch) -> None:
    stdout = _ReconfigurableCp1252Stream()
    stderr = _ReconfigurableCp1252Stream()
    monkeypatch.setattr("sys.stdout", stdout)
    monkeypatch.setattr("sys.stderr", stderr)

    cli._ensure_utf8_stdio()

    # The watch flow prints these glyphs to stdout/stderr; before the fix they
    # raised UnicodeEncodeError on a cp1252 console mid-command.
    for glyph in ("✓", "✗", "⚠", "←", "—"):
        print(glyph)

    assert stdout.encoding == "utf-8"
    assert stderr.encoding == "utf-8"


def test_ensure_utf8_stdio_tolerates_streams_without_reconfigure(monkeypatch) -> None:
    # A plain buffer (no reconfigure) must not raise — the summary path still
    # stays safe via _check_glyph().
    monkeypatch.setattr("sys.stdout", _Cp1252Stdout())
    monkeypatch.setattr("sys.stderr", _Cp1252Stdout())

    cli._ensure_utf8_stdio()  # should be a no-op, not an error
