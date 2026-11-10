"""Test-compose-only synthetic GitHub transport for subprocess job runners.

Mounted as /app/sitecustomize.py only in the test executors and activated only
for the isolated PlatformJob runner, never health probes or execution templates. Real credentials
retain the normal transport. Synthetic App JWTs are verified by the API fixture,
which owns the ephemeral signing key; no production authentication is changed.
"""
import sys

if "src.jobs.platform.runner" in sys.orig_argv:
    import importlib.abc
    import importlib.machinery

    def install_transport(module):
        # Python 3.15 initializes extension-module runtime state after sitecustomize.
        # Load Pydantic and provider clients only during normal runner imports.
        import base64

        import httpx
        import jwt

        class FixtureTransport(httpx.AsyncBaseTransport):
            def __init__(self):
                self.external = httpx.AsyncHTTPTransport()

            async def handle_async_request(self, request):
                token = request.headers.get("Authorization", "").removeprefix("Bearer ")
                fixture = token == "fixture-git-app-user" or token.startswith("fixture-git-app-installation")
                if not fixture:
                    try:
                        fixture = jwt.decode(token, options={"verify_signature": False}).get("iss") == "314159"
                    except jwt.InvalidTokenError:
                        # Opaque or malformed credentials do not identify the fixture.
                        fixture = False
                if not fixture:
                    return await self.external.handle_async_request(request)
                async with httpx.AsyncClient(timeout=20) as client:
                    response = await client.post("http://api:8000/__fixture/github", json={
                        "method": request.method, "url": str(request.url),
                        "headers": dict(request.headers), "body": base64.b64encode(request.content).decode(),
                    })
                response.raise_for_status()
                result = response.json()
                return httpx.Response(result["status"], json=result["body"], request=request)

            async def aclose(self):
                await self.external.aclose()

        def fixture_client():
            return httpx.AsyncClient(transport=FixtureTransport(), timeout=20, follow_redirects=False)

        module.http_client = fixture_client

    class FixtureLoader(importlib.abc.Loader):
        def __init__(self, original):
            self.original = original

        def create_module(self, spec):
            return self.original.create_module(spec)

        def exec_module(self, module):
            self.original.exec_module(module)
            install_transport(module)

    class FixtureFinder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if fullname != "shared.github_git_app":
                return None
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec is not None and spec.loader is not None:
                spec.loader = FixtureLoader(spec.loader)
            return spec

    sys.meta_path.insert(0, FixtureFinder())
