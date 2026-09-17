from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import httpx
import pytest

from src.jobs.platform.kubernetes_client import (
    KubernetesApiError,
    KubernetesConfigurationError,
    KubernetesJobClient,
    _lease_token_fingerprint,
    build_job_manifest,
)
from src.jobs.schedulers.platform_jobs import ClaimedPlatformJob


@dataclass
class KubernetesSettings:
    kubernetes_build_namespace: str = "builds"
    kubernetes_build_image: str = "registry.example.com/bifrost/api:sha-abc123"
    kubernetes_build_configmap: str = "bifrost-build-config"
    kubernetes_build_secret: str = "bifrost-build-secret"
    kubernetes_build_service_account: str = "bifrost-build-runner"
    kubernetes_build_memory_request_mib: int = 512
    kubernetes_build_memory_limit_mib: int = 2048
    kubernetes_build_cpu_request: str = "250m"
    kubernetes_build_cpu_limit: str = "1"
    kubernetes_build_max_jobs: int = 2
    kubernetes_build_pending_timeout_seconds: int = 300


def _claim() -> ClaimedPlatformJob:
    return ClaimedPlatformJob(
        id=UUID("11111111-2222-3333-4444-555555555555"),
        lease_token=UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        timeout_seconds=900,
        hard_memory_ratio=1.0,
    )


def test_build_job_manifest_renders_suspended_locked_down_job() -> None:
    claim = _claim()
    manifest = build_job_manifest(claim, KubernetesSettings())

    assert manifest["metadata"]["name"] == (
        f"bifrost-job-11111111222233334444555555555555-"
        f"{_lease_token_fingerprint(claim.lease_token)[:12]}"
    )
    assert manifest["metadata"]["namespace"] == "builds"
    labels = manifest["metadata"]["labels"]
    assert labels["bifrost.gobifrost.com/platform-job-id"] == str(claim.id)
    assert labels["bifrost.gobifrost.com/lease-token-sha256"] == (
        _lease_token_fingerprint(claim.lease_token)
    )
    assert str(claim.lease_token) not in repr(manifest["metadata"])

    spec = manifest["spec"]
    assert spec["suspend"] is True
    assert spec["backoffLimit"] == 0
    assert spec["parallelism"] == 1
    assert spec["completions"] == 1
    assert spec["ttlSecondsAfterFinished"] == 3600
    assert spec["activeDeadlineSeconds"] == 1260

    pod_spec = spec["template"]["spec"]
    assert pod_spec["restartPolicy"] == "Never"
    assert pod_spec["serviceAccountName"] == "bifrost-build-runner"
    assert pod_spec["automountServiceAccountToken"] is False
    assert pod_spec["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 1000,
        "runAsGroup": 1000,
        "fsGroup": 1000,
    }

    container = pod_spec["containers"][0]
    assert container["image"] == "registry.example.com/bifrost/api:sha-abc123"
    assert container["command"] == [
        "python",
        "-m",
        "src.jobs.platform.kubernetes_runner",
        str(claim.id),
        str(claim.lease_token),
    ]
    assert container["envFrom"] == [
        {"configMapRef": {"name": "bifrost-build-config"}},
        {"secretRef": {"name": "bifrost-build-secret"}},
    ]
    assert container["env"] == [
        {
            "name": "BIFROST_KUBERNETES_JOB_UID",
            "valueFrom": {
                "fieldRef": {
                    "fieldPath": (
                        "metadata.labels['batch.kubernetes.io/controller-uid']"
                    )
                }
            },
        },
        {
            "name": "BIFROST_KUBERNETES_POD_UID",
            "valueFrom": {"fieldRef": {"fieldPath": "metadata.uid"}},
        },
    ]
    assert container["resources"] == {
        "requests": {"memory": "512Mi", "cpu": "250m"},
        "limits": {"memory": "2048Mi", "cpu": "1"},
    }
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "capabilities": {"drop": ["ALL"]},
    }


def test_build_job_manifest_requires_explicit_image() -> None:
    settings = KubernetesSettings(kubernetes_build_image="")

    with pytest.raises(KubernetesConfigurationError, match="kubernetes_build_image"):
        build_job_manifest(_claim(), settings)


def test_build_job_manifest_rejects_request_above_limit() -> None:
    settings = KubernetesSettings(
        kubernetes_build_memory_request_mib=4096,
        kubernetes_build_memory_limit_mib=2048,
    )

    with pytest.raises(KubernetesConfigurationError, match="request_mib"):
        build_job_manifest(_claim(), settings)


@pytest.mark.asyncio
async def test_client_reads_fresh_token_per_request_and_gets_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("token-one")
    seen_authorization: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_authorization.append(request.headers["authorization"])
        token_path.write_text("token-two")
        return httpx.Response(
            200,
            json={"metadata": {"name": "job-a", "uid": "uid-a"}},
        )

    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.SERVICE_ACCOUNT_TOKEN_PATH",
        token_path,
    )
    client = KubernetesJobClient(
        settings=KubernetesSettings(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://kubernetes.default.svc",
        ),
    )

    first = await client.get_job("job-a")
    second = await client.get_job("job-a")

    assert first == {"metadata": {"name": "job-a", "uid": "uid-a"}}
    assert second == first
    assert seen_authorization == ["Bearer token-one", "Bearer token-two"]


@pytest.mark.asyncio
async def test_create_job_adopts_existing_job_on_conflict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("token")
    manifest = build_job_manifest(_claim(), KubernetesSettings())
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(409, json={"ignored": "body"})
        return httpx.Response(
            200,
            json={
                "metadata": {
                    "name": manifest["metadata"]["name"],
                    "uid": "uid-existing",
                    "labels": manifest["metadata"]["labels"],
                }
            },
        )

    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.SERVICE_ACCOUNT_TOKEN_PATH",
        token_path,
    )
    client = KubernetesJobClient(
        settings=KubernetesSettings(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://kubernetes.default.svc",
        ),
    )

    adopted = await client.create_job(manifest)

    assert adopted["metadata"]["uid"] == "uid-existing"
    assert calls == [
        ("POST", "/apis/batch/v1/namespaces/builds/jobs"),
        (
            "GET",
            "/apis/batch/v1/namespaces/builds/jobs/"
            f"{manifest['metadata']['name']}",
        ),
    ]


@pytest.mark.asyncio
async def test_create_job_rejects_conflicting_existing_job_labels(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("token")
    manifest = build_job_manifest(_claim(), KubernetesSettings())

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(409, json={})
        return httpx.Response(
            200,
            json={
                "metadata": {
                    "name": manifest["metadata"]["name"],
                    "uid": "uid-existing",
                    "labels": {
                        **manifest["metadata"]["labels"],
                        "bifrost.gobifrost.com/lease-token-sha256": "wrong-token",
                    },
                }
            },
        )

    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.SERVICE_ACCOUNT_TOKEN_PATH",
        token_path,
    )
    client = KubernetesJobClient(
        settings=KubernetesSettings(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://kubernetes.default.svc",
        ),
    )

    with pytest.raises(KubernetesApiError) as exc:
        await client.create_job(manifest)

    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_start_job_uses_uid_json_patch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("token")
    captured: dict[str, object] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["content_type"] = request.headers["content-type"]
        captured["body"] = request.read()
        return httpx.Response(200, json={"spec": {"suspend": False}})

    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.SERVICE_ACCOUNT_TOKEN_PATH",
        token_path,
    )
    client = KubernetesJobClient(
        settings=KubernetesSettings(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://kubernetes.default.svc",
        ),
    )

    started = await client.start_job("job-a", "uid-a")

    assert started == {"spec": {"suspend": False}}
    assert captured["content_type"] == "application/json-patch+json"
    assert captured["body"] == (
        b'[{"op":"test","path":"/metadata/uid","value":"uid-a"},'
        b'{"op":"replace","path":"/spec/suspend","value":false}]'
    )


@pytest.mark.asyncio
async def test_delete_job_uses_foreground_uid_precondition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("token")
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = request.read()
        return httpx.Response(202, json={})

    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.SERVICE_ACCOUNT_TOKEN_PATH",
        token_path,
    )
    client = KubernetesJobClient(
        settings=KubernetesSettings(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://kubernetes.default.svc",
        ),
    )

    await client.delete_job("job-a", "uid-a")

    assert captured["body"] == (
        b'{"apiVersion":"v1","kind":"DeleteOptions",'
        b'"propagationPolicy":"Foreground","preconditions":{"uid":"uid-a"}}'
    )


@pytest.mark.asyncio
async def test_list_pods_uses_controller_uid_label_selector(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("token")
    seen_query = ""

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_query
        seen_query = request.url.query.decode()
        return httpx.Response(200, json={"items": [{"metadata": {"name": "pod-a"}}]})

    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.SERVICE_ACCOUNT_TOKEN_PATH",
        token_path,
    )
    client = KubernetesJobClient(
        settings=KubernetesSettings(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://kubernetes.default.svc",
        ),
    )

    pods = await client.list_pods("controller-uid")

    assert pods == [{"metadata": {"name": "pod-a"}}]
    assert seen_query == (
        "labelSelector=batch.kubernetes.io%2Fcontroller-uid%3Dcontroller-uid"
    )


@pytest.mark.asyncio
async def test_http_error_message_omits_response_body_and_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token"
    token_path.write_text("secret-token")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="secret-token and response details")

    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.SERVICE_ACCOUNT_TOKEN_PATH",
        token_path,
    )
    client = KubernetesJobClient(
        settings=KubernetesSettings(),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://kubernetes.default.svc",
        ),
    )

    with pytest.raises(KubernetesApiError) as exc:
        await client.get_job("job-a")

    message = str(exc.value)
    assert "status 500" in message
    assert "secret-token" not in message
    assert "response details" not in message


def test_client_creation_requires_in_cluster_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KUBERNETES_SERVICE_HOST", raising=False)
    monkeypatch.delenv("KUBERNETES_SERVICE_PORT_HTTPS", raising=False)

    with pytest.raises(KubernetesConfigurationError, match="KUBERNETES_SERVICE_HOST"):
        KubernetesJobClient(settings=KubernetesSettings())


@pytest.mark.parametrize(
    "field_name",
    [
        "kubernetes_build_image",
        "kubernetes_build_configmap",
        "kubernetes_build_secret",
        "kubernetes_build_service_account",
    ],
)
def test_client_creation_requires_build_settings_before_http_client(
    monkeypatch: pytest.MonkeyPatch,
    field_name: str,
) -> None:
    def fail_http_client(**_kwargs: object) -> object:
        raise AssertionError("HTTP client must not be created")

    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.httpx.AsyncClient",
        fail_http_client,
    )
    settings = KubernetesSettings()
    setattr(settings, field_name, "")

    with pytest.raises(KubernetesConfigurationError, match=field_name):
        KubernetesJobClient(settings=settings)


def test_client_creation_brackets_ipv6_service_host_and_uses_ca_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ca_path = tmp_path / "ca.crt"
    token_path = tmp_path / "token"
    ca_path.write_text("ca")
    token_path.write_text("token")
    captured: dict[str, object] = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs: object) -> None:
            captured.update(kwargs)

        async def aclose(self) -> None:
            pass

    def fake_context(*, cafile: str) -> object:
        captured["cafile"] = cafile
        return "ssl-context"

    monkeypatch.setenv("KUBERNETES_SERVICE_HOST", "fd00::1")
    monkeypatch.setenv("KUBERNETES_SERVICE_PORT_HTTPS", "6443")
    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.SERVICE_ACCOUNT_CA_PATH",
        ca_path,
    )
    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.SERVICE_ACCOUNT_TOKEN_PATH",
        token_path,
    )
    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.ssl.create_default_context",
        fake_context,
    )
    monkeypatch.setattr(
        "src.jobs.platform.kubernetes_client.httpx.AsyncClient",
        FakeAsyncClient,
    )

    client = KubernetesJobClient(settings=KubernetesSettings())

    assert client._owned_client is True
    assert captured["base_url"] == "https://[fd00::1]:6443"
    assert captured["verify"] == "ssl-context"
    assert captured["timeout"] == 5.0
    assert captured["cafile"] == str(ca_path)
