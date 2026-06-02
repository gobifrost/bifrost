"""
Integration tests for Tables API.

Tests table and document endpoints with real PostgreSQL database.
These tests require PostgreSQL to be running (via docker-compose.test.yml).
"""

import pytest
import pytest_asyncio
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.orm.tables import Document
from src.models.orm.organizations import Organization
from src.repositories.tables import TableRepository
from src.routers.tables import DocumentRepository


@pytest_asyncio.fixture
async def test_org(db_session: AsyncSession):
    """Create a real organization for test isolation."""
    org = Organization(
        id=uuid4(),
        name=f"Test Org {uuid4().hex[:8]}",
        domain=f"test-{uuid4().hex[:8]}.example.com",
        created_by="test@example.com",
    )
    db_session.add(org)
    await db_session.commit()
    await db_session.refresh(org)
    return org


@pytest.fixture
def test_user_email():
    """Test user email."""
    return "test@example.com"


class TestTableRepositoryIntegration:
    """Integration tests for TableRepository with real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_create_and_get_table(self, db_session: AsyncSession, test_org, test_user_email):
        """Test creating and retrieving a table."""
        repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate
        table_data = TableCreate(
            name=f"test_customers_{uuid4().hex[:8]}",
            description="Customer records",
            schema={"type": "object", "properties": {"name": {"type": "string"}}},
        )

        table = await repo.create_table(table_data, created_by=test_user_email)

        assert table.id is not None
        assert table.name == table_data.name
        assert table.description == "Customer records"
        assert table.organization_id == test_org.id

        # Retrieve table
        retrieved = await repo.get_by_name(table_data.name)
        assert retrieved is not None
        assert retrieved.id == table.id

    @pytest.mark.asyncio
    async def test_list_tables(self, db_session: AsyncSession, test_org, test_user_email):
        """Test listing tables."""
        repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate
        for i in range(3):
            table_data = TableCreate(
                name=f"test_table_{uuid4().hex[:8]}_{i}",
                description=f"Test table {i}",
            )
            await repo.create_table(table_data, created_by=test_user_email)

        tables = await repo.list_tables()
        assert len(tables) >= 3

    @pytest.mark.asyncio
    async def test_update_table(self, db_session: AsyncSession, test_org, test_user_email):
        """Test updating a table."""
        repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate, TableUpdate
        table_data = TableCreate(
            name=f"test_update_{uuid4().hex[:8]}",
            description="Original description",
        )
        await repo.create_table(table_data, created_by=test_user_email)

        update_data = TableUpdate(
            description="Updated description",
            schema={"type": "object"},
        )
        table = await repo.get_by_name(table_data.name)
        assert table is not None
        updated = await repo.update_table(table.id, update_data)

        assert updated is not None
        assert updated.description == "Updated description"
        assert updated.schema == {"type": "object"}

    @pytest.mark.asyncio
    async def test_delete_table(self, db_session: AsyncSession, test_org, test_user_email):
        """Test deleting a table."""
        repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate
        table_data = TableCreate(
            name=f"test_delete_{uuid4().hex[:8]}",
        )
        table = await repo.create_table(table_data, created_by=test_user_email)

        success = await repo.delete_table(table.id)
        assert success is True

        retrieved = await repo.get_by_name(table_data.name)
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_duplicate_table_name_raises_error(self, db_session: AsyncSession, test_org, test_user_email):
        """Test that creating a table with duplicate name raises error."""
        repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate
        table_name = f"test_duplicate_{uuid4().hex[:8]}"
        table_data = TableCreate(name=table_name)

        await repo.create_table(table_data, created_by=test_user_email)

        with pytest.raises(ValueError, match="already exists"):
            await repo.create_table(table_data, created_by=test_user_email)


class TestDocumentRepositoryIntegration:
    """Integration tests for DocumentRepository with real PostgreSQL."""

    @pytest_asyncio.fixture
    async def test_table(self, db_session: AsyncSession, test_org, test_user_email):
        """Create a test table for document operations."""
        repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate
        table_data = TableCreate(
            name=f"test_documents_{uuid4().hex[:8]}",
        )
        table = await repo.create_table(table_data, created_by=test_user_email)
        return table

    @pytest.mark.asyncio
    async def test_insert_and_get_document(self, db_session: AsyncSession, test_table, test_user_email):
        """Test inserting and retrieving a document."""
        doc_repo = DocumentRepository(db_session, test_table)

        doc_data = {"name": "Acme Corp", "email": "info@acme.com", "status": "active"}
        doc = await doc_repo.insert(doc_data, created_by=test_user_email)

        assert doc.id is not None
        assert doc.data["name"] == "Acme Corp"
        assert doc.created_by == test_user_email

        retrieved = await doc_repo.get(doc.id)
        assert retrieved is not None
        assert retrieved.data["email"] == "info@acme.com"

    @pytest.mark.asyncio
    async def test_update_document(self, db_session: AsyncSession, test_table, test_user_email):
        """Test updating a document (partial update)."""
        doc_repo = DocumentRepository(db_session, test_table)

        doc = await doc_repo.insert(
            {"name": "Acme", "status": "active"},
            created_by=test_user_email,
        )

        updated = await doc_repo.update(
            doc.id,
            {"status": "inactive", "notes": "Churned"},
            updated_by=test_user_email,
        )

        assert updated is not None
        assert updated.data["name"] == "Acme"
        assert updated.data["status"] == "inactive"
        assert updated.data["notes"] == "Churned"

    @pytest.mark.asyncio
    async def test_delete_document(self, db_session: AsyncSession, test_table, test_user_email):
        """Test deleting a document."""
        doc_repo = DocumentRepository(db_session, test_table)

        doc = await doc_repo.insert({"name": "ToDelete"}, created_by=test_user_email)

        success = await doc_repo.delete(doc.id)
        assert success is True

        retrieved = await doc_repo.get(doc.id)
        assert retrieved is None

    @pytest.mark.asyncio
    async def test_query_documents_with_filter(self, db_session: AsyncSession, test_table, test_user_email):
        """Test querying documents with filter."""
        doc_repo = DocumentRepository(db_session, test_table)

        await doc_repo.insert({"name": "Active1", "status": "active"}, created_by=test_user_email)
        await doc_repo.insert({"name": "Active2", "status": "active"}, created_by=test_user_email)
        await doc_repo.insert({"name": "Inactive", "status": "inactive"}, created_by=test_user_email)

        from src.models.contracts.tables import DocumentQuery
        query = DocumentQuery(where={"status": "active"})
        documents, total = await doc_repo.query(query)

        assert total == 2
        assert all(d.data["status"] == "active" for d in documents)

    @pytest.mark.asyncio
    async def test_query_documents_with_pagination(self, db_session: AsyncSession, test_table, test_user_email):
        """Test querying documents with pagination."""
        doc_repo = DocumentRepository(db_session, test_table)

        for i in range(10):
            await doc_repo.insert({"index": i, "name": f"Doc{i}"}, created_by=test_user_email)

        from src.models.contracts.tables import DocumentQuery
        query = DocumentQuery(limit=3, offset=0)
        documents, total = await doc_repo.query(query)

        assert total == 10
        assert len(documents) == 3

        query = DocumentQuery(limit=3, offset=3)
        documents, total = await doc_repo.query(query)

        assert total == 10
        assert len(documents) == 3

    @pytest.mark.asyncio
    async def test_count_documents(self, db_session: AsyncSession, test_table, test_user_email):
        """Test counting documents."""
        doc_repo = DocumentRepository(db_session, test_table)

        await doc_repo.insert({"type": "a"}, created_by=test_user_email)
        await doc_repo.insert({"type": "a"}, created_by=test_user_email)
        await doc_repo.insert({"type": "b"}, created_by=test_user_email)

        total = await doc_repo.count()
        assert total >= 3

        count_a = await doc_repo.count(where={"type": "a"})
        assert count_a == 2

        count_b = await doc_repo.count(where={"type": "b"})
        assert count_b == 1


class TestDocumentQueryOperators:
    """Integration tests for document query filter operators."""

    @pytest_asyncio.fixture
    async def test_table_with_data(self, db_session: AsyncSession, test_org, test_user_email):
        """Create a test table with sample documents for query testing."""
        repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate
        table_data = TableCreate(
            name=f"test_query_ops_{uuid4().hex[:8]}",
        )
        table = await repo.create_table(table_data, created_by=test_user_email)

        doc_repo = DocumentRepository(db_session, table)

        # Insert test documents with various data types
        await doc_repo.insert({"name": "Acme Corp", "status": "active", "amount": 100, "active": True}, created_by=test_user_email)
        await doc_repo.insert({"name": "Beta Inc", "status": "active", "amount": 200, "active": True}, created_by=test_user_email)
        await doc_repo.insert({"name": "Gamma LLC", "status": "inactive", "amount": 300, "active": False}, created_by=test_user_email)
        await doc_repo.insert({"name": "Delta Co", "status": "pending", "amount": 150, "active": True}, created_by=test_user_email)
        await doc_repo.insert({"name": "Acme Beta", "status": "active"}, created_by=test_user_email)  # No amount/active field

        return table

    @pytest.mark.asyncio
    async def test_contains_operator(self, db_session: AsyncSession, test_table_with_data):
        """Test contains operator for case-insensitive substring search."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        # Test contains (case-insensitive)
        query = DocumentQuery(where={"name": {"contains": "acme"}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # "Acme Corp" and "Acme Beta"
        assert all("Acme" in d.data["name"] or "acme" in d.data["name"].lower() for d in documents)

        # Test contains with uppercase search
        query = DocumentQuery(where={"name": {"contains": "BETA"}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # "Beta Inc" and "Acme Beta"

    @pytest.mark.asyncio
    async def test_starts_with_operator(self, db_session: AsyncSession, test_table_with_data):
        """Test starts_with operator."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(where={"name": {"starts_with": "Acme"}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # "Acme Corp" and "Acme Beta"
        assert all(d.data["name"].startswith("Acme") for d in documents)

        # Test case-insensitivity
        query = DocumentQuery(where={"name": {"starts_with": "beta"}})
        documents, total = await doc_repo.query(query)
        assert total == 1  # "Beta Inc"

    @pytest.mark.asyncio
    async def test_ends_with_operator(self, db_session: AsyncSession, test_table_with_data):
        """Test ends_with operator."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(where={"name": {"ends_with": "Corp"}})
        documents, total = await doc_repo.query(query)
        assert total == 1  # "Acme Corp"
        assert documents[0].data["name"] == "Acme Corp"

        # Test case-insensitivity
        query = DocumentQuery(where={"name": {"ends_with": "inc"}})
        documents, total = await doc_repo.query(query)
        assert total == 1  # "Beta Inc"

    @pytest.mark.asyncio
    async def test_ne_operator(self, db_session: AsyncSession, test_table_with_data):
        """Test ne (not equals) operator."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(where={"status": {"ne": "active"}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # "inactive" and "pending"
        assert all(d.data["status"] != "active" for d in documents)

    @pytest.mark.asyncio
    async def test_gt_gte_lt_lte_operators(self, db_session: AsyncSession, test_table_with_data):
        """Test comparison operators (gt, gte, lt, lte)."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        # Greater than
        query = DocumentQuery(where={"amount": {"gt": "150"}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # 200 and 300

        # Greater than or equal
        query = DocumentQuery(where={"amount": {"gte": "150"}})
        documents, total = await doc_repo.query(query)
        assert total == 3  # 150, 200, 300

        # Less than
        query = DocumentQuery(where={"amount": {"lt": "200"}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # 100 and 150

        # Less than or equal
        query = DocumentQuery(where={"amount": {"lte": "200"}})
        documents, total = await doc_repo.query(query)
        assert total == 3  # 100, 150, 200

    @pytest.mark.asyncio
    async def test_in_operator(self, db_session: AsyncSession, test_table_with_data):
        """Test in operator for matching values in a list."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(where={"status": {"in": ["active", "pending"]}})
        documents, total = await doc_repo.query(query)
        assert total == 4  # 3 active + 1 pending
        assert all(d.data["status"] in ["active", "pending"] for d in documents)

    @pytest.mark.asyncio
    async def test_is_null_operator(self, db_session: AsyncSession, test_table_with_data):
        """Test is_null operator for checking null/missing fields."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        # Find documents where amount is null/missing
        query = DocumentQuery(where={"amount": {"is_null": True}})
        documents, total = await doc_repo.query(query)
        assert total == 1  # "Acme Beta" has no amount
        assert documents[0].data["name"] == "Acme Beta"

        # Find documents where amount is NOT null
        query = DocumentQuery(where={"amount": {"is_null": False}})
        documents, total = await doc_repo.query(query)
        assert total == 4  # All others have amount

    @pytest.mark.asyncio
    async def test_combined_operators(self, db_session: AsyncSession, test_table_with_data):
        """Test combining multiple filter conditions."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        # Active status AND amount >= 150
        query = DocumentQuery(where={
            "status": "active",
            "amount": {"gte": "150"}
        })
        documents, total = await doc_repo.query(query)
        assert total == 1  # Only "Beta Inc" (active, 200)

    @pytest.mark.asyncio
    async def test_eq_operator_explicit(self, db_session: AsyncSession, test_table_with_data):
        """Test explicit eq operator (same as simple equality)."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        # Using explicit eq operator
        query = DocumentQuery(where={"status": {"eq": "active"}})
        documents, total = await doc_repo.query(query)
        assert total == 3

        # Should be same as simple equality
        query = DocumentQuery(where={"status": "active"})
        documents2, total2 = await doc_repo.query(query)
        assert total2 == 3

    @pytest.mark.asyncio
    async def test_simple_equality_boolean(self, db_session: AsyncSession, test_table_with_data):
        """Test simple equality filter with boolean values."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(where={"active": True})
        documents, total = await doc_repo.query(query)
        assert total == 3  # Acme Corp, Beta Inc, Delta Co
        assert all(d.data["active"] is True for d in documents)

        query = DocumentQuery(where={"active": False})
        documents, total = await doc_repo.query(query)
        assert total == 1  # Gamma LLC
        assert documents[0].data["name"] == "Gamma LLC"

    @pytest.mark.asyncio
    async def test_in_operator_boolean(self, db_session: AsyncSession, test_table_with_data):
        """Test in operator with boolean values (regression: str(True) -> 'True' != 'true')."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(where={"active": {"in": [True]}})
        documents, total = await doc_repo.query(query)
        assert total == 3  # Acme Corp, Beta Inc, Delta Co

        query = DocumentQuery(where={"active": {"in": [False]}})
        documents, total = await doc_repo.query(query)
        assert total == 1  # Gamma LLC

        query = DocumentQuery(where={"active": {"in": [True, False]}})
        documents, total = await doc_repo.query(query)
        assert total == 4  # All documents that have the active field

    @pytest.mark.asyncio
    async def test_ne_operator_boolean(self, db_session: AsyncSession, test_table_with_data):
        """Test ne operator with boolean values."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(where={"active": {"ne": True}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # Gamma LLC (active=False) + Acme Beta (no active field)
        names = {d.data["name"] for d in documents}
        assert "Gamma LLC" in names

    @pytest.mark.asyncio
    async def test_in_operator_numeric(self, db_session: AsyncSession, test_table_with_data):
        """Test in operator with numeric values."""
        doc_repo = DocumentRepository(db_session, test_table_with_data)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(where={"amount": {"in": [100, 200]}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # Acme Corp (100) and Beta Inc (200)
        amounts = {d.data["amount"] for d in documents}
        assert amounts == {100, 200}


class TestILIKEEscaping:
    """Test that ILIKE wildcard characters in user input are properly escaped."""

    @pytest_asyncio.fixture
    async def test_table_with_wildcards(self, db_session: AsyncSession, test_org, test_user_email):
        """Create a test table with documents containing LIKE wildcard characters."""
        repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate
        table_data = TableCreate(
            name=f"test_ilike_{uuid4().hex[:8]}",
        )
        table = await repo.create_table(table_data, created_by=test_user_email)

        doc_repo = DocumentRepository(db_session, table)

        await doc_repo.insert({"name": "100% Complete", "code": "A_B"}, created_by=test_user_email)
        await doc_repo.insert({"name": "50% Done", "code": "A_C"}, created_by=test_user_email)
        await doc_repo.insert({"name": "Normal Name", "code": "AXB"}, created_by=test_user_email)
        await doc_repo.insert({"name": "Another Normal", "code": "AXBC"}, created_by=test_user_email)

        return table

    @pytest.mark.asyncio
    async def test_contains_escapes_percent(self, db_session: AsyncSession, test_table_with_wildcards):
        """Test that % in contains search is treated as literal, not wildcard."""
        doc_repo = DocumentRepository(db_session, test_table_with_wildcards)

        from src.models.contracts.tables import DocumentQuery

        # Search for literal "%" — should only match documents with actual percent sign
        query = DocumentQuery(where={"name": {"contains": "%"}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # "100% Complete" and "50% Done"
        assert all("%" in d.data["name"] for d in documents)

    @pytest.mark.asyncio
    async def test_contains_escapes_underscore(self, db_session: AsyncSession, test_table_with_wildcards):
        """Test that _ in contains search is treated as literal, not single-char wildcard."""
        doc_repo = DocumentRepository(db_session, test_table_with_wildcards)

        from src.models.contracts.tables import DocumentQuery

        # Search for literal "_" — should only match documents with actual underscore
        query = DocumentQuery(where={"code": {"contains": "_"}})
        documents, total = await doc_repo.query(query)
        assert total == 2  # "A_B" and "A_C"
        assert all("_" in d.data["code"] for d in documents)

    @pytest.mark.asyncio
    async def test_starts_with_escapes_wildcards(self, db_session: AsyncSession, test_table_with_wildcards):
        """Test that starts_with escapes wildcard characters."""
        doc_repo = DocumentRepository(db_session, test_table_with_wildcards)

        from src.models.contracts.tables import DocumentQuery

        # "A_B" as starts_with should NOT match "AXB" or "AXBC" (unescaped _ would match any char)
        query = DocumentQuery(where={"code": {"starts_with": "A_B"}})
        documents, total = await doc_repo.query(query)
        assert total == 1  # Only "A_B", not "AXB"
        assert documents[0].data["code"] == "A_B"

    @pytest.mark.asyncio
    async def test_ends_with_escapes_wildcards(self, db_session: AsyncSession, test_table_with_wildcards):
        """Test that ends_with escapes wildcard characters."""
        doc_repo = DocumentRepository(db_session, test_table_with_wildcards)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(where={"name": {"ends_with": "% Complete"}})
        documents, total = await doc_repo.query(query)
        assert total == 1  # Only "100% Complete"
        assert documents[0].data["name"] == "100% Complete"


class TestSkipCount:
    """Test skip_count parameter for document queries."""

    @pytest_asyncio.fixture
    async def test_table_with_docs(self, db_session: AsyncSession, test_org, test_user_email):
        """Create a test table with some documents."""
        repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate
        table_data = TableCreate(
            name=f"test_skipcount_{uuid4().hex[:8]}",
        )
        table = await repo.create_table(table_data, created_by=test_user_email)

        doc_repo = DocumentRepository(db_session, table)
        for i in range(5):
            await doc_repo.insert({"index": i}, created_by=test_user_email)

        return table

    @pytest.mark.asyncio
    async def test_skip_count_returns_negative_one(self, db_session: AsyncSession, test_table_with_docs):
        """Test that skip_count=True returns total=-1."""
        doc_repo = DocumentRepository(db_session, test_table_with_docs)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(skip_count=True)
        documents, total = await doc_repo.query(query)
        assert total == -1
        assert len(documents) == 5

    @pytest.mark.asyncio
    async def test_skip_count_false_returns_real_count(self, db_session: AsyncSession, test_table_with_docs):
        """Test that skip_count=False (default) returns the real count."""
        doc_repo = DocumentRepository(db_session, test_table_with_docs)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery(skip_count=False)
        documents, total = await doc_repo.query(query)
        assert total == 5
        assert len(documents) == 5

    @pytest.mark.asyncio
    async def test_skip_count_default_is_false(self, db_session: AsyncSession, test_table_with_docs):
        """Test that the default is skip_count=False."""
        doc_repo = DocumentRepository(db_session, test_table_with_docs)

        from src.models.contracts.tables import DocumentQuery

        query = DocumentQuery()
        documents, total = await doc_repo.query(query)
        assert total == 5

    @pytest.mark.asyncio
    async def test_skip_count_with_pagination(self, db_session: AsyncSession, test_table_with_docs):
        """Test skip_count works correctly with pagination."""
        doc_repo = DocumentRepository(db_session, test_table_with_docs)

        from src.models.contracts.tables import DocumentQuery

        # First page with count
        query = DocumentQuery(limit=2, offset=0, skip_count=False)
        documents, total = await doc_repo.query(query)
        assert total == 5
        assert len(documents) == 2

        # Second page without count
        query = DocumentQuery(limit=2, offset=2, skip_count=True)
        documents, total = await doc_repo.query(query)
        assert total == -1
        assert len(documents) == 2


class TestTableCascadeDelete:
    """Test that deleting a table cascades to documents."""

    @pytest.mark.asyncio
    async def test_delete_table_deletes_documents(self, db_session: AsyncSession, test_org, test_user_email):
        """Test that deleting a table also deletes all its documents."""
        table_repo = TableRepository(db_session, test_org.id)

        from src.models.contracts.tables import TableCreate
        table_data = TableCreate(name=f"test_cascade_{uuid4().hex[:8]}")
        table = await table_repo.create_table(table_data, created_by=test_user_email)

        doc_repo = DocumentRepository(db_session, table)

        doc1 = await doc_repo.insert({"name": "Doc1"}, created_by=test_user_email)
        doc2 = await doc_repo.insert({"name": "Doc2"}, created_by=test_user_email)
        doc_ids = [doc1.id, doc2.id]

        success = await table_repo.delete_table(table.id)
        assert success is True

        stmt = select(Document).where(Document.id.in_(doc_ids))
        result = await db_session.execute(stmt)
        remaining = result.scalars().all()
        assert len(remaining) == 0
