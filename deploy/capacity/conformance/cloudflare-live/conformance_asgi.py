"""Disposable ASGI entrypoint for Cloudflare normal-image conformance only."""

from asgi_seam import disable_capacity_loops


# Guard and patch before importing the canonical application.  This is not a
# production flag or endpoint: use it only through the isolated Compose setup.
disable_capacity_loops()

from src.main import app  # noqa: E402
