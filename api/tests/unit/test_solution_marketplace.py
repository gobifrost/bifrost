from src.models.orm.solutions import Solution


def test_solution_has_marketplace_columns():
    cols = set(Solution.__table__.columns.keys())
    assert {"repo_subpath", "git_ref", "update_available_version"} <= cols


from bifrost.solution_descriptor import SolutionDescriptor


def test_descriptor_carries_repo_subpath_and_ref():
    d = SolutionDescriptor(slug="s", name="S", repo_subpath="microsoft-csp", git_ref="v1.2.0")
    assert d.repo_subpath == "microsoft-csp"
    assert d.git_ref == "v1.2.0"
    d2 = SolutionDescriptor(slug="s", name="S")
    assert d2.repo_subpath is None and d2.git_ref is None
