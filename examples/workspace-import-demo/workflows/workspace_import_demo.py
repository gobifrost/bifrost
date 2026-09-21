from bifrost import workflow


@workflow(name="workspace_import_demo")
async def workspace_import_demo() -> dict:
    """Small visible payload used to exercise reviewed workspace imports."""
    return {
        "message": "Imported from the Workspace Import Demo bundle",
        "bundle_version": "1.0.0",
    }
