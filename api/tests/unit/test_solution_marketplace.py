from src.models.orm.solutions import Solution


def test_solution_has_marketplace_columns():
    cols = set(Solution.__table__.columns.keys())
    assert {"repo_subpath", "git_ref", "update_available_version"} <= cols
