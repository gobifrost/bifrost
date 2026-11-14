"""Request target regressions; no network, credentials or cloud invocation."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('cloud_relay', Path(__file__).with_name('cloud-canonical-relay.py'))
relay = importlib.util.module_from_spec(spec)
spec.loader.exec_module(relay)


@pytest.mark.parametrize('target', [
    'https://evil.invalid/api/sdk/config/get', '//evil.invalid/api/sdk/config/get',
    '/api/sdk/../../api/workflows', '/api/sdk/%2e%2e/%2e%2e/api/workflows',
    '/api/sdk/%252e%252e/workflows', '/api/sdk/%5c..%5cworkflows',
    '/api/sdk/config/get#fragment', '/api/sdk/config/get\r\nHost:evil.invalid',
    '/api/workflows', '/api/sdk/%00x',
])
def test_target_refused_before_transport(target):
    with pytest.raises(ValueError):
        relay.sdk_target(target)


@pytest.mark.parametrize('target', ['/api/sdk/modules/workflows/test.py',
    '/api/tables/proof/documents?scope=tenant', '/auth/refresh'])
def test_authority_is_fixed_and_sdk_target_survives(target):
    url = relay.sdk_target(target)
    assert (url.scheme, url.host, url.port) == ('http', 'api', 8000)
    assert url.raw_path.decode() == target
