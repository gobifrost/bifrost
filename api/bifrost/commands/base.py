"""Shared infrastructure for entity mutation CLI commands.

Provides:

* :func:`run_async` — runs an async command body inside ``asyncio.run`` and
  surfaces ref resolution / HTTP errors as process exit codes.
* :func:`output_result` — writes a single response object to stdout (``--json``
  for machine output, human-friendly fallback otherwise).
* :func:`json_output_option` — the ``--json`` Click option shared by every
  command (via the ``ctx.obj["json_output"]`` flag).
* :func:`pass_resolver` — decorator that injects a per-invocation
  :class:`bifrost.refs.RefResolver` + :class:`bifrost.client.BifrostClient`
  into the command function.

Error-surfacing contract (matches the plan's Task 4):

* :class:`bifrost.refs.RefNotFoundError` → exit 2.
* :class:`bifrost.refs.AmbiguousRefError` → exit 2 with candidate list.
* HTTP 4xx → exit 1 with the server's error body.
* HTTP 4xx with ``403`` → exit 1; error body's ``required_role`` / ``detail``
  surfaced alongside the standard body (server error shapes vary; we print
  the full body plus a ``required`` hint when the shape matches).
* HTTP 5xx → exit 3 with a ``retry later`` hint.

There is **no ``--org`` scoping flag**. Disambiguation by org-via-name is a
known antipattern (ambiguous names are an opportunity for the user to learn
the UUID). See Task 1 in the plan.
"""

from __future__ import annotations

import asyncio
import functools
import json
import sys
from typing import Any, Callable, Coroutine

import click
import httpx

from bifrost.client import BifrostClient
from bifrost.refs import AmbiguousRefError, RefNotFoundError, RefResolver

_ExitCode = int


def _set_json_output(ctx: click.Context, _param: Any, value: bool) -> bool:
    ctx.ensure_object(dict)
    # Only overwrite when the flag is explicitly set, so a group-level
    # ``--json`` isn't reset to False by an unspecified subcommand-level
    # default. Click's ``Option`` wires this callback even when the user
    # didn't type ``--json``, so a default-False call comes through too.
    if value:
        ctx.obj["json_output"] = True
    return value


def _build_json_option() -> click.Option:
    """Construct the shared ``--json`` Click Option directly.

    ``json_output_option`` (the decorator form) is what subcommand callbacks
    use during decoration. For appending the option to a built ``Command``
    after the fact (see ``_EntityGroup.add_command``) we need the raw
    ``Option`` object rather than running the decorator against an empty
    callback.
    """
    return click.Option(
        ["--json", "json_output"],
        is_flag=True,
        default=False,
        expose_value=False,
        callback=_set_json_output,
        help="Emit JSON instead of human-readable output.",
    )


def json_output_option(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Add a shared ``--json`` flag that routes through the Click context obj.

    Used directly only by the group-level decorator built in ``entity_group``;
    subcommands get the same flag attached automatically by ``_EntityGroup.add_command``
    via ``_build_json_option``.
    """
    return click.option(
        "--json",
        "json_output",
        is_flag=True,
        default=False,
        expose_value=False,
        callback=_set_json_output,
        help="Emit JSON instead of human-readable output.",
    )(fn)


def _json_requested(ctx: click.Context | None) -> bool:
    if ctx is None:
        return False
    obj = ctx.obj if isinstance(ctx.obj, dict) else None
    return bool(obj and obj.get("json_output"))


def output_result(result: Any, *, ctx: click.Context | None = None) -> None:
    """Write ``result`` to stdout.

    Uses JSON output when ``--json`` is set on the context, otherwise writes
    a compact human-readable rendering. The human rendering is deliberately
    minimal: dicts become ``key: value`` lines, lists become one item per
    line. Rich per-entity formatting is each command's responsibility.
    """
    if _json_requested(ctx):
        click.echo(json.dumps(result, indent=2, sort_keys=True, default=str))
        return
    if isinstance(result, dict):
        for key in sorted(result):
            click.echo(f"{key}: {result[key]}")
        return
    if isinstance(result, list):
        for item in result:
            if isinstance(item, dict) and "id" in item and "name" in item:
                click.echo(f"{item['id']}\t{item['name']}")
            else:
                click.echo(str(item))
        return
    click.echo(str(result))


def _format_candidates(candidates: list[dict[str, Any]]) -> str:
    lines = []
    for cand in candidates:
        org = cand.get("org_id")
        suffix = f" (org: {org})" if org else ""
        lines.append(f"  - {cand.get('name')}  [{cand.get('uuid')}]{suffix}")
    return "\n".join(lines)


def _current_ctx() -> click.Context | None:
    try:
        return click.get_current_context(silent=True)
    except RuntimeError:
        return None


def _emit_error(payload: dict[str, Any], human_lines: list[str]) -> None:
    """Emit an error to stderr in JSON or human format based on --json.

    ``payload`` is the machine-readable shape (single-line JSON when --json is
    set). ``human_lines`` are the stderr lines used when --json is NOT set.
    """
    if _json_requested(_current_ctx()):
        click.echo(json.dumps(payload, default=str), err=True)
        return
    for line in human_lines:
        click.echo(line, err=True)


def _print_http_error(exc: httpx.HTTPStatusError) -> _ExitCode:
    status = exc.response.status_code
    body_json: Any = None
    try:
        body_json = exc.response.json()
    except ValueError:
        body_json = None
    body_text_raw = exc.response.text or ""

    required: Any = None
    if status == 403 and isinstance(body_json, dict):
        required = (
            body_json.get("required_role")
            or body_json.get("required_permission")
            or body_json.get("required")
        )

    payload: dict[str, Any] = {
        "error": "http_error",
        "status": status,
        "reason": exc.response.reason_phrase,
    }
    if body_json is not None:
        payload["body"] = body_json
    elif body_text_raw:
        payload["body"] = body_text_raw
    if required:
        payload["required"] = required

    human: list[str] = [f"HTTP {status} {exc.response.reason_phrase}"]
    if body_json is not None:
        human.append(json.dumps(body_json, indent=2, sort_keys=True))
    elif body_text_raw:
        human.append(body_text_raw)
    if required:
        human.append(f"Required: {required}")
    if 500 <= status < 600:
        human.append("Server error — retry later.")

    _emit_error(payload, human)
    if 500 <= status < 600:
        return 3
    return 1


def _handle_exception(exc: BaseException) -> _ExitCode:
    if isinstance(exc, RefNotFoundError):
        _emit_error(
            {"error": "ref_not_found", "kind": exc.kind, "value": exc.value},
            [f"Could not find {exc.kind} matching {exc.value!r}."],
        )
        return 2
    if isinstance(exc, AmbiguousRefError):
        _emit_error(
            {
                "error": "ambiguous_ref",
                "kind": exc.kind,
                "value": exc.value,
                "candidates": exc.candidates,
            },
            [
                f"Multiple {exc.kind} entities match {exc.value!r} — pass the UUID instead.",
                _format_candidates(exc.candidates),
            ],
        )
        return 2
    if isinstance(exc, httpx.HTTPStatusError):
        return _print_http_error(exc)
    if isinstance(exc, RuntimeError) and "Not logged in" in str(exc):
        _emit_error(
            {"error": "not_logged_in", "message": str(exc)},
            [str(exc)],
        )
        return 1
    raise exc


def run_async(coro_fn: Callable[..., Coroutine[Any, Any, Any]]) -> Callable[..., Any]:
    """Wrap an async Click command body in ``asyncio.run`` with error surfacing.

    Usage::

        @orgs_group.command("delete")
        @click.argument("ref")
        @run_async
        async def delete(ref: str, *, client: BifrostClient, resolver: RefResolver) -> None:
            uuid = await resolver.resolve("org", ref)
            response = await client.delete(f"/api/organizations/{uuid}")
            response.raise_for_status()
    """

    @functools.wraps(coro_fn)
    def wrapper(*args: Any, **kwargs: Any) -> None:
        try:
            asyncio.run(coro_fn(*args, **kwargs))
        except SystemExit:
            raise
        except BaseException as exc:  # noqa: BLE001 - surfaced as exit codes
            code = _handle_exception(exc)
            sys.exit(code)

    return wrapper


def _apply_flags(
    flags: list[Callable[[Callable[..., Any]], Callable[..., Any]]],
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Apply a list of Click option decorators in stable order.

    DTO-driven flags are built in :func:`bifrost.dto_flags.build_cli_flags`
    and need to be attached to the underlying command function before
    ``pass_resolver`` / ``run_async`` wrap it. Apply in reverse so the first
    decorator in the list lands closest to the function (leftmost in the
    final ``--help``).
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        for flag in reversed(flags):
            fn = flag(fn)
        return fn

    return decorator


def pass_resolver(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Inject a fresh :class:`BifrostClient` + :class:`RefResolver` per invocation.

    The client is pulled from ``BifrostClient.get_instance(require_auth=True)``
    so a missing credentials file surfaces as the standard "Not logged in"
    error. The resolver owns a per-invocation cache.
    """

    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        client = BifrostClient.get_instance(require_auth=True)
        resolver = RefResolver(client)
        kwargs["client"] = client
        kwargs["resolver"] = resolver
        return fn(*args, **kwargs)

    return wrapper


class _EntityGroup(click.Group):
    """Click group that auto-applies ``--json`` to every subcommand.

    Click's parser only accepts options at the position they're declared,
    so a flag attached at the group level (``bifrost tables --json list``)
    won't parse after the subcommand (``bifrost tables list --json``). Most
    users type the latter. We attach the option at both levels: the group
    decorator handles the pre-subcommand position, and ``add_command``
    appends a fresh ``--json`` option to each subcommand so the post-
    subcommand position also works. Both writes target ``ctx.obj["json_output"]``
    so ``output_result`` reads a consistent value regardless of position.
    """

    def add_command(
        self, cmd: click.Command, name: str | None = None
    ) -> None:
        if not any(p.name == "json_output" for p in cmd.params):
            cmd.params.append(_build_json_option())
        super().add_command(cmd, name)


def entity_group(name: str, help_text: str) -> click.Group:
    """Factory for a Click group with the project's shared conventions.

    Attaches the ``--json`` option at the group level *and* on every
    sub-command, so callers can place ``--json`` either before or after the
    subcommand name.
    """

    @click.group(name=name, help=help_text, cls=_EntityGroup)
    @json_output_option
    @click.pass_context
    def group(ctx: click.Context) -> None:
        ctx.ensure_object(dict)

    return group


__all__ = [
    "_apply_flags",
    "entity_group",
    "json_output_option",
    "output_result",
    "pass_resolver",
    "run_async",
]
