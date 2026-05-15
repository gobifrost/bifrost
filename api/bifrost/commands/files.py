"""CLI commands for managing workspace files.

Implements the ``bifrost files`` sub-group. Each verb is a thin wrapper
around an ``api/bifrost/files.py`` SDK method, which in turn calls the
matching ``/api/files/*`` HTTP endpoint.

Verbs:

* ``bifrost files read <path> [--location LOC]`` -> SDK ``files.read``
* ``bifrost files write <path> (--content S | --from-file F | -) [--location LOC]``
  -> SDK ``files.write``
* ``bifrost files list [directory] [--location LOC]`` -> SDK ``files.list``
* ``bifrost files delete <path> [--location LOC]`` -> SDK ``files.delete``
* ``bifrost files exists <path> [--location LOC]`` -> SDK ``files.exists``;
  exits 0 if exists, 1 if not
* ``bifrost files search <query> [--regex] [--case-sensitive]
  [--include GLOB] [--max-results N]`` -> SDK ``files.search``

There is no ``stat`` verb -- the SDK only surfaces ``exists``. There is no
``mode`` flag -- workers always run in cloud mode; local mode is for the
laptop CLI where the user controls cwd directly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from bifrost.client import BifrostClient
from bifrost.files import files as files_sdk

from .base import entity_group, output_result, pass_resolver, run_async

files_group = entity_group("files", "Read, write, list, search workspace files.")


_LOCATION_HELP = (
    'Storage location. Reserved: "workspace" (default), "temp", "uploads". '
    'Freeform names (e.g. "reports") are also accepted.'
)


@files_group.command("read")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def read_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Read a workspace file and write its contents to stdout.

    Text files only. The SDK has `read_bytes` for binary; this CLI verb does not.
    """
    content = await files_sdk.read(path, location=location)
    # Avoid output_result()'s key:value dict formatting; raw stdout is what
    # shell pipelines and agents expect from a `read` verb.
    click.echo(content, nl=False)


@files_group.command("write")
@click.argument("path")
@click.argument("source", required=False)
@click.option("--content", "content_flag", default=None, help="Inline content to write.")
@click.option(
    "--from-file",
    "from_file",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Read content from a local file.",
)
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def write_cmd(
    ctx: click.Context,
    path: str,
    source: str | None,
    content_flag: str | None,
    from_file: str | None,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Write to a workspace file. Source: --content, --from-file, or `-` for stdin.

    Text files only. Pass --content "" to truncate an existing file.
    """
    sources = [s for s in (content_flag, from_file, source) if s is not None]
    if len(sources) != 1:
        raise click.UsageError(
            "Provide exactly one content source: --content, --from-file, or `-` for stdin."
        )

    if content_flag is not None:
        content = content_flag
    elif from_file is not None:
        content = Path(from_file).read_text()
    elif source == "-":
        content = sys.stdin.read()
    else:
        # Positional source other than `-` is not allowed (avoids ambiguity
        # with shell expansion accidentally passing a filename).
        raise click.UsageError(
            "Positional content must be `-` for stdin. Use --content or --from-file otherwise."
        )

    await files_sdk.write(path, content, location=location)


@files_group.command("list")
@click.argument("directory", required=False, default="")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def list_cmd(
    ctx: click.Context,
    directory: str,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """List files in a directory (default: location root)."""
    items = await files_sdk.list(directory=directory, location=location)
    output_result(items, ctx=ctx)


@files_group.command("delete")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def delete_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Delete a workspace file."""
    await files_sdk.delete(path, location=location)


@files_group.command("exists")
@click.argument("path")
@click.option("--location", default="workspace", help=_LOCATION_HELP)
@click.pass_context
@pass_resolver
@run_async
async def exists_cmd(
    ctx: click.Context,
    path: str,
    location: str,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Check if a file exists. Exits 0 if yes, 1 if no (script-friendly)."""
    found = await files_sdk.exists(path, location=location)
    output_result({"exists": found}, ctx=ctx)
    if not found:
        sys.exit(1)


@files_group.command("search")
@click.argument("query")
@click.option("--regex", "is_regex", is_flag=True, default=False, help="Treat query as a regex.")
@click.option("--case-sensitive", "case_sensitive", is_flag=True, default=False)
@click.option(
    "--include",
    "include_pattern",
    default="**/*",
    help='Glob restricting which files to search (default: "**/*").',
)
@click.option(
    "--max-results",
    "max_results",
    type=click.IntRange(1, 10000),
    default=1000,
    help="Maximum results to return (default: 1000, max: 10000).",
)
@click.pass_context
@pass_resolver
@run_async
async def search_cmd(
    ctx: click.Context,
    query: str,
    is_regex: bool,
    case_sensitive: bool,
    include_pattern: str,
    max_results: int,
    *,
    client: BifrostClient,  # noqa: ARG001
    resolver,  # noqa: ARG001
) -> None:
    """Search workspace file contents."""
    result = await files_sdk.search(
        query,
        case_sensitive=case_sensitive,
        is_regex=is_regex,
        include_pattern=include_pattern,
        max_results=max_results,
    )
    output_result(result, ctx=ctx)


__all__ = ["files_group"]
