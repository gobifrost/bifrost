"""Unit tests for FormIndexer."""

from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.forms import Form, FormField
from src.services.file_storage.indexers.form import FormIndexer


@pytest_asyncio.fixture(autouse=True)
async def cleanup_forms(db_session: AsyncSession):
    """Clean up test forms after each test."""
    yield
    await db_session.execute(
        delete(Form).where(Form.created_by == "file_sync")
    )
    await db_session.commit()


@pytest.mark.e2e
@pytest.mark.asyncio
class TestFormIndexer:
    async def test_index_form_with_workflow_alias(self, db_session: AsyncSession):
        """FormIndexer should accept 'workflow' as alias for 'workflow_id'."""
        wf_id = uuid4()
        form_yaml = f"""name: Test Form Alias
workflow: {wf_id}
"""
        indexer = FormIndexer(db_session)
        await indexer.index_form("forms/test.form.yaml", form_yaml.encode())
        await db_session.flush()

        form = (await db_session.execute(
            select(Form).where(Form.name == "Test Form Alias")
        )).scalar_one()
        assert str(form.workflow_id) == str(wf_id)

    async def test_index_form_with_flat_fields(self, db_session: AsyncSession):
        """FormIndexer should accept flat 'fields' array (not nested in form_schema)."""
        form_yaml = """name: Flat Fields Form
fields:
- name: email
  type: text
  label: Email
  required: true
- name: count
  type: number
  label: Count
  default: 5
"""
        indexer = FormIndexer(db_session)
        await indexer.index_form("forms/test.form.yaml", form_yaml.encode())
        await db_session.flush()

        form = (await db_session.execute(
            select(Form).where(Form.name == "Flat Fields Form")
        )).scalar_one()
        fields = (await db_session.execute(
            select(FormField).where(FormField.form_id == form.id).order_by(FormField.position)
        )).scalars().all()
        assert len(fields) == 2
        assert fields[0].name == "email"
        assert fields[1].default_value == 5

    async def test_index_form_with_form_schema_fields(self, db_session: AsyncSession):
        """FormIndexer should still work with canonical form_schema.fields format."""
        form_yaml = """name: Schema Fields Form
form_schema:
  fields:
  - name: email
    type: text
    label: Email
    required: true
"""
        indexer = FormIndexer(db_session)
        await indexer.index_form("forms/test2.form.yaml", form_yaml.encode())
        await db_session.flush()

        form = (await db_session.execute(
            select(Form).where(Form.name == "Schema Fields Form")
        )).scalar_one()
        fields = (await db_session.execute(
            select(FormField).where(FormField.form_id == form.id)
        )).scalars().all()
        assert len(fields) == 1
        assert fields[0].name == "email"

    async def test_index_form_persists_portable_layout(self, db_session: AsyncSession):
        """The indexer validates and stores layout separately from field rows."""
        form_yaml = """name: Layout Form
form_schema:
  fields:
  - name: first_name
    type: text
    label: First name
  - name: last_name
    type: text
    label: Last name
  layout:
    sections:
    - title: Contact
      rows:
      - columns:
        - field: first_name
          width: 4
        - field: null
          width: 4
        - field: null
          width: 4
    - rows:
      - field: last_name
"""
        indexer = FormIndexer(db_session)
        await indexer.index_form("forms/layout.form.yaml", form_yaml.encode())
        await db_session.flush()

        form = (await db_session.execute(
            select(Form).where(Form.name == "Layout Form")
        )).scalar_one()
        assert form.layout == {
            "sections": [{
                "title": "Contact",
                "description": None,
                "rows": [{"field": None, "columns": [
                    {"field": "first_name", "width": 4},
                    {"field": None, "width": 4},
                    {"field": None, "width": 4},
                ]}],
            }, {"title": None, "description": None, "rows": [{"field": "last_name", "columns": []}]}],
        }

    async def test_invalid_schema_preserves_existing_form_and_fields(
        self, db_session: AsyncSession
    ):
        """A malformed replacement never partially overwrites a file-synced form."""
        form_id = uuid4()
        indexer = FormIndexer(db_session)
        await indexer.index_form(
            "forms/atomic.form.yaml",
            f"""id: {form_id}
name: Existing Form
description: Existing metadata
form_schema:
  fields:
  - name: email
    type: text
    label: Email
""".encode(),
        )
        await db_session.flush()

        await indexer.index_form(
            "forms/atomic.form.yaml",
            f"""id: {form_id}
name: Replacement Must Not Apply
description: Invalid metadata
form_schema:
  fields:
  - name: missing_type
    label: Missing Type
""".encode(),
        )
        await db_session.flush()

        form = await db_session.get(Form, form_id)
        fields = (
            await db_session.execute(
                select(FormField)
                .where(FormField.form_id == form_id)
                .order_by(FormField.position)
            )
        ).scalars().all()
        assert form is not None
        assert form.name == "Existing Form"
        assert form.description == "Existing metadata"
        assert [(field.name, field.label) for field in fields] == [("email", "Email")]

    async def test_valid_schema_replaces_all_existing_fields(
        self, db_session: AsyncSession
    ):
        """A fully valid explicit schema atomically replaces the old field set."""
        form_id = uuid4()
        indexer = FormIndexer(db_session)
        await indexer.index_form(
            "forms/replacement.form.yaml",
            f"""id: {form_id}
name: Original Form
form_schema:
  fields:
  - name: old_field
    type: text
    label: Old Field
""".encode(),
        )
        await db_session.flush()

        await indexer.index_form(
            "forms/replacement.form.yaml",
            f"""id: {form_id}
name: Replaced Form
fields:
- name: count
  type: number
  label: Count
  default: 5
- name: notes
  type: text
  label: Notes
""".encode(),
        )
        await db_session.flush()

        form = await db_session.get(Form, form_id)
        fields = (
            await db_session.execute(
                select(FormField)
                .where(FormField.form_id == form_id)
                .order_by(FormField.position)
            )
        ).scalars().all()
        assert form is not None
        assert form.name == "Replaced Form"
        assert [(field.name, field.default_value) for field in fields] == [
            ("count", 5),
            ("notes", None),
        ]

    async def test_too_many_fields_preserves_existing_schema(
        self, db_session: AsyncSession
    ):
        """The FormSchema 50-field limit applies before deletion or upsert."""
        form_id = uuid4()
        indexer = FormIndexer(db_session)
        await indexer.index_form(
            "forms/limit.form.yaml",
            f"""id: {form_id}
name: Existing Limited Form
fields:
- name: retained
  type: text
  label: Retained
""".encode(),
        )
        await db_session.flush()

        oversized_fields = "\n".join(
            f"- name: field_{index}\n  type: text\n  label: Field {index}"
            for index in range(51)
        )
        await indexer.index_form(
            "forms/limit.form.yaml",
            (
                f"id: {form_id}\nname: Too Many Fields\nfields:\n"
                f"{oversized_fields}\n"
            ).encode(),
        )
        await db_session.flush()

        form = await db_session.get(Form, form_id)
        fields = (
            await db_session.execute(
                select(FormField).where(FormField.form_id == form_id)
            )
        ).scalars().all()
        assert form is not None
        assert form.name == "Existing Limited Form"
        assert [field.name for field in fields] == ["retained"]
