"""
Content chunking for the knowledge store.

Splits long documents into ~target_chars windows with overlap, preferring
natural boundaries (paragraph → sentence → word → hard cut) so that
embeddings are computed over coherent text instead of mid-sentence fragments.

Why character-based and not token-based:
- We don't want a tokenizer dependency in this layer (the embedding client
  owns that). ~4 chars/token is a stable approximation across English text
  and current OpenAI/Cohere/Anthropic models.
- The exact chunk size doesn't matter — embeddings degrade smoothly with
  size. Anywhere from 400-800 tokens is fine. We aim for ~500 (2000 chars).
"""
from __future__ import annotations

DEFAULT_TARGET_CHARS = 2000   # ~500 tokens
DEFAULT_OVERLAP_CHARS = 200   # ~50 tokens of trailing context repeated


def split_into_chunks(
    text: str,
    target_chars: int = DEFAULT_TARGET_CHARS,
    overlap_chars: int = DEFAULT_OVERLAP_CHARS,
) -> list[str]:
    """
    Split `text` into chunks of at most `target_chars`, preferring natural
    boundaries. Returns at least one chunk (an empty list is never valid —
    an empty doc returns `[""]`).
    """
    if len(text) <= target_chars:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + target_chars, len(text))
        if end < len(text):
            end = _find_boundary(text, start, end)
        chunk = text[start:end]
        chunks.append(chunk)
        if end >= len(text):
            break
        # Step forward by (chunk_length - overlap) so the next window
        # repeats the last `overlap_chars` of this one.
        start = max(end - overlap_chars, start + 1)
    return chunks


def _find_boundary(text: str, start: int, end: int) -> int:
    """
    Walk backward from `end` looking for a natural boundary. The search
    window is bounded by `start + (end-start)//2` so we never produce a
    chunk smaller than half the target (avoids pathological short chunks
    when boundaries are sparse).
    """
    min_acceptable = start + (end - start) // 2
    for boundary in ("\n\n", ". ", "? ", "! ", "\n", " "):
        idx = text.rfind(boundary, min_acceptable, end)
        if idx != -1:
            return idx + len(boundary)
    return end  # Hard cut — no boundary found in the search window.
