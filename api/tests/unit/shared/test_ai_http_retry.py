"""Raw provider reads share the model transport's finite recovery policy."""
import asyncio
import logging

import httpx
import pytest

from shared.ai_http_retry import MAX_ATTEMPTS, get_ai_provider_read


@pytest.mark.asyncio
@pytest.mark.parametrize('guidance', [None, 'invalid', '0', '-1', '5'])
async def test_safe_read_recovers_with_bounded_delay(guidance):
    requests = []
    delays = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429 if len(requests) == 1 else 200,
                              headers={} if guidance is None else {'Retry-After': guidance})

    async def sleep(delay):
        delays.append(delay)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        response = await get_ai_provider_read(client, 'https://provider.test/models',
                                             provider='test', surface='catalog', sleep=sleep)
    assert response.status_code == 200
    assert len(requests) == 2
    assert len(delays) == 1
    if guidance == '5':
        assert delays == [5]
    else:
        assert 1 <= delays[0] <= 2


@pytest.mark.asyncio
async def test_exhausted_read_preserves_status_and_private_identity(caplog):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, json={'error': 'private provider body'})

    async def sleep(_):
        pass

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with caplog.at_level(logging.WARNING):
            response = await get_ai_provider_read(
                client, 'https://provider.test/status?token=secret', provider='test', model='model',
                surface='video_status', operation_ref='operation-hash', sleep=sleep)
    with pytest.raises(httpx.HTTPStatusError) as error:
        response.raise_for_status()
    assert error.value.response.status_code == 429
    assert len(requests) == MAX_ATTEMPTS
    records = [r for r in caplog.records if r.message == 'ai_provider_request_terminal']
    assert len(records) == 1
    assert records[0].operation_ref == 'operation-hash'
    assert records[0].provider == 'test'
    assert records[0].attempt == MAX_ATTEMPTS
    assert 'secret' not in caplog.text
    assert 'private provider body' not in caplog.text


@pytest.mark.asyncio
async def test_concurrent_reads_use_independent_jitter(monkeypatch):
    waits = iter([1.1, 1.5, 1.9])
    monkeypatch.setattr('shared.ai_http_retry.random.uniform', lambda *_: next(waits))
    delays = []

    async def run_one():
        count = 0

        def handler(request):
            nonlocal count
            count += 1
            return httpx.Response(429 if count == 1 else 200)

        async def sleep(delay):
            delays.append(delay)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await get_ai_provider_read(client, 'https://provider.test/models',
                                              provider='test', surface='catalog', sleep=sleep)

    results = await asyncio.gather(*(run_one() for _ in range(3)))
    assert all(r.status_code == 200 for r in results)
    assert sorted(delays) == [1.1, 1.5, 1.9]


@pytest.mark.asyncio
async def test_cancellation_during_backoff_stops_read():
    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(429)

    async def sleep(_):
        raise asyncio.CancelledError

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(asyncio.CancelledError):
            await get_ai_provider_read(client, 'https://provider.test/models',
                                       provider='test', surface='catalog', sleep=sleep)
    assert attempts == 1
