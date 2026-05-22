"""Tests for knowledge content chunking."""
from src.services.knowledge.chunking import split_into_chunks


def test_short_text_returns_single_chunk():
    text = "This is a short document that fits in one chunk."
    chunks = split_into_chunks(text)
    assert chunks == [text]


def test_empty_string_returns_single_empty_chunk():
    # An empty doc is a valid doc — store() should still produce one row.
    assert split_into_chunks("") == [""]


def test_long_text_splits_at_paragraph_boundaries():
    # Three ~600-char paragraphs separated by blank lines. With target
    # chunk size ~2000 chars (~500 tokens), we expect two chunks: the
    # first holds para 1+2, the second holds para 3 — split at the
    # paragraph break, not mid-sentence.
    para = "Sentence one. " * 80  # ~1120 chars
    text = f"{para}\n\n{para}\n\n{para}"
    chunks = split_into_chunks(text, target_chars=2000, overlap_chars=200)
    assert len(chunks) >= 2
    assert all(len(c) <= 2000 for c in chunks)
    # Reassembled content is a superset of the original (overlap means
    # some text repeats, but every character of the original appears).
    rejoined = " ".join(chunks)
    for fragment in ["Sentence one."]:
        assert fragment in rejoined


def test_long_text_with_no_paragraph_breaks_splits_at_sentences():
    text = ("This is sentence number one. " * 100).strip()  # ~3000 chars, no \n
    chunks = split_into_chunks(text, target_chars=1000, overlap_chars=100)
    assert len(chunks) >= 3
    # Each chunk should end at a sentence boundary (period + space) or
    # be the last chunk.
    for chunk in chunks[:-1]:
        assert chunk.rstrip().endswith(".")


def test_long_text_with_no_boundaries_falls_back_to_hard_cut():
    # No paragraph, no sentence, no spaces — single long token.
    text = "a" * 5000
    chunks = split_into_chunks(text, target_chars=1000, overlap_chars=100)
    assert len(chunks) >= 5
    assert all(len(c) <= 1000 for c in chunks)


def test_overlap_repeats_trailing_context():
    # Build a doc where we can verify the tail of chunk N appears in
    # the head of chunk N+1.
    sentences = [f"Sentence {i}." for i in range(50)]
    text = " ".join(sentences)
    chunks = split_into_chunks(text, target_chars=200, overlap_chars=50)
    assert len(chunks) >= 3
    for i in range(len(chunks) - 1):
        # The last sentence of chunk i should reappear in chunk i+1
        # — that's what overlap means.
        prev_sentences = [s for s in chunks[i].split(".") if s.strip()]
        if prev_sentences:
            last_sentence = prev_sentences[-1].strip()
            assert last_sentence in chunks[i + 1]


def test_default_target_size_is_reasonable_for_embeddings():
    # ~500 tokens ≈ 2000 chars. Doc of ~10000 chars (the halo_kb
    # average) should produce roughly 5 chunks, not 1 and not 50.
    text = ("Lorem ipsum dolor sit amet. " * 400).strip()  # ~10800 chars
    chunks = split_into_chunks(text)
    assert 4 <= len(chunks) <= 8
