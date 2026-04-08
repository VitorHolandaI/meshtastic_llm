"""
Integration tests for MeshLLMGateway.

Radio interface and LLM pipe are mocked — only the gateway logic,
session handling, and DB persistence are exercised with real code.
"""

import time
import threading
import pytest
from unittest.mock import MagicMock, patch

from chat_mesh.db.store     import SessionStore
from chat_mesh.mesh.gateway import MeshLLMGateway


# ── fixtures ──────────────────────────────────────────────────────────────────

def make_pipe(response: str = "Test reply"):
    """Return a mock LLM pipe whose generate() appends tokens to the streamer."""
    pipe = MagicMock()

    def fake_generate(prompt, streamer=None, **kwargs):
        if streamer:
            for token in response.split():
                streamer(token + " ")

    pipe.generate.side_effect = fake_generate
    return pipe


def make_interface():
    """Return a mock Meshtastic interface that captures sent messages."""
    iface = MagicMock()
    iface.sent = []

    def fake_send(text, destinationId=None, channelIndex=0, wantAck=False):
        iface.sent.append({"text": text, "to": destinationId, "ch": channelIndex})
        packet = MagicMock()
        packet.id = None   # disable ACK tracking in tests
        return packet

    iface.sendText.side_effect = fake_send
    return iface


def make_packet(text: str, from_id: str = "!aaa111", channel: int = 0) -> dict:
    return {
        "decoded": {"text": text},
        "fromId":  from_id,
        "channel": channel,
    }


@pytest.fixture
def store():
    return SessionStore(db_path=":memory:")


@pytest.fixture
def gateway(store):
    iface = make_interface()
    pipe  = make_pipe("Test reply")
    gw    = MeshLLMGateway(iface, pipe, prompt_token_limit=3200, reply_mode="dm", store=store)
    yield gw
    gw.stop()


# ── helper: send a packet and wait for processing ────────────────────────────

def send_and_wait(gw, packet, timeout=3.0):
    gw._on_receive(packet, gw.interface)
    gw.work_queue.join()


# ── basic message flow ────────────────────────────────────────────────────────

def test_message_triggers_reply(gateway):
    send_and_wait(gateway, make_packet("hello"))
    assert gateway.interface.sendText.called


def test_reply_sent_to_correct_node(gateway):
    send_and_wait(gateway, make_packet("hello", from_id="!aaa111"))
    call_kwargs = gateway.interface.sendText.call_args_list[0]
    assert call_kwargs[1].get("destinationId") == "!aaa111"


def test_reply_sent_on_correct_channel(gateway):
    send_and_wait(gateway, make_packet("hello", channel=2))
    call_kwargs = gateway.interface.sendText.call_args_list[0]
    assert call_kwargs[1].get("channelIndex") == 2


def test_empty_message_ignored(gateway):
    send_and_wait(gateway, make_packet(""))
    assert not gateway.interface.sendText.called


# ── session persistence ───────────────────────────────────────────────────────

def test_history_saved_to_db_after_message(gateway, store):
    send_and_wait(gateway, make_packet("hi", from_id="!abc", channel=0))
    session = store.load_session("!abc", 0)
    assert any(role == "user" and "hi" in content for role, content in session["history"])


def test_assistant_reply_saved_to_db(gateway, store):
    send_and_wait(gateway, make_packet("hi", from_id="!abc", channel=0))
    session = store.load_session("!abc", 0)
    assert any(role == "assistant" for role, _ in session["history"])


def test_history_accumulates_across_messages(gateway, store):
    send_and_wait(gateway, make_packet("first",  from_id="!abc", channel=0))
    send_and_wait(gateway, make_packet("second", from_id="!abc", channel=0))
    session = store.load_session("!abc", 0)
    user_messages = [c for r, c in session["history"] if r == "user"]
    assert len(user_messages) == 2


def test_different_nodes_have_independent_sessions(gateway, store):
    send_and_wait(gateway, make_packet("from aaa", from_id="!aaa", channel=0))
    send_and_wait(gateway, make_packet("from bbb", from_id="!bbb", channel=0))
    session_a = store.load_session("!aaa", 0)
    session_b = store.load_session("!bbb", 0)
    assert len(session_a["history"]) == 2   # user + assistant
    assert len(session_b["history"]) == 2
    assert all("from aaa" in c for r, c in session_a["history"] if r == "user")


def test_same_node_different_channels_are_independent(gateway, store):
    send_and_wait(gateway, make_packet("ch0 msg", from_id="!abc", channel=0))
    send_and_wait(gateway, make_packet("ch1 msg", from_id="!abc", channel=1))
    session_ch0 = store.load_session("!abc", 0)
    session_ch1 = store.load_session("!abc", 1)
    assert len(session_ch0["history"]) == 2
    assert len(session_ch1["history"]) == 2


# ── reset command ─────────────────────────────────────────────────────────────

def test_reset_clears_in_memory_session(gateway):
    send_and_wait(gateway, make_packet("hello", from_id="!abc", channel=0))
    gateway._on_receive(make_packet("!reset", from_id="!abc", channel=0), gateway.interface)
    assert ("!abc", 0) not in gateway._sessions


def test_reset_clears_db_session(gateway, store):
    send_and_wait(gateway, make_packet("hello", from_id="!abc", channel=0))
    gateway._on_receive(make_packet("!reset", from_id="!abc", channel=0), gateway.interface)
    session = store.load_session("!abc", 0)
    assert session["history"] == []


def test_slash_reset_works_same_as_exclamation(gateway, store):
    send_and_wait(gateway, make_packet("hello", from_id="!abc", channel=0))
    gateway._on_receive(make_packet("/reset", from_id="!abc", channel=0), gateway.interface)
    session = store.load_session("!abc", 0)
    assert session["history"] == []


# ── broadcast mode ────────────────────────────────────────────────────────────

def test_broadcast_sends_to_channel_0():
    store = SessionStore(db_path=":memory:")
    iface = make_interface()
    pipe  = make_pipe("Broadcast reply")
    gw    = MeshLLMGateway(iface, pipe, prompt_token_limit=3200, reply_mode="broadcast", store=store)
    send_and_wait(gw, make_packet("hello", from_id="!abc", channel=0))
    gw.stop()
    sent = iface.sent
    assert all(s["ch"] == 0 for s in sent)


def test_broadcast_prefixes_sender_id():
    store = SessionStore(db_path=":memory:")
    iface = make_interface()
    pipe  = make_pipe("Answer")
    gw    = MeshLLMGateway(iface, pipe, prompt_token_limit=3200, reply_mode="broadcast", store=store)
    send_and_wait(gw, make_packet("hello", from_id="!abc123", channel=0))
    gw.stop()
    assert any("!abc123" in s["text"] for s in iface.sent)


# ── ACK behaviour ─────────────────────────────────────────────────────────────

def test_failed_ack_stops_remaining_chunks(store):
    """If ACK times out, no further chunks should be sent."""
    iface = make_interface()

    # Return a real packet id so ACK tracking is activated, but never fire the ACK
    call_count = {"n": 0}
    def fake_send(text, destinationId=None, channelIndex=0, wantAck=False):
        call_count["n"] += 1
        packet = MagicMock()
        packet.id = 999  # non-None → ACK tracking enabled, but event never set
        return packet
    iface.sendText.side_effect = fake_send

    # Very short timeout so test doesn't hang
    with patch("chat_mesh.mesh.gateway.ACK_TIMEOUT", 0.1):
        # Long reply that would need multiple chunks
        long_reply = "word " * 60
        pipe = make_pipe(long_reply)
        gw   = MeshLLMGateway(iface, pipe, prompt_token_limit=3200, reply_mode="dm", store=store)
        send_and_wait(gw, make_packet("hello"), timeout=5.0)
        gw.stop()

    # Should have stopped after the first unacknowledged chunk
    assert call_count["n"] == 1
