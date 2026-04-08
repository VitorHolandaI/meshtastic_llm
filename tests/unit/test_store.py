"""
Tests for SessionStore using an in-memory SQLite database.
No files written to disk — each test gets a fresh :memory: DB.
"""

import pytest
from chat_mesh.db.store import SessionStore


@pytest.fixture
def store():
    return SessionStore(db_path=":memory:")


# ── load_session ──────────────────────────────────────────────────────────────

def test_load_nonexistent_session_returns_empty(store):
    session = store.load_session("!abc123", 0)
    assert session["history"] == []
    assert session["summary"] == ""


def test_load_session_different_nodes_are_independent(store):
    store.append_messages("!aaa", 0, [("user", "hello")])
    session_b = store.load_session("!bbb", 0)
    assert session_b["history"] == []


def test_load_session_same_node_different_channels_are_independent(store):
    store.append_messages("!aaa", 0, [("user", "ch0 message")])
    session = store.load_session("!aaa", 1)
    assert session["history"] == []


# ── append_messages ───────────────────────────────────────────────────────────

def test_append_messages_persists(store):
    store.append_messages("!abc", 0, [("user", "hi"), ("assistant", "hello")])
    session = store.load_session("!abc", 0)
    assert session["history"] == [("user", "hi"), ("assistant", "hello")]


def test_append_messages_accumulates(store):
    store.append_messages("!abc", 0, [("user", "one")])
    store.append_messages("!abc", 0, [("assistant", "two")])
    store.append_messages("!abc", 0, [("user", "three")])
    session = store.load_session("!abc", 0)
    assert [r for r, _ in session["history"]] == ["user", "assistant", "user"]


def test_append_messages_preserves_order(store):
    turns = [("user", "a"), ("assistant", "b"), ("user", "c"), ("assistant", "d")]
    store.append_messages("!abc", 0, turns)
    session = store.load_session("!abc", 0)
    assert session["history"] == turns


def test_append_messages_creates_session_implicitly(store):
    # Should not raise even if session row doesn't exist yet
    store.append_messages("!new", 5, [("user", "first message")])
    session = store.load_session("!new", 5)
    assert len(session["history"]) == 1


# ── replace_history ───────────────────────────────────────────────────────────

def test_replace_history_overwrites_old_messages(store):
    store.append_messages("!abc", 0, [("user", "old1"), ("assistant", "old2")])
    store.replace_history("!abc", 0, [("user", "kept")], summary="summary text")
    session = store.load_session("!abc", 0)
    assert session["history"] == [("user", "kept")]


def test_replace_history_updates_summary(store):
    store.append_messages("!abc", 0, [("user", "hi")])
    store.replace_history("!abc", 0, [], summary="we talked about stuff")
    session = store.load_session("!abc", 0)
    assert session["summary"] == "we talked about stuff"


def test_replace_history_with_empty_keeps_summary(store):
    store.replace_history("!abc", 0, [], summary="compressed summary")
    session = store.load_session("!abc", 0)
    assert session["history"] == []
    assert session["summary"] == "compressed summary"


def test_replace_history_does_not_affect_other_sessions(store):
    store.append_messages("!aaa", 0, [("user", "keep me")])
    store.replace_history("!bbb", 0, [("user", "replaced")], summary="")
    session_a = store.load_session("!aaa", 0)
    assert session_a["history"] == [("user", "keep me")]


# ── delete_session ────────────────────────────────────────────────────────────

def test_delete_session_removes_history(store):
    store.append_messages("!abc", 0, [("user", "hi"), ("assistant", "hello")])
    store.delete_session("!abc", 0)
    session = store.load_session("!abc", 0)
    assert session["history"] == []
    assert session["summary"] == ""


def test_delete_session_only_affects_target(store):
    store.append_messages("!aaa", 0, [("user", "keep")])
    store.append_messages("!bbb", 0, [("user", "delete me")])
    store.delete_session("!bbb", 0)
    session = store.load_session("!aaa", 0)
    assert session["history"] == [("user", "keep")]


def test_delete_nonexistent_session_does_not_raise(store):
    store.delete_session("!ghost", 99)  # should not raise


def test_delete_only_target_channel(store):
    store.append_messages("!abc", 0, [("user", "ch0")])
    store.append_messages("!abc", 1, [("user", "ch1")])
    store.delete_session("!abc", 0)
    session_ch1 = store.load_session("!abc", 1)
    assert session_ch1["history"] == [("user", "ch1")]


# ── multiple sessions coexist ─────────────────────────────────────────────────

def test_multiple_nodes_multiple_channels(store):
    store.append_messages("!aaa", 0, [("user", "a-ch0")])
    store.append_messages("!aaa", 1, [("user", "a-ch1")])
    store.append_messages("!bbb", 0, [("user", "b-ch0")])

    assert store.load_session("!aaa", 0)["history"] == [("user", "a-ch0")]
    assert store.load_session("!aaa", 1)["history"] == [("user", "a-ch1")]
    assert store.load_session("!bbb", 0)["history"] == [("user", "b-ch0")]
    assert store.load_session("!bbb", 1)["history"] == []
