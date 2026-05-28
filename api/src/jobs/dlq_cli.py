"""Operational CLI for RabbitMQ poison queues.

Run inside the API/worker environment:

    python -m src.jobs.dlq_cli inspect workflow-executions --limit 10
    python -m src.jobs.dlq_cli replay workflow-executions --limit 5 --dry-run
    python -m src.jobs.dlq_cli discard workflow-executions --limit 5 --reason "bad payload"
"""

import argparse
import asyncio
import json
import logging
from typing import Any

import aio_pika

from src.config import get_settings
from src.jobs.rabbitmq import _message_headers

logger = logging.getLogger(__name__)


def decode_message(body: bytes) -> dict[str, Any] | str:
    try:
        parsed = json.loads(body.decode())
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except Exception:
        return body.decode(errors="replace")


async def _connect():
    return await aio_pika.connect_robust(get_settings().rabbitmq_url)


async def _fetch_poison_messages(poison, limit: int) -> list[Any]:
    messages: list[Any] = []
    for _ in range(limit):
        message = await poison.get(fail=False, no_ack=False)
        if message is None:
            break
        messages.append(message)
    return messages


async def _requeue_messages(messages: list[Any]) -> None:
    for message in messages:
        await message.nack(requeue=True)


async def _ensure_main_queue(channel, queue_name: str) -> None:
    await channel.declare_queue(queue_name, passive=True)


async def inspect(queue: str, limit: int) -> list[dict[str, Any]]:
    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        messages = await _fetch_poison_messages(poison, limit)
        rows = [_describe(queue, message) for message in messages]
        await _requeue_messages(messages)
        await channel.close()
    return rows


async def replay(queue: str, limit: int, dry_run: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        await _ensure_main_queue(channel, queue)
        messages = await _fetch_poison_messages(poison, limit)
        for message in messages:
            row = _describe(queue, message)
            rows.append(row)
            if dry_run:
                continue
            body = decode_message(message.body)
            publish_body = body if isinstance(body, dict) else {"_malformed_body": body}
            replay_count = int((message.headers or {}).get("x-replayed-count") or 0) + 1
            headers = _message_headers(
                publish_body,
                queue,
                message_id=message.message_id,
                headers=dict(message.headers or {}),
                retry_count=0,
                replay_count=replay_count,
            )
            await channel.default_exchange.publish(
                aio_pika.Message(
                    body=json.dumps(publish_body).encode(),
                    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                    message_id=message.message_id,
                    correlation_id=message.correlation_id,
                    headers=headers,
                ),
                routing_key=queue,
            )
            await message.ack()
            logger.info("Replayed poison message", extra={"queue": queue, **row})
        if dry_run:
            await _requeue_messages(messages)
        await channel.close()
    return rows


async def discard(queue: str, limit: int, reason: str, dry_run: bool) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    connection = await _connect()
    async with connection:
        channel = await connection.channel()
        poison = await channel.declare_queue(f"{queue}-poison", durable=True)
        messages = await _fetch_poison_messages(poison, limit)
        for message in messages:
            row = _describe(queue, message)
            rows.append(row)
            if dry_run:
                continue
            await message.ack()
            logger.warning(
                "Discarded poison message",
                extra={"queue": queue, "discard_reason": reason, **row},
            )
        if dry_run:
            await _requeue_messages(messages)
        await channel.close()
    return rows


def _describe(queue: str, message) -> dict[str, Any]:
    headers = dict(message.headers or {})
    return {
        "queue": queue,
        "poison_queue": f"{queue}-poison",
        "message_id": message.message_id,
        "correlation_id": message.correlation_id,
        "idempotency_key": headers.get("x-idempotency-key"),
        "retry_count": headers.get("x-retry-count", 0),
        "replay_count": headers.get("x-replayed-count", 0),
        "origin_queue": headers.get("x-origin-queue"),
        "dead_letter": headers.get("x-death"),
        "headers": headers,
        "body": decode_message(message.body),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect/replay/discard Bifrost RabbitMQ poison queues")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "replay", "discard"):
        cmd = sub.add_parser(name)
        cmd.add_argument("queue")
        cmd.add_argument("--limit", type=int, default=10)
        cmd.add_argument("--dry-run", action="store_true")
    sub.choices["discard"].add_argument("--reason", required=True)
    args = parser.parse_args(argv)

    if args.command == "inspect":
        rows = asyncio.run(inspect(args.queue, args.limit))
    elif args.command == "replay":
        rows = asyncio.run(replay(args.queue, args.limit, args.dry_run))
    else:
        rows = asyncio.run(discard(args.queue, args.limit, args.reason, args.dry_run))
    print(json.dumps(rows, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
