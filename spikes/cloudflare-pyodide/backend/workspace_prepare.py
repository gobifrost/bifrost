"""Prepare declared workspace pins through upstream's authenticated SDK API."""
import asyncio
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import quote
from uuid import UUID

import httpx

from shared.sdk_modules import logical_name_to_base_path, validate_module_path, ModuleSourceError


class PreparedWorkspace(SimpleNamespace):
    def __repr__(self):
        return 'PreparedWorkspace()'


class WorkspacePreparationError(RuntimeError):
    def __init__(self, message, *, status_code=None):
        super().__init__(message)
        self.status_code = status_code


async def prepare_workspace(api_url, context, workspace_imports):
    """Snapshot one run's declared loose-workspace sources without executing code.

    The API owns authorization, lookup and import-name resolution. This function
    verifies returned identities/content hashes and constructs a bounded manifest.
    It does not discover arbitrary dynamic imports or mint/refresh credentials.
    """
    url = httpx.URL(api_url)
    local = url.scheme == 'http' and url.host == 'api' and url.port == 8000
    if ((url.scheme != 'https' and not local) or not url.host or url.username or url.password
        or url.query or url.fragment or url.path not in ('', '/')):
        raise WorkspacePreparationError('Trusted API root required')
    frozen = json.loads(json.dumps(context, default=str, allow_nan=False))
    execution_id, path, token = frozen.get('execution_id'), frozen.get('file_path'), frozen.get('engine_token')
    try:
        valid_id = isinstance(execution_id, str) and str(UUID(execution_id)) == execution_id
        if not isinstance(path, str) or not path.endswith('.py') or len(path) > 512:
            raise ValueError('path')
        validate_module_path(path)
        if not valid_id or not isinstance(token, str) or not 1 <= len(token) <= 16384:
            raise ValueError('identity')
        if any(frozen.get(key) is not None for key in ('solution_id', 'artifact_workspace_id', 'code', 'service')):
            raise ValueError('unsupported scope')
        if not isinstance(workspace_imports, (list, tuple)) or len(workspace_imports) > 31:
            raise ValueError('import bound')
        names = tuple(workspace_imports)
        if len(set(names)) != len(names):
            raise ValueError('duplicate import')
        bases = {name: logical_name_to_base_path(name) for name in names if isinstance(name, str)}
        if len(bases) != len(names):
            raise ValueError('import name')
        if any(name not in (base, base.replace('/', '.')) for name, base in bases.items()):
            raise ValueError('noncanonical import declaration')
    except (ValueError, TypeError, ModuleSourceError):
        raise WorkspacePreparationError('Canonical loose-workspace plan required') from None
    manifest, observations, namespaces = {}, [], []
    total_bytes = 0

    async with asyncio.timeout(20):
        async with httpx.AsyncClient(base_url=str(url), timeout=10, follow_redirects=False, trust_env=False) as api:
            async def get(endpoint, *, params=None):
                nonlocal total_bytes
                async with api.stream('GET', endpoint, params=params,
                    headers={'Authorization': 'Bearer ' + token}) as response:
                    observations.append({'method': 'GET', 'path': endpoint, 'status': response.status_code})
                    if response.status_code != 200:
                        raise WorkspacePreparationError('Upstream workspace request returned status ' + str(response.status_code),
                                                        status_code=response.status_code)
                    chunks, size = [], 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        total_bytes += len(chunk)
                        if size > 2 * 1024**2 or total_bytes > 4 * 1024**2:
                            raise WorkspacePreparationError('Workspace preparation response bound exceeded')
                        chunks.append(chunk)
                    try:
                        result = json.loads(b''.join(chunks))
                    except (ValueError, UnicodeError):
                        raise WorkspacePreparationError('Invalid upstream workspace response') from None
                    if not isinstance(result, dict):
                        raise WorkspacePreparationError('Invalid upstream workspace response')
                    return result

            def pin(value, expected):
                if (value.get('path') != expected or not isinstance(value.get('content'), str)
                    or value.get('storage_path', expected) != expected):
                    raise WorkspacePreparationError('Scoped or mismatched workspace source refused')
                digest = hashlib.sha256(value['content'].encode('utf-8')).hexdigest()
                if value.get('hash') != digest or manifest.get(expected, digest) != digest:
                    raise WorkspacePreparationError('Workspace content hash mismatch')
                manifest[expected] = digest

            entry = await get('/api/sdk/modules/' + quote(path, safe='/'))
            pin(entry, path)
            existing = frozen.get('content_hash')
            if existing is not None and existing != manifest[path]:
                raise WorkspacePreparationError('Original workflow pin differs from authoritative API source')
            for name, base in bases.items():
                value = await get('/api/sdk/modules-resolve', params={'name': name})
                kind = value.get('kind')
                if kind in ('module', 'package'):
                    pin(value, base + ('.py' if kind == 'module' else '/__init__.py'))
                elif kind == 'namespace' and value.get('path') == base:
                    namespaces.append(base)
                else:
                    raise WorkspacePreparationError('Declared workspace import unavailable or unsupported')
            if any(not any(module.startswith(namespace + '/') for module in manifest) for namespace in namespaces):
                raise WorkspacePreparationError('Namespace requires declared pinned descendants')
    frozen['content_hash'] = manifest[path]
    return PreparedWorkspace(context=frozen, manifest=manifest, observations=observations,
                             response_bytes=total_bytes)
