"""
Server-side TSX/JSX compiler and Tailwind CSS generator for Bifrost app files.

Uses Node.js subprocesses:
- @babel/standalone to compile app source files (same pipeline as client)
- @tailwindcss/node to generate per-app Tailwind CSS from class candidates
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.services.node_subprocess import (
    APP_COMPILER_NODE_POLICY,
    NodeScriptInputLimitError,
    NodeScriptLimitError,
    TAILWIND_NODE_POLICY,
    run_node_script,
)
from src.services.app_resource_limits import (
    AppSourceCompilationError,
    AppSourceLimitError,
    validate_app_source_metadata,
    validate_app_source_write,
)

logger = logging.getLogger(__name__)

COMPILE_SCRIPT = Path(__file__).parent / "compile.js"
TAILWIND_SCRIPT = Path(__file__).parent / "tailwind.js"


@dataclass
class CompileResult:
    """Result of compiling a single file."""
    path: str
    success: bool
    compiled: str | None = None
    error: str | None = None
    default_export: str | None = None
    named_exports: list[str] = field(default_factory=list)


class TailwindGenerationError(ValueError):
    """The required Tailwind build step could not produce CSS."""


class AppCompilerInfrastructureError(RuntimeError):
    """The compiler process or its response was unavailable or malformed."""


class AppCompilerService:
    """Compile TSX/JSX source files via Node.js subprocess."""

    async def compile_file(self, source: str, path: str = "component.tsx") -> CompileResult:
        """Compile a single file."""
        results = await self.compile_batch([{"path": path, "source": source}])
        return results[0]

    async def compile_batch(self, files: list[dict]) -> list[CompileResult]:
        """Compile multiple files in a single Node.js invocation."""
        if not files:
            return []

        _validate_compile_input(files)
        input_data = json.dumps({"files": files})

        try:
            returncode, stdout, stderr = await run_node_script(
                COMPILE_SCRIPT, input_data.encode(), policy=APP_COMPILER_NODE_POLICY
            )

            if returncode != 0:
                error_msg = stderr.decode().strip() or "Node process exited with error"
                logger.error(f"Compiler process failed: {error_msg}")
                raise AppCompilerInfrastructureError(error_msg)

            try:
                output = json.loads(stdout.decode())
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise AppCompilerInfrastructureError(
                    f"Compiler returned invalid JSON: {exc}"
                ) from exc

            if "error" in output:
                raise AppCompilerInfrastructureError(str(output["error"]))

            raw_results = output.get("results")
            if not isinstance(raw_results, list):
                raise AppCompilerInfrastructureError("Compiler response omitted its results list")
            expected_paths = [file["path"] for file in files]
            if len(raw_results) != len(expected_paths):
                raise AppCompilerInfrastructureError(
                    "Compiler response did not return exactly one result for every source file"
                )

            results: list[CompileResult] = []
            result_paths: set[str] = set()
            for item in raw_results:
                if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                    raise AppCompilerInfrastructureError("Compiler response has a malformed result")
                path = item["path"]
                if path in result_paths:
                    raise AppCompilerInfrastructureError(
                        "Compiler response returned duplicate source paths"
                    )
                result_paths.add(path)
                if item.get("error"):
                    results.append(CompileResult(
                        path=path,
                        success=False,
                        error=str(item["error"]),
                    ))
                else:
                    if not isinstance(item.get("compiled"), str):
                        raise AppCompilerInfrastructureError(
                            f"Compiler response omitted compiled output for {path!r}"
                        )
                    default_export = item.get("defaultExport")
                    named_exports = item.get("namedExports", [])
                    if default_export is not None and not isinstance(default_export, str):
                        raise AppCompilerInfrastructureError(
                            f"Compiler response has an invalid default export for {path!r}"
                        )
                    if not isinstance(named_exports, list) or not all(
                        isinstance(name, str) for name in named_exports
                    ):
                        raise AppCompilerInfrastructureError(
                            f"Compiler response has invalid named exports for {path!r}"
                        )
                    results.append(CompileResult(
                        path=path,
                        success=True,
                        compiled=item["compiled"],
                        default_export=default_export,
                        named_exports=named_exports,
                    ))
            if result_paths != set(expected_paths):
                raise AppCompilerInfrastructureError(
                    "Compiler response paths do not match the submitted source files"
                )
            return results

        except NodeScriptInputLimitError as exc:
            raise AppSourceLimitError(str(exc)) from exc
        except NodeScriptLimitError:
            raise
        except FileNotFoundError:
            logger.error("Node.js not found — cannot compile app files")
            raise AppCompilerInfrastructureError("Node.js not available for App compilation")
        except AppCompilerInfrastructureError:
            raise
        except Exception as e:
            logger.exception(f"Compilation failed: {e}")
            raise AppCompilerInfrastructureError(str(e)) from e


def _validate_compile_input(files: list[dict]) -> None:
    """Validate batch shape and source bounds before serializing to Node."""
    paths: set[str] = set()
    sizes: dict[str, int] = {}
    for item in files:
        path = item.get("path") if isinstance(item, dict) else None
        source = item.get("source") if isinstance(item, dict) else None
        if not isinstance(path, str) or not path:
            raise AppSourceCompilationError("Compiler input has a missing source path")
        if not isinstance(source, str):
            raise AppSourceCompilationError(f"Compiler input {path!r} is not text")
        if path in paths:
            raise AppSourceCompilationError(f"Compiler input contains duplicate path {path!r}")
        paths.add(path)
        try:
            encoded = source.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise AppSourceCompilationError(
                f"Compiler input {path!r} is not valid UTF-8 text"
            ) from exc
        validate_app_source_write(path, encoded)
        sizes[path] = len(encoded)
    validate_app_source_metadata(sizes)


# Extract all string literals from compiled JS output, then split into tokens.
# Over-extraction is fine — Tailwind silently ignores unknown candidates.
# This is intentionally broad to handle both JSX (className="...") and compiled
# output (className: "...") without fragile pattern matching.
_STRING_LITERAL = re.compile(r'"([^"]{1,500})"')
# Split tokens on whitespace ONLY. Splitting on commas breaks Tailwind v4
# arbitrary values that legitimately contain commas — e.g.
# `lg:grid-cols-[minmax(0,1fr)_360px]` or `bg-[rgb(0,0,0)]`.
_TOKEN_SPLIT = re.compile(r"\s+")
# Tailwind classes contain hyphens, brackets, parens, colons, slashes, commas.
# Tailwind v4 arbitrary values heavily use `()` (var(), minmax(), calc()) and
# `,` (rgb commas, minmax args), so both must pass the candidate filter.
# Single-word utilities (flex, grid, hidden, etc.) also need to pass through.
_LOOKS_LIKE_CLASS = re.compile(
    r"^!?-?[a-z][a-z0-9:\-/\[\](),.=#%_*>~&+]*$",
    re.IGNORECASE,
)


class AppTailwindService:
    """Generate Tailwind CSS for app source files via @tailwindcss/node."""

    @staticmethod
    def extract_candidates(sources: list[str]) -> list[str]:
        """Extract Tailwind class candidates from source strings.

        Scans all string literals and splits into whitespace-separated tokens.
        Over-extraction is harmless — Tailwind ignores unknown candidates.
        """
        candidates: set[str] = set()
        for source in sources:
            for match in _STRING_LITERAL.finditer(source):
                for token in _TOKEN_SPLIT.split(match.group(1)):
                    token = token.strip()
                    if token and _LOOKS_LIKE_CLASS.match(token):
                        candidates.add(token)
        return sorted(candidates)

    @staticmethod
    async def generate_css(sources: list[str]) -> str | None:
        """Extract candidates from sources and generate Tailwind CSS.

        Returns the generated CSS string, or None on failure.

        Candidates-only mode: produces utility CSS for the class names
        found in `sources`. Does NOT process user CSS files — see
        `generate_css_pipeline` for the pipeline mode that supports
        @apply / @layer / per-app tailwind.config.
        """
        candidates = AppTailwindService.extract_candidates(sources)
        if not candidates:
            return None

        return await AppTailwindService._invoke({"candidates": candidates})

    @staticmethod
    async def generate_css_pipeline(
        code_sources: list[str],
        user_css: list[tuple[str, str]],
        config_path: str | None = None,
    ) -> str | None:
        """Run the full Tailwind v4 pipeline against app source.

        Args:
            code_sources: contents of .tsx/.ts/.jsx/.js files, scanned for
                Tailwind class candidates.
            user_css: list of (filename, content) tuples for the app's
                .css files. Concatenated into the Tailwind input so
                @apply / @layer / @theme directives in user CSS are
                processed against the utility layer.
            config_path: optional absolute path to a per-app
                tailwind.config.js. Threaded through as @config.

        Returns the generated CSS string (utilities + processed user CSS),
        or None on failure.
        """
        candidates = AppTailwindService.extract_candidates(code_sources)
        payload: dict[str, object] = {
            "candidates": candidates,
            "user_css": [
                {"path": p, "content": c} for p, c in user_css
            ],
        }
        if config_path:
            payload["config_path"] = config_path

        # Skip the subprocess only when there's literally nothing to do —
        # no candidates AND no user CSS. (User CSS alone with no candidates
        # is still a real input; e.g. a stylesheet declaring CSS variables.)
        if not candidates and not user_css:
            return None

        return await AppTailwindService._invoke(payload)

    @staticmethod
    async def _invoke(payload: dict[str, object]) -> str | None:
        """Send a payload to tailwind.js, returning CSS or a concrete failure."""
        input_data = json.dumps(payload)

        try:
            returncode, stdout, stderr = await run_node_script(
                TAILWIND_SCRIPT, input_data.encode(), policy=TAILWIND_NODE_POLICY
            )

            if returncode != 0:
                error_msg = stderr.decode().strip() or "tailwind.js exited with error"
                logger.error(f"Tailwind CSS generation failed: {error_msg}")
                raise TailwindGenerationError(error_msg)

            output = json.loads(stdout.decode())
            if output.get("error"):
                logger.error(f"Tailwind CSS generation error: {output['error']}")
                raise TailwindGenerationError(str(output["error"]))

            css = output.get("css", "")
            return css if css else None

        except FileNotFoundError:
            logger.error("Node.js not found — cannot generate Tailwind CSS")
            raise TailwindGenerationError("Node.js not available for Tailwind CSS generation")
        except TailwindGenerationError:
            raise
        except Exception as e:
            logger.exception(f"Tailwind CSS generation failed: {e}")
            raise TailwindGenerationError(str(e)) from e
