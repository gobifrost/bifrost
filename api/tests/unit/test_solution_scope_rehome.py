from src.models.orm.base import Base
from src.services.solutions.scope_rehome import ORGANIZATION_STAMPED_MODELS


def test_rehome_catalog_covers_every_solution_owned_organization_stamp() -> None:
    """A new solution_id+organization_id table cannot silently miss promotion."""
    expected = {
        table.name
        for table in Base.metadata.tables.values()
        if table.name != "solutions"
        and "solution_id" in table.columns
        and "organization_id" in table.columns
    }
    actual = {model.__table__.name for model in ORGANIZATION_STAMPED_MODELS}
    assert actual == expected
