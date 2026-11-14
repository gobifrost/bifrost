"""Package qualification: do the five blocked workspace packages import and work
inside a real Python Worker (Pyodide), and do their dependencies come along?

Run locally:
    npx wrangler dev --config wrangler.jsonc --port 8822 --ip 127.0.0.1
    curl -s http://127.0.0.1:8822 | jq

Each check performs a trivial real operation, not just an import, so a package
that imports but cannot function is recorded as a failure.
"""
import io

from workers import Response, WorkerEntrypoint


def _pypdf():
    import pypdf
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buffer = io.BytesIO()
    writer.write(buffer)
    return {"version": getattr(pypdf, "__version__", None), "pdf_bytes": len(buffer.getvalue())}


def _beautifulsoup4():
    import bs4

    soup = bs4.BeautifulSoup("<p>hello <b>world</b></p>", "html.parser")
    return {"version": bs4.__version__, "text": soup.p.get_text()}


def _mammoth():
    import mammoth

    return {"version": getattr(mammoth, "__version__", None),
            "has_convert_to_html": hasattr(mammoth, "convert_to_html")}


def _openrouter():
    import openrouter

    return {"version": getattr(openrouter, "__version__", None),
            "has_client": hasattr(openrouter, "OpenRouter")}


def _reportlab():
    import reportlab
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    drawing = canvas.Canvas(buffer)
    drawing.drawString(72, 720, "bifrost")
    drawing.showPage()
    drawing.save()
    return {"version": reportlab.Version, "pdf_bytes": len(buffer.getvalue())}


def _pillow():
    import PIL
    from PIL import Image

    image = Image.new("RGB", (8, 8), (1, 2, 3))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return {"version": PIL.__version__, "png_bytes": len(buffer.getvalue())}


CHECKS = {
    "pypdf": _pypdf,
    "beautifulsoup4": _beautifulsoup4,
    "mammoth": _mammoth,
    "openrouter": _openrouter,
    "reportlab": _reportlab,
    "pillow": _pillow,
}


def _run(name, fn):
    try:
        return {"ok": True, **(fn() or {})}
    except Exception as error:  # noqa: BLE001 - qualification must record any failure
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        return Response.json({name: _run(name, fn) for name, fn in CHECKS.items()})
