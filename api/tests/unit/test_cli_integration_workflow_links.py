import json
from uuid import uuid4

from bifrost.commands.integrations import _extract_workflow_links


def test_cli_link_json_file_and_clear(tmp_path):
    link = {"workflow_id": str(uuid4()), "label": "Sync Now", "pinned": True}
    assert _extract_workflow_links({"workflow_links": (json.dumps(link),)}) == [link]
    path = tmp_path / "links.yaml"
    path.write_text(json.dumps([link]))
    assert _extract_workflow_links({"workflow_links": (f"@{path}",)}) == [link]
    assert _extract_workflow_links({"workflow_links": ("[]",)}) == []
    assert _extract_workflow_links({}) is None
