#!/usr/bin/env python3
"""
Meshtastic <-> OpenVINO LLM Gateway
Receives text messages via LoRa radio, queries a local LLM, sends response back.

Dependencies:
    pip install meshtastic pytap2 openvino-genai

Usage:
    python mesh_llm_gateway.py                    # auto-detect serial port
    python mesh_llm_gateway.py --port /dev/ttyUSB0
    python mesh_llm_gateway.py --host 192.168.1.10  # TCP mode (e.g. for ESP32 WiFi)
    python mesh_llm_gateway.py --port /dev/ttyUSB0 --model ./qwen2.5-1.5b-instruct --device NPU
"""

import os
import sys
import time
import queue
import argparse
import threading
import re

import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface
from pubsub import pub
import openvino_genai as ov_genai

# ── tunables ──────────────────────────────────────────────────────────────────
MESH_MAX_CHUNK   = 200      # bytes per Meshtastic packet (safe margin under 228)
CHUNK_DELAY      = 0.8      # seconds between chunks to avoid flooding
COMPRESS_KEEP    = 3        # recent turns kept after compression
CHARS_PER_TOKEN  = 4        # rough estimate
SYSTEM_PROMPT    = (
    "You are a helpful AI assistant running on a Meshtastic LoRa radio gateway. "
    "Keep answers short and concise — messages are limited in length. "
    "Respond in the same language as the user."
)


# ── LLM helpers (adapted from chat.py) ───────────────────────────────────────

def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def build_prompt(history: list, summary: str, user_input: str) -> str:
    parts = [SYSTEM_PROMPT]
    if summary:
        parts.append(f"\n[Conversation summary]\n{summary}")
    for role, text in history:
        tag = "User" if role == "user" else "Assistant"
        parts.append(f"{tag}: {text}")
    parts.append(f"User: {user_input}")
    parts.append("Assistant:")
    return "\n".join(parts)


def collect_streamer(collector: list):
    """Streamer that hides <think> blocks and collects full output."""
    buf = []
    state = {"thinking": False}

    def streamer(token: str) -> bool:
        buf.append(token)
        collector.append(token)
        combined = "".join(buf)
        if state["thinking"]:
            if "</think>" in combined:
                state["thinking"] = False
                after = combined.split("</think>", 1)[1]
                buf.clear()
                if after:
                    buf.append(after)
        else:
            if "<think>" in combined:
                state["thinking"] = True
                buf.clear()
        return False

    return streamer


def compress_history(pipe, history: list, summary: str) -> tuple[str, list]:
    old_turns  = history[:-COMPRESS_KEEP]
    keep_turns = history[-COMPRESS_KEEP:]
    convo_text = "\n".join(
        f"{'User' if r == 'user' else 'Assistant'}: {t}" for r, t in old_turns
    )
    prefix = f"Existing summary: {summary}\n\n" if summary else ""
    prompt = (
        f"{prefix}Summarise the following conversation briefly:\n\n"
        f"{convo_text}\n\nSummary:"
    )
    tokens: list[str] = []
    pipe.generate(prompt, max_new_tokens=150, streamer=collect_streamer(tokens))
    return "".join(tokens).strip(), keep_turns


def chunk_text(text: str, size: int = MESH_MAX_CHUNK) -> list[str]:
    """Split text into chunks that fit in a Meshtastic packet."""
    words = text.split()
    chunks, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate.encode()) > size:
            if current:
                chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


# ── Gateway ───────────────────────────────────────────────────────────────────

class MeshLLMGateway:
    def __init__(self, interface, pipe, prompt_token_limit: int):
        self.interface          = interface
        self.pipe               = pipe
        self.prompt_token_limit = prompt_token_limit

        # per-node state: {node_id: {"history": [...], "summary": ""}}
        self.sessions: dict = {}
        self.lock = threading.Lock()

        # work queue so LLM runs sequentially in a background thread
        self.work_queue: queue.Queue = queue.Queue()
        self._worker = threading.Thread(target=self._process_loop, daemon=True)
        self._worker.start()

        pub.subscribe(self._on_receive, "meshtastic.receive.text")
        print("[Gateway] Listening for Meshtastic text messages…")

    # ── incoming message callback (called from Meshtastic's thread) ───────────

    def _on_receive(self, packet, interface):
        try:
            decoded  = packet.get("decoded", {})
            text     = decoded.get("text", "").strip()
            from_id  = packet.get("fromId", "unknown")
            channel  = packet.get("channel", 0)

            if not text:
                return

            # gateway commands
            if text.lower() in ("!reset", "/reset"):
                with self.lock:
                    self.sessions.pop(from_id, None)
                self.interface.sendText("History cleared.", destinationId=from_id, channelIndex=channel)
                return

            print(f"[RX] {from_id}: {text}")
            self.work_queue.put((from_id, channel, text))

        except Exception as e:
            print(f"[ERROR] on_receive: {e}")

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
        with self.lock:
            session = self.sessions.setdefault(from_id, {"history": [], "summary": ""})
            history = session["history"]
            summary = session["summary"]

        # build prompt, compress if needed
        prompt = build_prompt(history, summary, user_text)
        if len(prompt) // CHARS_PER_TOKEN >= self.prompt_token_limit and history:
            print(f"[{from_id}] Compressing history…")
            summary, history = compress_history(self.pipe, history, summary)
            with self.lock:
                self.sessions[from_id]["summary"] = summary
                self.sessions[from_id]["history"] = history
            prompt = build_prompt(history, summary, user_text)

        print(f"[{from_id}] Generating…")
        tokens: list[str] = []
        try:
            self.pipe.generate(prompt, streamer=collect_streamer(tokens))
        except Exception as e:
            err = str(e)
            if "tokens" in err.lower() and history:
                print(f"[{from_id}] Token limit hit, compressing and retrying…")
                summary, history = compress_history(self.pipe, history, summary)
                with self.lock:
                    self.sessions[from_id]["summary"] = summary
                    self.sessions[from_id]["history"] = history
                prompt = build_prompt(history, summary, user_text)
                tokens = []
                self.pipe.generate(prompt, streamer=collect_streamer(tokens))
            else:
                raise

        reply = strip_think("".join(tokens))
        if "Assistant:" in reply:
            reply = reply.split("Assistant:")[-1].strip()

        # persist conversation
        with self.lock:
            self.sessions[from_id]["history"].append(("user", user_text))
            self.sessions[from_id]["history"].append(("assistant", reply))

        print(f"[TX → {from_id}] {reply[:80]}{'…' if len(reply)>80 else ''}")

        # send back in chunks
        chunks = chunk_text(reply)
        for i, chunk in enumerate(chunks):
            if len(chunks) > 1:
                chunk = f"[{i+1}/{len(chunks)}] {chunk}"
            self.interface.sendText(chunk, destinationId=from_id, channelIndex=channel)
            if i < len(chunks) - 1:
                time.sleep(CHUNK_DELAY)

    def stop(self):
        self.work_queue.put(None)
        self._worker.join(timeout=5)


# ── model/device selection helpers ───────────────────────────────────────────

def find_models(search_dir="."):
    candidates = []
    for root, dirs, files in os.walk(search_dir):
        if any(f.endswith(".xml") for f in files):
            candidates.append(root)
        dirs[:] = [d for d in dirs if not d.startswith(".")]
    return sorted(set(candidates))


def choose(prompt_text, options, allow_custom=False):
    print(f"\n{prompt_text}")
    for i, opt in enumerate(options, 1):
        print(f"  [{i}] {opt}")
    if allow_custom:
        print("  [0] Enter custom path")
    while True:
        raw = input("  > ").strip()
        if raw.isdigit():
            idx = int(raw)
            if allow_custom and idx == 0:
                return input("  Custom path: ").strip()
            if 1 <= idx <= len(options):
                return options[idx - 1]
        print("  Invalid choice, try again.")


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Meshtastic ↔ OpenVINO LLM Gateway")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--port",  help="Serial port (e.g. /dev/ttyUSB0)")
    group.add_argument("--host",  help="TCP host for WiFi-connected device")
    parser.add_argument("--model",  help="Path to OpenVINO model directory")
    parser.add_argument("--device", default=None, choices=["CPU", "GPU", "NPU", "AUTO"],
                        help="Compute device (default: interactive menu)")
    args = parser.parse_args()

    print("=" * 60)
    print("  Meshtastic <-> OpenVINO LLM Gateway")
    print("=" * 60)

    # ── model selection ───────────────────────────────────────────────────────
    model_path = args.model
    if not model_path:
        models = find_models(".")
        if models:
            model_path = choose("Select a model directory:", models, allow_custom=True)
        else:
            print("\nNo OpenVINO model directories found in current folder.")
            model_path = input("Enter model path manually: ").strip()

    if not os.path.isdir(model_path):
        print(f"ERROR: '{model_path}' is not a valid directory.")
        sys.exit(1)

    # ── device selection ──────────────────────────────────────────────────────
    device = args.device
    if not device:
        device = choose("Select compute device:", ["CPU", "GPU", "NPU", "AUTO"])

    # ── load LLM ─────────────────────────────────────────────────────────────
    print(f"\nLoading model from '{model_path}' on {device}…")
    npu_max_prompt = 4096
    try:
        if device == "NPU":
            pipe = ov_genai.LLMPipeline(model_path, device, MAX_PROMPT_LEN=npu_max_prompt)
            prompt_token_limit = int(npu_max_prompt * 0.75)
        else:
            pipe = ov_genai.LLMPipeline(model_path, device)
            prompt_token_limit = 3200
    except Exception as e:
        print(f"Failed to load model: {e}")
        sys.exit(1)
    print("Model loaded.")

    # ── connect to Meshtastic ─────────────────────────────────────────────────
    print("\nConnecting to Meshtastic device…")
    try:
        if args.host:
            iface = meshtastic.tcp_interface.TCPInterface(args.host)
        elif args.port:
            iface = meshtastic.serial_interface.SerialInterface(args.port)
        else:
            iface = meshtastic.serial_interface.SerialInterface()   # auto-detect
    except Exception as e:
        print(f"Failed to connect to Meshtastic device: {e}")
        sys.exit(1)

    node_info = iface.getMyNodeInfo()
    my_id = node_info.get("user", {}).get("id", "unknown")
    print(f"Connected! Gateway node ID: {my_id}")
    print("\nReady. Send any text message to this node to chat with the LLM.")
    print("Send '!reset' to clear your conversation history.\n")

    gateway = MeshLLMGateway(iface, pipe, prompt_token_limit)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down…")
    finally:
        gateway.stop()
        iface.close()


if __name__ == "__main__":
    main()
