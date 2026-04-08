"""
MeshLLMGateway — bridges incoming Meshtastic messages with the LLM pipeline.

Responsibilities:
- Subscribe to incoming text packets via pubsub
- Manage per-(node, channel) conversation sessions with SQLite persistence
- Queue messages and process them sequentially in a background thread
- Send replies back via DM (ACK-confirmed) or broadcast
"""

import time
import queue
import threading

from pubsub import pub

from chat_mesh.config import (
    MESH_MAX_CHUNK,
    CHUNK_DELAY,
    ACK_TIMEOUT,
    CHARS_PER_TOKEN,
)
from chat_mesh.db.store import SessionStore
from chat_mesh.llm.prompt import build_prompt, compress_history, collect_streamer, strip_think
from chat_mesh.mesh.radio import chunk_text


class MeshLLMGateway:
    def __init__(
        self,
        interface,
        pipe,
        prompt_token_limit: int,
        reply_mode: str = "dm",
        store: SessionStore | None = None,
    ):
        self.interface          = interface
        self.pipe               = pipe
        self.prompt_token_limit = prompt_token_limit
        self.reply_mode         = reply_mode
        self.store              = store or SessionStore()

        # in-memory cache: {(node_id, channel): {"history": [...], "summary": ""}}
        # loaded lazily from DB on first message from each (node, channel) pair
        self._sessions: dict = {}
        self.lock = threading.Lock()

        # ACK tracking: {packet_id: {"event": Event, "ok": bool}}
        self._pending_acks: dict = {}

        # sequential work queue — LLM runs in one background thread
        self.work_queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._process_loop, daemon=True)
        self._worker.start()

        pub.subscribe(self._on_receive, "meshtastic.receive.text")
        pub.subscribe(self._on_ack,     "meshtastic.receive.routing")
        print("[Gateway] Listening for Meshtastic text messages…")

    # ── session helpers ───────────────────────────────────────────────────────

    def _get_session(self, node_id: str, channel: int) -> dict:
        """Return the in-memory session, loading from DB if not yet cached."""
        key = (node_id, channel)
        with self.lock:
            if key not in self._sessions:
                self._sessions[key] = self.store.load_session(node_id, channel)
            return self._sessions[key]

    def _drop_session(self, node_id: str, channel: int):
        key = (node_id, channel)
        with self.lock:
            self._sessions.pop(key, None)
        self.store.delete_session(node_id, channel)

    # ── incoming message callback (Meshtastic thread) ─────────────────────────

    def _on_receive(self, packet, interface):
        try:
            decoded = packet.get("decoded", {})
            text    = decoded.get("text", "").strip()
            from_id = packet.get("fromId", "unknown")
            channel = packet.get("channel", 0)

            if not text:
                return

            if text.lower() in ("!reset", "/reset"):
                self._drop_session(from_id, channel)
                if self.reply_mode == "broadcast":
                    self.interface.sendText(f"[{from_id}] History cleared.", channelIndex=0)
                else:
                    ok = self._send_dm("History cleared.", from_id, channel)
                    print(f"[ACK] reset → {from_id}: {'✓' if ok else '✗ not received'}")
                return

            print(f"[RX] {from_id} ch{channel}: {text}")
            self.work_queue.put((from_id, channel, text))

        except Exception as e:
            print(f"[ERROR] on_receive: {e}")

    # ── ACK handler (routing packets from firmware) ───────────────────────────

    def _on_ack(self, packet, interface):
        try:
            decoded    = packet.get("decoded", {})
            request_id = decoded.get("requestId") or decoded.get("request_id")
            if request_id is None:
                return
            error = decoded.get("routing", {}).get("errorReason", "NONE")
            with self.lock:
                entry = self._pending_acks.get(request_id)
            if entry:
                entry["ok"] = (error == "NONE")
                entry["event"].set()
        except Exception as e:
            print(f"[ERROR] on_ack: {e}")

    # ── send a DM with ACK confirmation ──────────────────────────────────────

    def _send_dm(self, text: str, node_id: str, channel: int) -> bool:
        """Send a direct message with wantAck=True. Returns True if acknowledged."""
        packet    = self.interface.sendText(text, destinationId=node_id, channelIndex=channel, wantAck=True)
        packet_id = getattr(packet, "id", None)
        if not packet_id:
            return True

        event = threading.Event()
        with self.lock:
            self._pending_acks[packet_id] = {"event": event, "ok": False}

        received = event.wait(timeout=ACK_TIMEOUT)

        with self.lock:
            entry = self._pending_acks.pop(packet_id, {})

        return received and entry.get("ok", False)

    # ── background worker ─────────────────────────────────────────────────────

    def _process_loop(self):
        while True:
            item = self.work_queue.get()
            if item is None:
                break
            from_id, channel, text = item
            try:
                self._handle(from_id, channel, text)
            except Exception as e:
                print(f"[ERROR] handle: {e}")
            finally:
                self.work_queue.task_done()

    def _handle(self, from_id: str, channel: int, user_text: str):
        session = self._get_session(from_id, channel)
        history = list(session["history"])
        summary = session["summary"]

        # compress history if approaching the token limit
        prompt = build_prompt(history, summary, user_text)
        if len(prompt) // CHARS_PER_TOKEN >= self.prompt_token_limit and history:
            print(f"[{from_id}] Compressing history…")
            summary, history = compress_history(self.pipe, history, summary)
            with self.lock:
                session = self._sessions[(from_id, channel)]
                session["history"] = history
                session["summary"] = summary
            self.store.replace_history(from_id, channel, history, summary)
            prompt = build_prompt(history, summary, user_text)

        # generate — full response collected before anything is sent
        print(f"[{from_id}] ch{channel} generating…")
        tokens: list[str] = []
        try:
            self.pipe.generate(prompt, streamer=collect_streamer(tokens))
        except Exception as e:
            err = str(e)
            if "tokens" in err.lower() and history:
                print(f"[{from_id}] Token limit hit, compressing and retrying…")
                summary, history = compress_history(self.pipe, history, summary)
                with self.lock:
                    session = self._sessions[(from_id, channel)]
                    session["history"] = history
                    session["summary"] = summary
                self.store.replace_history(from_id, channel, history, summary)
                prompt = build_prompt(history, summary, user_text)
                tokens = []
                self.pipe.generate(prompt, streamer=collect_streamer(tokens))
            else:
                raise

        reply = strip_think("".join(tokens))
        if "Assistant:" in reply:
            reply = reply.split("Assistant:")[-1].strip()

        # persist to memory cache and DB
        new_turns = [("user", user_text), ("assistant", reply)]
        with self.lock:
            self._sessions[(from_id, channel)]["history"].extend(new_turns)
        self.store.append_messages(from_id, channel, new_turns)

        self._transmit(reply, from_id, channel)

    def _transmit(self, reply: str, from_id: str, channel: int):
        """Send the complete reply, chunked to fit Meshtastic packets."""
        if self.reply_mode == "broadcast":
            prefix = f"@{from_id}: "
            chunks = chunk_text(reply, size=MESH_MAX_CHUNK - len(prefix.encode()))
            print(f"[TX → broadcast] {len(chunks)} chunk(s) | {reply[:60]}{'…' if len(reply)>60 else ''}")
            for i, chunk in enumerate(chunks):
                msg = f"{prefix}[{i+1}/{len(chunks)}] {chunk}" if len(chunks) > 1 else f"{prefix}{chunk}"
                self.interface.sendText(msg, channelIndex=0)
                if i < len(chunks) - 1:
                    time.sleep(CHUNK_DELAY)
        else:
            chunks = chunk_text(reply)
            print(f"[TX → {from_id}] {len(chunks)} chunk(s) | {reply[:60]}{'…' if len(reply)>60 else ''}")
            for i, chunk in enumerate(chunks):
                msg = f"[{i+1}/{len(chunks)}] {chunk}" if len(chunks) > 1 else chunk
                ok  = self._send_dm(msg, from_id, channel)
                print(f"[ACK] chunk {i+1}/{len(chunks)} → {from_id}: {'✓' if ok else '✗ not received'}")
                if not ok:
                    print(f"[WARN] chunk {i+1} not acknowledged — stopping remaining chunks")
                    break
                if i < len(chunks) - 1:
                    time.sleep(CHUNK_DELAY)

    def stop(self):
        self.work_queue.put(None)
        self._worker.join(timeout=5)
