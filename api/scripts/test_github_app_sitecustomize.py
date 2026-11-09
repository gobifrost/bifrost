"""Test-compose-only synthetic GitHub transport for subprocess job runners.

Mounted as /app/sitecustomize.py only in the test executors and activated only
for the isolated PlatformJob runner, never health probes or execution templates. Real credentials
retain the normal transport. Synthetic App JWTs are verified by the API fixture,
which owns the ephemeral signing key; no production authentication is changed.
"""
import sys

if "src.jobs.platform.runner" in sys.orig_argv:
    import base64

    import httpx
    import jwt

    from shared import github_git_app


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


    github_git_app.http_client = fixture_client
