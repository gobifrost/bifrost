def test_solution_export_store_is_removed():
    import src.services.solutions.export as export_mod

    assert not hasattr(export_mod, "SolutionExportStore"), (
        "stale export store must be deleted — export rebuilds live now"
    )
