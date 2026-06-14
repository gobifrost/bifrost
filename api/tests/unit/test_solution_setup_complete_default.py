def test_solution_has_setup_complete_default_true():
    from src.models.orm.solutions import Solution

    col = Solution.__table__.c.setup_complete
    assert col.default.arg is True or col.server_default is not None
