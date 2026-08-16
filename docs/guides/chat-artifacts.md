# Chat artifacts and custom workflow tools

Chat treats generated files as durable, authorized message attachments. The
model never emits PDF, DOCX, XLSX, image, or video bytes directly. It calls a typed Bifrost
tool; trusted Python code renders the file, validates it, stores it, and returns
a canonical `ArtifactRef`.

## Built-in formats

- PDF uses ReportLab Platypus flow layout.
- DOCX uses python-docx.
- XLSX uses openpyxl with styled headers, filters, frozen header rows, and
  content-sized columns.
- CSV, HTML, Markdown, JSON, and plain text use UTF-8 output. JSON is parsed and
  normalized before publication.
- Images use the dedicated image model configured by an administrator. Bifrost
  calls the provider's media endpoint and validates the returned raster bytes.
- Videos use the dedicated video model and the shared durable platform-job
  runner. Chat can finish responding while generation continues; the completed
  MP4 or WebM is attached to the original tool message.

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

Image and video generation are configured as dedicated models under
**Generation Models**. They are intentionally separate from Fast, Balanced,
and Pro because those Chat tiers only need capability evidence for image input,
PDF input, and tool calling. Leaving either generation model blank keeps that
generation type unavailable.

OpenRouter-backed image generation uses its dedicated synchronous Image API.
OpenRouter video generation uses its asynchronous Video API and is mirrored by
a Bifrost platform job, so progress, cancellation, failure, and completion use
the same notification transport as other durable platform operations. OpenAI's
standard image endpoint is also supported. Video generation currently requires
OpenRouter; unsupported provider combinations fail explicitly rather than
silently substituting another model.

## Returning an artifact from a workflow tool

Return the opaque `ArtifactRef` created by the SDK. The public reference contains
only an ID, filename, MIME type, and size; it never exposes an S3 key, filesystem
path, scope coordinate, signed URL, or base64 payload.

```python
from bifrost import artifacts, tool


@tool
async def create_customer_brief(customer: str):
    return await artifacts.create_document(
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
```

Chat recognizes the marker `type: "bifrost_artifact"`, authorizes the opaque ID,
associates that same artifact with the conversation without copying it, and
emits an `artifact_ready` stream event. MCP recognizes the identical return.

## The artifact workspace

`ArtifactRef` is the transport receipt, not the agent's working-directory
model. Every Chat conversation and root agent run has one artifact workspace.
Uploads and generated files are written under that workspace's S3 prefix with
a normalized logical path. Nested workflow tools and delegated agents inherit
the root workspace, so a later step can discover and read files produced by an
earlier step without the user copying opaque IDs between calls.

- Chat uses the conversation ID as the workspace ID.
- Workflow and autonomous runs default to their root execution/run ID.
- A nested workflow inherits its caller's workspace ID.
- Reusing a logical filename creates a new stored version; workspace listing
  returns the newest version at each path.
- Retention settings govern cleanup. The S3 key remains private and is never
  part of an agent, SDK, Chat, or MCP contract.

Workflow code can treat this like a small shared working directory:

```python
from bifrost import artifacts, tool


@tool
async def compose_report():
    files = await artifacts.list()
    image = next(file for file in files if file.content_type.startswith("image/"))
    image_bytes = await artifacts.read(image)
    # Inspect or transform image_bytes here, or refer to image.filename from
    # a schema-first document section.
    return await artifacts.create_document(
        "Field Report.pdf",
        format="pdf",
        title="Field report",
        sections=[
            {
                "heading": "Photograph",
                "images": [{"path": image.filename, "caption": "Generated earlier"}],
            }
        ],
    )
```

The same SDK surface can call the configured media models:

```python
from bifrost import ai

image = await ai.create_image(
    "A clean isometric launch dashboard on a dark navy background",
    filename="Launch Concept",
)

video = await ai.create_video(
    "A slow camera move across the launch dashboard, subtle ambient motion",
    filename="Launch Loop",
)
```

`create_image` returns after the provider responds. `create_video` enqueues a
durable platform job, waits for its terminal state from the SDK, and returns the
same opaque `ArtifactRef`. If the caller times out, the exception includes the job
ID and the platform job continues independently.

## Explicit artifact input and MCP transport

An explicit `ArtifactRef` is still useful when a workflow declares a particular
file as an input, or when an artifact crosses an MCP boundary. MCP tool
arguments are JSON, so that explicit input is represented by the canonical
reference rather than a filesystem path or inline base64 payload. Validate the
object and let the SDK resolve its authorized artifact ID.

```python
from bifrost import ArtifactRef, artifacts, tool


@tool
async def inspect_artifact(artifact: dict) -> dict:
    ref = ArtifactRef.model_validate(artifact)
    data = await artifacts.read(ref)
    processed = transform(data)
    return await artifacts.write(
        "Processed Artifact.pdf",
        processed,
        content_type="application/pdf",
    )
```

For MCP output, image artifacts become `ImageContent`; videos and other files become
short-lived `ResourceLink` blocks while the JSON `ArtifactRef` remains in
`structuredContent`. MCP Apps/widgets are independent of this transport and
are intentionally outside the first Chat artifact release.

The shared workspace itself is a Bifrost runtime capability, not an MCP
filesystem extension. A Bifrost-hosted MCP/workflow tool automatically operates
inside the inherited workspace. A remote MCP server receives explicit
`ArtifactRef` inputs and returns the same reference-shaped structured output;
Bifrost performs the authorized byte read or resource-link projection at the
platform boundary.
