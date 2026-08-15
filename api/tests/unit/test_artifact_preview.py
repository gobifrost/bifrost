from shared.artifact_generation import generate_document, generate_spreadsheet
from shared.artifact_preview import preview_office_artifact
from src.models.contracts.artifacts import DocumentArtifactSpec, SpreadsheetArtifactSpec


def test_docx_preview_escapes_content() -> None:
    artifact = generate_document(
        DocumentArtifactSpec.model_validate(
            {
                "filename": "brief.docx",
                "format": "docx",
                "title": "A <safe> brief",
                "sections": [
                    {"heading": "Summary", "paragraphs": ["No <script> tags"]}
                ],
            }
        )
    )

    preview = preview_office_artifact(artifact.content, artifact.content_type)

    assert preview is not None
    assert "A &lt;safe&gt; brief" in preview
    assert "<script>" not in preview


def test_xlsx_preview_is_bounded_and_contains_sheet() -> None:
    artifact = generate_spreadsheet(
        SpreadsheetArtifactSpec.model_validate(
            {
                "filename": "dashboard.xlsx",
                "sheets": [
                    {
                        "name": "Summary",
                        "columns": ["Metric", "Value"],
                        "rows": [["Adoption", 42]],
                    }
                ],
            }
        )
    )

    preview = preview_office_artifact(artifact.content, artifact.content_type)

    assert preview is not None
    assert "<h2>Summary</h2>" in preview
    assert "Adoption" in preview
