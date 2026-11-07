"""Preparation HTTP contract tests, separate from real API/engine qualification."""
import hashlib
from unittest import IsolatedAsyncioTestCase, main
from unittest.mock import patch
from uuid import uuid4

import httpx

from workspace_prepare import prepare_workspace, WorkspacePreparationError

CLIENT = httpx.AsyncClient
ENTRY = 'raise RuntimeError("source must not execute during preparation")\n'
HELPER = 'def fold(n):\n    return n\n'


def module(path, content):
    return {'path': path, 'content': content, 'hash': hashlib.sha256(content.encode()).hexdigest()}


class PrepareTests(IsolatedAsyncioTestCase):
    def context(self):
        return {'execution_id': str(uuid4()), 'file_path': 'workflows/test.py', 'content_hash': None,
                'engine_token': 'synthetic-preparation-token', 'timeout_seconds': 30}

    def provider(self, handler):
        return patch('workspace_prepare.httpx.AsyncClient',
            side_effect=lambda **kw: CLIENT(transport=httpx.MockTransport(handler), **kw))

    async def test_upstream_excluded_import_roots_refused_before_api_lookup(self):
        for name in ('json', 'json.decoder', 'bifrost', 'bifrost.tables', 'src', 'os', 'httpx'):
            requests = []

            def handler(request):
                requests.append(request)
                path = 'workflows/test.py' if request.url.path.startswith('/api/sdk/modules/') else name.replace('.', '/') + '.py'
                return httpx.Response(200, json={
                    'kind': 'module', **module(path, ENTRY)})

            with self.subTest(name=name), self.provider(handler):
                with self.assertRaises(WorkspacePreparationError):
                    await prepare_workspace('https://api.invalid/', self.context(), [name])
                self.assertEqual(requests, [])

    async def test_provider_bootstrap_roots_refused_before_api_lookup(self):
        for root in ('main', 'runtime_runner', 'runtime_guard', 'pyodide_refresh', 'sdk_cleanup'):
            for name in (root, root + '.nested'):
                requests = []

                def handler(request):
                    requests.append(request)
                    path = 'workflows/test.py' if request.url.path.startswith('/api/sdk/modules/') else name.replace('.', '/') + '.py'
                    return httpx.Response(200, json={'kind': 'module', **module(path, ENTRY)})

                with self.subTest(name=name), self.provider(handler):
                    with self.assertRaises(WorkspacePreparationError):
                        await prepare_workspace('https://api.invalid/', self.context(), [name])
                    self.assertEqual(requests, [])

    async def test_bootstrap_or_upstream_root_cannot_be_an_entry_module(self):
        for root in ('main', 'runtime_runner', 'runtime_guard', 'pyodide_refresh', 'sdk_cleanup', 'json', 'bifrost'):
            requests = []

            def handler(request):
                requests.append(request)
                return httpx.Response(200, json=module(root + '.py', ENTRY))

            with self.subTest(root=root), self.provider(handler):
                with self.assertRaises(WorkspacePreparationError):
                    await prepare_workspace('https://api.invalid/', {**self.context(), 'file_path': root + '.py'}, [])
                self.assertEqual(requests, [])

    async def test_nested_workflow_leaf_can_share_a_bootstrap_basename(self):
        path = 'workflows/sdk_cleanup.py'
        with self.provider(lambda request: httpx.Response(200, json=module(path, ENTRY))):
            prepared = await prepare_workspace('https://api.invalid/', {**self.context(), 'file_path': path}, [])
        self.assertEqual(prepared.manifest, {path: module(path, ENTRY)['hash']})

    async def test_api_hashes_and_upstream_resolution_prepare_snapshot_without_executing_source(self):
        context, names, sent = self.context(), ['helper'], []

        def handler(request):
            sent.append(request)
            context['engine_token'] = 'replacement'
            names.append('late-mutation')
            if request.url.path == '/api/sdk/modules/workflows/test.py':
                return httpx.Response(200, json=module('workflows/test.py', ENTRY))
            self.assertEqual(request.url.params['name'], 'helper')
            return httpx.Response(200, json={'kind': 'module', 'storage_path': 'helper.py', **module('helper.py', HELPER)})

        with self.provider(handler):
            prepared = await prepare_workspace('https://api.invalid/', context, names)
        self.assertEqual(len(sent), 2)
        self.assertTrue(all(request.headers['authorization'] == 'Bearer synthetic-preparation-token' for request in sent))
        self.assertIsNone(context['content_hash'])
        self.assertEqual(prepared.context['engine_token'], 'synthetic-preparation-token')
        self.assertEqual(prepared.context['content_hash'], module('workflows/test.py', ENTRY)['hash'])
        self.assertEqual(prepared.manifest['helper.py'], module('helper.py', HELPER)['hash'])
        self.assertNotIn('synthetic-preparation-token', repr(prepared))

    async def test_existing_pin_and_corrupt_response_rejected(self):
        for context, response in [
            ({**self.context(), 'content_hash': 'b' * 64}, module('workflows/test.py', ENTRY)),
            (self.context(), {**module('workflows/test.py', ENTRY), 'hash': 'a' * 64}),
            (self.context(), module('workflows/other.py', ENTRY)),
        ]:
            with self.subTest(context=context['execution_id']), self.provider(lambda request: httpx.Response(200, json=response)):
                with self.assertRaises(WorkspacePreparationError):
                    await prepare_workspace('https://api.invalid/', context, [])

    async def test_auth_denial_and_redirect_never_retry_or_follow(self):
        for status in (401, 403, 307):
            requests = []

            def handler(request):
                requests.append(request)
                return httpx.Response(status, headers={'Location': 'https://other.invalid/'},
                                      json={'detail': 'synthetic-preparation-token'})

            with self.provider(handler), self.assertRaises(WorkspacePreparationError) as error:
                await prepare_workspace('https://api.invalid/', self.context(), [])
            self.assertEqual(error.exception.status_code, status)
            self.assertEqual(len(requests), 1)
            self.assertNotIn('synthetic-preparation-token', str(error.exception))

    async def test_missing_scoped_or_unpinned_namespace_dependency_refused(self):
        for resolution in [{'kind': 'not_found', 'path': 'helper'}, {'kind': 'namespace', 'path': 'helper'},
            {'kind': 'module', 'storage_path': '__solutions/foreign/helper.py', **module('helper.py', HELPER)}]:
            def handler(request):
                return httpx.Response(200, json=module('workflows/test.py', ENTRY)
                    if request.url.path.startswith('/api/sdk/modules/') else resolution)

            with self.provider(handler), self.assertRaises(WorkspacePreparationError):
                await prepare_workspace('https://api.invalid/', self.context(), ['helper'])

    async def test_package_and_declared_namespace_descendant_keep_api_paths(self):
        def handler(request):
            if request.url.path.startswith('/api/sdk/modules/'):
                return httpx.Response(200, json=module('workflows/test.py', ENTRY))
            name = request.url.params['name']
            if name == 'tools':
                return httpx.Response(200, json={'kind': 'namespace', 'path': 'tools'})
            path = 'tools/nested/__init__.py'
            return httpx.Response(200, json={'kind': 'package', 'storage_path': path, **module(path, HELPER)})

        with self.provider(handler):
            prepared = await prepare_workspace('https://api.invalid/', self.context(), ['tools', 'tools.nested'])
        self.assertIn('tools/nested/__init__.py', prepared.manifest)
        self.assertNotIn('tools', prepared.manifest)

    async def test_input_and_response_bounds_refused(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, content=b'x' * (2 * 1024**2 + 1))

        with self.provider(handler):
            for url, context, names in [
                ('http://other.invalid/', self.context(), []),
                ('https://api.invalid/', {**self.context(), 'solution_id': str(uuid4())}, []),
                ('https://api.invalid/', self.context(), ['helper'] * 32),
                ('https://api.invalid/', self.context(), ['../escape']),
            ]:
                with self.assertRaises(WorkspacePreparationError):
                    await prepare_workspace(url, context, names)
            self.assertEqual(requests, [])
            with self.assertRaisesRegex(WorkspacePreparationError, 'response bound'):
                await prepare_workspace('https://api.invalid/', self.context(), [])
        self.assertEqual(len(requests), 1)


if __name__ == '__main__':
    main()
