"""File contract models accept freeform storage locations."""
from __future__ import annotations

from src.models.contracts.cli import (
    CLIFileDeleteRequest,
    CLIFileListRequest,
    CLIFileReadRequest,
    CLIFileWriteRequest,
)
from src.models.contracts.sdk import (
    SDKFileDeleteRequest,
    SDKFileListRequest,
    SDKFileReadRequest,
    SDKFileWriteRequest,
)


def test_sdk_file_contracts_accept_custom_location() -> None:
    assert SDKFileReadRequest(path="q1.pdf", location="reports").location == "reports"
    assert SDKFileWriteRequest(path="q1.pdf", content="x", location="reports").location == "reports"
    assert SDKFileListRequest(directory="", location="reports").location == "reports"
    assert SDKFileDeleteRequest(path="q1.pdf", location="reports").location == "reports"


def test_cli_file_contracts_accept_custom_location() -> None:
    assert CLIFileReadRequest(path="q1.pdf", location="reports").location == "reports"
    assert CLIFileWriteRequest(path="q1.pdf", content="x", location="reports").location == "reports"
    assert CLIFileListRequest(directory="", location="reports").location == "reports"
    assert CLIFileDeleteRequest(path="q1.pdf", location="reports").location == "reports"
