import src.services.events.builtins as b


async def test_emit_solution_update_available(monkeypatch):
    calls = []

    async def fake_emit(topic, body, *, organization_id, triggered_by=None):
        calls.append((topic, body, organization_id))

    monkeypatch.setattr(b, "_emit", fake_emit)

    await b.emit_solution_update_available(
        solution_id="s1",
        slug="acme",
        organization_id="o1",
        installed_version="1.0.0",
        available_version="1.1.0",
    )

    assert calls
    topic, body, org = calls[0]
    assert topic == "solution.update_available"
    assert org == "o1"
    # Envelope from _base_body matches the curated registry example shape.
    assert body["schema_version"] == 1
    assert set(body["organization"].keys()) == {"id", "name"}
    assert set(body["actor"].keys()) == {"type", "id", "email", "name"}
    assert body["solution"] == {
        "id": "s1",
        "slug": "acme",
        "installed_version": "1.0.0",
        "available_version": "1.1.0",
    }


async def test_emit_solution_update_available_null_solution_id(monkeypatch):
    calls = []

    async def fake_emit(topic, body, *, organization_id, triggered_by=None):
        calls.append((topic, body, organization_id))

    monkeypatch.setattr(b, "_emit", fake_emit)

    await b.emit_solution_update_available(
        solution_id=None,
        slug="acme",
        organization_id=None,
        installed_version=None,
        available_version="2.0.0",
    )

    assert calls[0][1]["solution"]["id"] is None
    assert calls[0][1]["solution"]["installed_version"] is None
