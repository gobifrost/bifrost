"""Bound RPC wire bytes; this is not an isolate peak-memory limit."""
import json

MAX_RPC_BYTES = 4 * 1024 * 1024


class RpcResultTooLargeError(ValueError):
    pass


def encode_rpc(value, *, max_bytes=MAX_RPC_BYTES):
    if type(max_bytes) is not int or max_bytes < 1:
        raise ValueError('Positive RPC byte budget required')
    chunks = []
    size = 0
    # ASCII encoding makes character count equal wire bytes, without allocating
    # a second UTF-8 copy of a possibly oversized chunk. JSON string chunks can
    # still allocate in the encoder; the user's object already exists in memory.
    encoder = json.JSONEncoder(ensure_ascii=True, allow_nan=False, separators=(',', ':'))
    for chunk in encoder.iterencode(value):
        size += len(chunk)
        if size > max_bytes:
            raise RpcResultTooLargeError('RPC result exceeds wire budget; workflow effects may already exist')
        chunks.append(chunk)
    return ''.join(chunks)
