# Chat artifacts and custom workflow tools

Chat treats generated files as durable, authorized message attachments. The
model never emits PDF, DOCX, or XLSX bytes directly. It calls a typed Bifrost
tool; trusted Python code renders the file, validates it, stores it, and returns
a canonical `ArtifactRef`.

## Built-in formats

- PDF uses ReportLab Platypus flow layout.
- DOCX uses python-docx.
- XLSX uses openpyxl with styled headers, filters, frozen header rows, and
  content-sized columns.
- CSV, HTML, Markdown, JSON, and plain text use UTF-8 output. JSON is parsed and
  normalized before publication.

The dependency and design survey deliberately favors maintained format
libraries over hand-authored OOXML or PDF coordinates:

| Project | Used for | License | Runtime decision |
| --- | --- | --- | --- |
| [ReportLab](https://pypi.org/project/reportlab/) | PDF generation with Platypus flowables | BSD | Runs in the existing Python API/worker image and writes to memory; no LibreOffice, Chromium, or host process is required. |
| [python-docx](https://github.com/python-openxml/python-docx) | DOCX generation | MIT | Uses its supported high-level document/table API; Bifrost does not author OOXML directly. |
| [openpyxl](https://pypi.org/project/openpyxl/) | XLSX generation and validation | MIT | Uses typed workbook APIs. Bifrost already ships `defusedxml`, which is the upstream-recommended XML hardening dependency. |
| [HyperAgent](https://github.com/hyperlight-dev/hyperagent) | Design reference for typed PDF/XLSX modules, validation, profiles, and declared file output | Apache-2.0 | Inspiration only; no HyperAgent code is bundled. Its hardware-virtualized JavaScript sandbox is pre-release and is not a runtime dependency. |

The first release intentionally omits arbitrary model-authored Python,
LibreOffice conversion, PPTX, and browser-based document rendering. Those add
substantially different isolation and runtime requirements and belong with the
Code Builder sandbox. A commercial Office CLI survey also informed the
capability/discover/prepare/output event shape, but no closed-source code or
prompt text was copied.

The built-in Chat tools are only exposed when the selected Fast, Balanced, or
Pro model is configured for tool calling. Image and PDF input are checked
separately from artifact output: a text-only model can still create a PDF if it
supports tools.

## Model capability evidence

Settings first asks the OpenRouter public catalog for a recognized OpenRouter
model. That request does not call the configured provider. Unknown models and
custom endpoints stay conservative until an administrator runs **Verify with
provider**, which performs bounded text, forced-tool, image-input, and PDF-input
conformance calls. The result is stored with its source, timestamp, and a
fingerprint of provider, endpoint, and model; changing any of those fields
invalidates it.

Native image output remains separate. The shared LLM response contract does
not currently transport provider-native image bytes, so an unknown provider's
image output must be asserted manually or obtained from a catalog record.

## Returning an artifact from a workflow tool

Use `bifrost.artifacts` so the result carries a managed-file path and scope. Do
not return raw S3 keys, host filesystem paths, or base64 blobs.

```python
from bifrost import artifacts, tool


@tool
async def create_customer_brief(customer: str) -> dict:
    artifact = await artifacts.create_document(
        f"{customer}-brief.pdf",
        format="pdf",
        title=f"{customer} brief",
        sections=[
            {
                "heading": "Decision",
                "paragraphs": ["Proceed with the proposed rollout."],
                "bullets": ["Owner assigned", "Review date confirmed"],
            }
        ],
    )
    return {"artifact": artifact.model_dump(mode="json")}
```

Chat recognizes the marker `type: "bifrost_artifact"`, verifies that the scope
belongs to the conversation, copies the file into Chat's durable attachment
store, and emits an `artifact_ready` stream event.

## Accepting an artifact as tool input

MCP tool arguments are JSON, so a file input is represented by the canonical
reference rather than an inline protocol binary part. Validate the object and
let the SDK resolve its authorized managed-file location.

```python
from bifrost import ArtifactRef, artifacts, tool


@tool
async def inspect_artifact(artifact: dict) -> dict:
    ref = ArtifactRef.model_validate(artifact)
    data = await artifacts.read(ref)
    return {
        "filename": ref.filename,
        "content_type": ref.content_type,
        "size_bytes": len(data),
    }
```

For MCP output, image artifacts become `ImageContent`; other files become
short-lived `ResourceLink` blocks while the JSON `ArtifactRef` remains in
`structuredContent`. MCP Apps/widgets are independent of this transport and
are intentionally outside the first Chat artifact release.
