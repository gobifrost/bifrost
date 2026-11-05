"""Diagnose ambient Azure identity availability without printing any token.

Explicit Azure-hosted diagnostic only. Run in the intended child/container
network context with Blob environment variables removed. A positive result means
configuration scrubbing is not a metadata-service access boundary; it is not an
assertion that the context is isolated or safe for untrusted workflow code.
"""

import json
import os
import urllib.request


def main():
    request = urllib.request.Request(
        "http://169.254.169.254/metadata/identity/oauth2/token"
        "?api-version=2018-02-01&resource=https%3A%2F%2Fstorage.azure.com%2F",
        headers={"Metadata": "true"},
    )
    result = {
        "uid": os.getuid(),
        "blob_config_present": any(
            name.startswith("BIFROST_AZURE_BLOB_") for name in os.environ
        ),
    }
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result.update(
                status=response.status,
                credential_obtainable=bool(json.load(response).get("access_token")),
            )
    except Exception as error:
        result.update(
            status=getattr(error, "code", None), error_type=type(error).__name__
        )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
