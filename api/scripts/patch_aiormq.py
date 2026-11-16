"""Remove aiormq 7.2.5's Python 3.16-incompatible coroutine marker.

Upstream already marks connect with inspect.markcoroutinefunction on Python
3.12+. Its additional private asyncio marker was removed in Python 3.16.
Remove this build patch when upgrading to an upstream release with the fix.
"""

from importlib.metadata import distribution


def main() -> None:
    package = distribution("aiormq")
    if package.version != "7.2.5":
        raise RuntimeError("Reassess the aiormq coroutine-marker patch for the new version")
    path = package.locate_file("aiormq/connection.py")
    source = path.read_text()
    obsolete = (
        "connect._is_coroutine = asyncio.coroutines._is_coroutine"
        "  # ty: ignore[unresolved-attribute]  # noqa: E501\n"
    )
    if source.count(obsolete) != 1:
        raise RuntimeError("The expected aiormq coroutine marker changed")
    path.write_text(source.replace(obsolete, ""))


if __name__ == "__main__":
    main()
