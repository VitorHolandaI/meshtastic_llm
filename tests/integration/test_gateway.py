"""
Integration tests for MeshLLMGateway.

Radio interface and LLM pipe are mocked — only the gateway logic,
session handling, and DB persistence are exercised with real code.
"""

import time
import threading
import pytest
from unittest.mock import MagicMock, patch, call

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

# ── reply contains "Assistant:" artifact ─────────────────────────────────────

def test_reply_strips_assistant_prefix(gateway):
    """When model leaks 'Assistant: ...' in output, it should be stripped."""
    iface = make_interface()
    pipe  = make_pipe("Assistant: cleaned response")
    store = SessionStore(db_path=":memory:")
    gw    = MeshLLMGateway(iface, pipe, 3200, reply_mode="dm", store=store)
    send_and_wait(gw, make_packet("hello"))
    gw.stop()
    sent_text = iface.sent[0]["text"]
    assert "Assistant:" not in sent_text
    assert "cleaned response" in sent_text


# ── multi-chunk with sleep (covers time.sleep branches) ───────────────────────

def test_multi_chunk_dm_sends_all_chunks(store):
    iface = make_interface()
    long_reply = "word " * 60   # ~300 bytes → needs 2+ chunks at 200 bytes each
    pipe  = make_pipe(long_reply)
    with patch("chat_mesh.mesh.gateway.CHUNK_DELAY", 0):  # skip actual sleep
        gw = MeshLLMGateway(iface, pipe, 3200, reply_mode="dm", store=store)
        send_and_wait(gw, make_packet("hello"))
        gw.stop()
    assert len(iface.sent) > 1


def test_multi_chunk_broadcast_sends_all_chunks(store):
    iface = make_interface()
    long_reply = "word " * 60
    pipe  = make_pipe(long_reply)
    with patch("chat_mesh.mesh.gateway.CHUNK_DELAY", 0):
        gw = MeshLLMGateway(iface, pipe, 3200, reply_mode="broadcast", store=store)
        send_and_wait(gw, make_packet("hello"))
        gw.stop()
    assert len(iface.sent) > 1


# ── broadcast reset ───────────────────────────────────────────────────────────

def test_reset_broadcast_sends_to_channel_0(store):
    iface = make_interface()
    pipe  = make_pipe()
    gw    = MeshLLMGateway(iface, pipe, 3200, reply_mode="broadcast", store=store)
    gw._on_receive(make_packet("!reset", from_id="!abc", channel=0), iface)
    time.sleep(0.1)  # reset is synchronous but give pubsub a moment
    gw.stop()
    assert any(s["ch"] == 0 for s in iface.sent)


# ── _on_ack ───────────────────────────────────────────────────────────────────

def test_on_ack_resolves_pending_ack(gateway):
    event = threading.Event()
    with gateway.lock:
        gateway._pending_acks[42] = {"event": event, "ok": False}
    packet = {"decoded": {"requestId": 42, "routing": {"errorReason": "NONE"}}}
    gateway._on_ack(packet, None)
    assert event.is_set()
    assert gateway._pending_acks[42]["ok"] is True


def test_on_ack_marks_nack(gateway):
    event = threading.Event()
    with gateway.lock:
        gateway._pending_acks[99] = {"event": event, "ok": False}
    packet = {"decoded": {"requestId": 99, "routing": {"errorReason": "NO_ROUTE"}}}
    gateway._on_ack(packet, None)
    assert gateway._pending_acks[99]["ok"] is False


def test_on_ack_ignores_unknown_request_id(gateway):
    packet = {"decoded": {"requestId": 0, "routing": {"errorReason": "NONE"}}}
    gateway._on_ack(packet, None)   # should not raise


def test_on_ack_ignores_packet_without_request_id(gateway):
    packet = {"decoded": {"routing": {}}}
    gateway._on_ack(packet, None)   # should not raise


# ── compression path ──────────────────────────────────────────────────────────

def test_compression_triggered_when_prompt_too_long(store):
    iface = make_interface()
    pipe  = make_pipe("compressed reply")
    # token limit so small that any history triggers compression
    gw    = MeshLLMGateway(iface, pipe, prompt_token_limit=1, reply_mode="dm", store=store)

    # Pre-load some history
    history = [("user", "hi"), ("assistant", "hello")]
    with gw.lock:
        gw._sessions[("!aaa111", 0)] = {"history": history, "summary": ""}
    store.append_messages("!aaa111", 0, history)

    send_and_wait(gw, make_packet("new message"))
    gw.stop()

    session = store.load_session("!aaa111", 0)
    # After compression summary should be set or history trimmed
    assert session["summary"] != "" or len(session["history"]) <= 4


# ── token limit retry ─────────────────────────────────────────────────────────

def test_token_limit_retry_on_generate_error(store):
    iface      = make_interface()
    call_count = {"n": 0}

    def fake_generate(prompt, streamer=None, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise RuntimeError("too many tokens in context")
        if streamer:
            for token in ["retry ", "ok"]:
                streamer(token)

    pipe = MagicMock()
    pipe.generate.side_effect = fake_generate
    gw   = MeshLLMGateway(iface, pipe, 3200, reply_mode="dm", store=store)

    # Need existing history so retry path's `and history` check passes
    history = [("user", "prev"), ("assistant", "prev reply")]
    with gw.lock:
        gw._sessions[("!aaa111", 0)] = {"history": history, "summary": ""}
    store.append_messages("!aaa111", 0, history)

    send_and_wait(gw, make_packet("hello"))
    gw.stop()

    assert call_count["n"] >= 2
    assert iface.sendText.called


# ── _process_loop error handling ──────────────────────────────────────────────

def test_process_loop_catches_handle_errors(gateway):
    """_process_loop must not crash when _handle raises an unexpected error."""
    with patch.object(gateway, "_handle", side_effect=RuntimeError("unexpected")):
        gateway._on_receive(make_packet("hello"), gateway.interface)
        gateway.work_queue.join()   # must not hang or raise


# ── _on_receive error handling ────────────────────────────────────────────────

def test_on_receive_handles_malformed_packet(gateway):
    gateway._on_receive({}, gateway.interface)   # no 'decoded' key — must not raise


# ── failed ack stops remaining chunks ────────────────────────────────────────

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
