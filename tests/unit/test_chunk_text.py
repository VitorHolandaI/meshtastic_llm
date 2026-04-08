"""
Tests for chunk_text — the function that splits LLM replies into
Meshtastic-safe packets (≤ 200 bytes by default).

Critical because a bug here silently corrupts every message sent over radio.
"""

import pytest
from chat_mesh.mesh.radio import chunk_text


# ── basic splitting ───────────────────────────────────────────────────────────

def test_short_text_is_single_chunk():
    result = chunk_text("Hello world")
    assert result == ["Hello world"]


def test_empty_string_returns_one_empty_chunk():
    result = chunk_text("")
    assert result == [""]


def test_single_word_longer_than_limit_is_kept_as_one_chunk():
    # A single word that exceeds the limit cannot be split — keep it as-is
    long_word = "a" * 300
    result = chunk_text(long_word, size=200)
    assert result == [long_word]


def test_exact_size_boundary_stays_in_one_chunk():
    word = "a" * 100
    text = f"{word} {word}"   # 201 chars but two words — should split
    result = chunk_text(text, size=200)
    assert len(result) == 2
    assert result[0] == word
    assert result[1] == word


def test_text_splits_into_multiple_chunks():
    # 10 words of 30 chars each → ~300 bytes, should not all fit in 200
    word  = "x" * 29          # 29 chars + 1 space = 30 bytes
    text  = " ".join([word] * 10)
    result = chunk_text(text, size=200)
    assert len(result) > 1


def test_no_chunk_exceeds_size_limit():
    word   = "hello"
    text   = " ".join([word] * 100)
    result = chunk_text(text, size=50)
    for chunk in result:
        assert len(chunk.encode()) <= 50


def test_all_words_are_present_across_chunks():
    words  = ["alpha", "beta", "gamma", "delta", "epsilon", "zeta"]
    text   = " ".join(words)
    result = chunk_text(text, size=20)
    reassembled = " ".join(result)
    assert reassembled == text


def test_no_chunk_is_empty():
    text   = "word " * 50
    result = chunk_text(text.strip(), size=30)
    for chunk in result:
        assert chunk.strip() != ""


# ── custom size ───────────────────────────────────────────────────────────────

def test_custom_size_respected():
    text   = "one two three four five"
    result = chunk_text(text, size=10)
    for chunk in result:
        assert len(chunk.encode()) <= 10


def test_size_1_each_word_is_its_own_chunk():
    # size so small each word can't share a chunk with another
    result = chunk_text("a b c d", size=2)
    assert result == ["a", "b", "c", "d"]


# ── multibyte (UTF-8) characters ──────────────────────────────────────────────

def test_utf8_text_byte_length_respected():
    # Portuguese / accented chars are 2 bytes each in UTF-8
    word   = "ação"          # 6 bytes
    text   = " ".join([word] * 50)
    result = chunk_text(text, size=40)
    for chunk in result:
        assert len(chunk.encode("utf-8")) <= 40


def test_utf8_words_not_split():
    words  = ["ação", "coração", "avião"]
    text   = " ".join(words)
    result = chunk_text(text, size=200)
    reassembled = " ".join(result)
    assert reassembled == text


# ── edge cases ────────────────────────────────────────────────────────────────

def test_whitespace_only_string():
    result = chunk_text("   ")
    # split() on whitespace-only → no words → returns [""]
    assert result == [""]


def test_single_word_fits_exactly():
    word   = "hello"          # 5 bytes
    result = chunk_text(word, size=5)
    assert result == [word]


def test_repeated_calls_are_deterministic():
    text = "The quick brown fox jumps over the lazy dog"
    assert chunk_text(text, size=20) == chunk_text(text, size=20)
