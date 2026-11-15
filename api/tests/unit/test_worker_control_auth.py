"""A physical external worker cannot reuse its key for another resource."""

from dataclasses import replace
from uuid import UUID

import pytest

from shared.worker_control_auth import external_worker_control_key
from src.worker.remote.protocol import AuthenticationError, Hello, sign_hello, verify_hello


WORKER = UUID("00000000-0000-4000-8000-000000001400")
PROVIDER_KEY = "synthetic-provider-credential-for-unit-test"


def test_control_key_is_stable_and_bound_to_provider_worker_and_resource():
    key = external_worker_control_key(PROVIDER_KEY, WORKER, "resource-1")
    assert key == external_worker_control_key(PROVIDER_KEY, WORKER, "resource-1")
    assert key != PROVIDER_KEY
    assert key != external_worker_control_key("another-provider", WORKER, "resource-1")
    assert key != external_worker_control_key(PROVIDER_KEY, UUID(int=2), "resource-1")
    assert key != external_worker_control_key(PROVIDER_KEY, WORKER, "resource-2")


def test_control_key_matches_the_cloudflare_provider_vector():
    assert external_worker_control_key(
        PROVIDER_KEY, UUID("11111111-2222-4333-8444-555555555555"), "resource-1",
    ) == "740ab8ee7b20fcc7e0d19de991e5d6d11ab564677b9703b68cab58ac20ba9771"


def test_scoped_key_signs_hello_but_cannot_authenticate_another_resource():
    hello = Hello(
        launcher_id=UUID(int=1), incarnation_id=WORKER, form="external",
        job_uid=None, pod_uid=None, hostname="worker-test", resource_uid="resource-1",
        issued_at=1000, nonce="n" * 32, hello_sequence=1,
    )
    key = external_worker_control_key(PROVIDER_KEY, WORKER, "resource-1")
    signed = sign_hello(key, hello)
    assert verify_hello(key, signed, now=1000) == hello
    replacement_key = external_worker_control_key(PROVIDER_KEY, WORKER, "resource-2")
    with pytest.raises(AuthenticationError):
        verify_hello(replacement_key, replace(signed, resource_uid="resource-2"), now=1000)
    with pytest.raises(AuthenticationError):
        verify_hello("installation-signing-key", signed, now=1000)


@pytest.mark.parametrize("uid", ["", "resource/1", "resource\n1", "x" * 256])
def test_invalid_resource_identity_is_refused(uid):
    with pytest.raises(ValueError, match="identity"):
        external_worker_control_key(PROVIDER_KEY, WORKER, uid)
