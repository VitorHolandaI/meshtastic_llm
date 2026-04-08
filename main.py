#!/usr/bin/env python3
"""
Meshtastic <-> OpenVINO LLM Gateway — entry point.
Run with --help for full usage information.
"""

import os
import sys
import time
import argparse

from dotenv import load_dotenv
load_dotenv()

import meshtastic
import meshtastic.serial_interface
import meshtastic.tcp_interface

from chat_mesh.db.store     import SessionStore
from chat_mesh.llm.pipeline import load_pipeline
from chat_mesh.mesh.gateway import MeshLLMGateway
from chat_mesh.mesh.radio   import find_models, choose


def build_parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="main.py",
        description=(
            "Meshtastic <-> OpenVINO LLM Gateway\n"
            "Receives text messages from LoRa radio nodes, generates replies\n"
            "with a local LLM, and sends them back. Works fully offline."
        ),
        epilog=(
            "examples:\n"
            "  python main.py\n"
            "      Interactive menus — pick model and device on startup.\n\n"
            "  python main.py --port /dev/ttyUSB0 --model ./qwen2.5-1.5b-int4 --device CPU\n"
            "      Serial connection, specific model, CPU inference.\n\n"
            "  python main.py --host 192.168.1.10 --model ./qwen2.5-1.5b-int4 --device NPU\n"
            "      TCP connection (WiFi device), NPU inference.\n\n"
            "  python main.py --reply-mode broadcast\n"
            "      Replies visible to all nodes on channel 0 (LongFast).\n\n"
            "radio commands (send from any Meshtastic node):\n"
            "  !reset   Clear your conversation history with the gateway.\n\n"
            "environment variables (can also be set in .env):\n"
            "  MESH_PORT, MESH_HOST, MESH_MODEL, MESH_DEVICE,\n"
            "  MESH_REPLY_MODE, MESH_CHANNEL_PSK\n\n"
            "encryption:\n"
            "  All encryption is handled by the Meshtastic firmware at the device\n"
            "  level. The gateway only sees plain text. To match keys across devices:\n"
            "    meshtastic --ch-index 0 --ch-set psk default   (factory default)\n"
            "    meshtastic --ch-index 0 --ch-set psk base64:KEY (custom key)\n"
            "    openssl rand -base64 32                         (generate a key)\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )


def parse_args(parser: argparse.ArgumentParser) -> argparse.Namespace:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--port",
        default=os.getenv("MESH_PORT"),
        metavar="PORT",
        help="Serial port for USB-connected Meshtastic device (e.g. /dev/ttyUSB0, COM3). "
             "Auto-detected if omitted. [env: MESH_PORT]",
    )
    group.add_argument(
        "--host",
        default=os.getenv("MESH_HOST"),
        metavar="HOST",
        help="IP address or hostname for a WiFi/TCP-connected Meshtastic device. "
             "Mutually exclusive with --port. [env: MESH_HOST]",
    )
    parser.add_argument(
        "--model",
        default=os.getenv("MESH_MODEL"),
        metavar="PATH",
        help="Path to an OpenVINO IR model directory (must contain .xml + .bin files). "
             "Presents an interactive menu if omitted. [env: MESH_MODEL]",
    )
    parser.add_argument(
        "--device",
        default=os.getenv("MESH_DEVICE"),
        choices=["CPU", "GPU", "NPU", "AUTO"],
        help="Compute device for LLM inference. NPU requires Intel Core Ultra + drivers. "
             "AUTO lets OpenVINO choose the best available device. "
             "Presents an interactive menu if omitted. [env: MESH_DEVICE]",
    )
    parser.add_argument(
        "--reply-mode",
        default=os.getenv("MESH_REPLY_MODE", "dm"),
        choices=["dm", "broadcast"],
        help="dm: reply goes only to the sender, ACK-confirmed (default). "
             "broadcast: reply is sent to channel 0 and visible to all nearby nodes. "
             "[env: MESH_REPLY_MODE]",
    )
    parser.add_argument(
        "--db",
        default=os.getenv("MESH_DB_PATH", "db/sessions.db"),
        metavar="PATH",
        help="Path to the SQLite database file for session persistence. "
             "[env: MESH_DB_PATH] (default: db/sessions.db)",
    )
    parser.add_argument(
        "--channel-psk",
        default=os.getenv("MESH_CHANNEL_PSK", "AQ=="),
        metavar="BASE64",
        help="Base64-encoded AES PSK for channel 0. Informational — the key must already "
             "be configured on the physical devices. Default AQ== is the public factory key. "
             "[env: MESH_CHANNEL_PSK]",
    )
    return parser.parse_args()


def main():
    args = parse_args(build_parser())

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
    device = args.device or choose("Select compute device:", ["CPU", "GPU", "NPU", "AUTO"])

    # ── load LLM ─────────────────────────────────────────────────────────────
    print(f"\nLoading model from '{model_path}' on {device}…")
    pipe, prompt_token_limit = load_pipeline(model_path, device)
    print("Model loaded.")

    # ── connect to Meshtastic ─────────────────────────────────────────────────
    print("\nConnecting to Meshtastic device…")
    try:
        if args.host:
            iface = meshtastic.tcp_interface.TCPInterface(args.host)
        elif args.port:
            iface = meshtastic.serial_interface.SerialInterface(args.port)
        else:
            iface = meshtastic.serial_interface.SerialInterface()
    except Exception as e:
        print(f"Failed to connect to Meshtastic device: {e}")
        sys.exit(1)

    node_info = iface.getMyNodeInfo()
    my_id = node_info.get("user", {}).get("id", "unknown")
    print(f"Connected! Gateway node ID: {my_id}")
    print(f"Reply mode   : {args.reply_mode}")
    print(f"Channel PSK  : {args.channel_psk}  (must match device — set via meshtastic CLI or app)")
    print(f"Database     : {args.db}")
    print("\nReady. Send any text message to this node to chat with the LLM.")
    print("Send '!reset' to clear your conversation history.\n")

    store   = SessionStore(db_path=args.db)
    gateway = MeshLLMGateway(iface, pipe, prompt_token_limit, reply_mode=args.reply_mode, store=store)

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
