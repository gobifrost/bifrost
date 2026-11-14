# Package import qualification

Tests whether a candidate package **imports and works** inside a real Python
Worker (Pyodide), not merely whether a wheel exists. A package that imports but
cannot perform its job is recorded as a failure.

## Run

```sh
cd spikes/cloudflare-pyodide/backend/package-import
# python_modules/ next to index.py is the bundle under test (build artifact, not committed)
npx wrangler dev --config wrangler.jsonc --port 8822 --ip 127.0.0.1
curl -s http://127.0.0.1:8822/
```

Wrangler bundles `python_modules/` automatically. Build it from `pylock.toml`
(unpack each wheel into `python_modules/`); the Pyodide CDN wheels
(`pillow`, `pyyaml`, `pydantic-core`) come from the same index the existing lock
already uses.

## What is checked

| Package | Operation performed |
|---|---|
| `pypdf` | build a one-page PDF with `PdfWriter`, serialize it |
| `beautifulsoup4` | parse HTML with `html.parser`, read text |
| `mammoth` | import + assert `convert_to_html` is present |
| `openrouter` | import + assert the `OpenRouter` client exists |
| `reportlab` | draw a string on a canvas, serialize the PDF |
| `pillow` | create an image and PNG-encode it into a buffer |

## Measured result (2026-11-13)

All six pass functionally. Bundle arithmetic and cold/warm costs are in
[`../DEPENDENCY-CENSUS.md`](../DEPENDENCY-CENSUS.md) ("Result 2").

Bundle startup itself is unchanged (~120 ms cold with the enlarged bundle).
The cost is first-import of the heavy packages: ~4.9 s if one request imports all
five, then ~32–40 ms warm. Workflows that do not import them pay nothing.

## Limits

Local `workerd` only. This proves the packages work in the runtime; it does not
prove a deployed Worker, and it does not prove any specific workflow.
