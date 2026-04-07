# chat_mesh — Project Documentation

## What this is

A gateway that bridges **Meshtastic LoRa radio** with a **local LLM running on OpenVINO**. Any Meshtastic device within radio range can send a text message to the gateway node and receive an AI-generated reply — no internet, no cloud, no infrastructure required.

```
[Remote radio]  ──LoRa──>  [Meshtastic node]  ──USB/WiFi──>  [Gateway PC + LLM]
                                                                        │
[Remote radio]  <──LoRa──  [Meshtastic node]  <─────────────  [reply sent back]
```

Designed for off-grid scenarios: field operations, emergencies, mesh networks without connectivity.

---

## Project structure

```
chat_mesh/
├── main.py                        # Entry point — argparse, startup, wires everything together
├── mesh_llm_gateway.py            # Deprecated shim → calls main.py (backwards compat)
├── chat_mesh/                     # Package
│   ├── config.py                  # All tunables and constants (no secrets)
│   ├── llm/
│   │   ├── pipeline.py            # load_pipeline() — OpenVINO model loading
│   │   └── prompt.py              # build_prompt, compress_history, collect_streamer, strip_think
│   └── mesh/
│       ├── gateway.py             # MeshLLMGateway class — session mgmt, ACK, send logic
│       └── radio.py               # chunk_text, find_models, choose (interactive menus)
├── requirements.txt               # Python dependencies
├── .env                           # Local secrets and config (gitignored)
├── .env_dev                       # Template — copy to .env and fill in values
├── .gitignore
└── changes.md                     # This file
```

### Module responsibilities

| Module | Owns |
|---|---|
| `main.py` | CLI parsing, startup sequence, top-level wiring |
| `config.py` | Behaviour knobs (chunk size, delays, timeouts, system prompt) |
| `llm/pipeline.py` | Model loading — swap this to change inference backend |
| `llm/prompt.py` | Prompt assembly, history compression, token streaming |
| `mesh/gateway.py` | Message queue, session state, ACK tracking, transmit logic |
| `mesh/radio.py` | Packet chunking, model directory discovery, interactive menus |

---

## How it works

### Message flow

1. A remote Meshtastic node sends a text message over LoRa
2. The gateway's Meshtastic device receives it and passes it to the Python script via `pubsub`
3. The script puts the message on a work queue (non-blocking)
4. A background worker thread picks it up, builds a prompt with conversation history, and calls OpenVINO to generate a reply
5. The reply is split into ≤200-byte chunks and sent back over LoRa

### Conversation memory

Each remote node (`fromId`) gets its own session with independent history. When the history grows too large to fit in the model's context window, the gateway automatically compresses older turns into a rolling summary using the LLM itself, keeping the last 3 turns verbatim.

### Thinking model support

The streamer filters out `<think>...</think>` blocks in real time so reasoning traces from models like Qwen2.5 or DeepSeek-R1 are never sent over the radio.

---

## Configuration

All settings can be provided via CLI arguments or environment variables (loaded from `.env`).

| CLI argument      | Env variable        | Default         | Description                                      |
|-------------------|---------------------|-----------------|--------------------------------------------------|
| `--port`          | `MESH_PORT`         | auto-detect     | Serial port of the Meshtastic device             |
| `--host`          | `MESH_HOST`         | —               | TCP host for WiFi-connected Meshtastic device    |
| `--model`         | `MESH_MODEL`        | interactive menu| Path to OpenVINO model directory                 |
| `--device`        | `MESH_DEVICE`       | interactive menu| Inference device: `CPU`, `GPU`, `NPU`, `AUTO`    |
| `--reply-mode`    | `MESH_REPLY_MODE`   | `dm`            | See reply modes below                            |
| `--channel-psk`   | `MESH_CHANNEL_PSK`  | `AQ==`          | Channel 0 PSK (informational, see encryption)    |

---

## Reply modes

The `--reply-mode` flag controls where replies go on the mesh network.

### `dm` (default)

Reply is sent directly back to the node that sent the message.

- Only the sender sees the reply
- Encryption is guaranteed to match: if the message arrived, the key already works
- Best for private assistant use

### `broadcast`

Reply is sent to channel 0 (LongFast) and is visible to all nodes with a matching channel key.
The reply is prefixed with `@<sender_node_id>:` so everyone knows who asked.

- All nodes on channel 0 see the exchange
- All devices must share the same channel 0 PSK
- Best for group/public use

```bash
python mesh_llm_gateway.py --reply-mode broadcast
```

---

## Encryption

Meshtastic encrypts every message with the channel's PSK (AES-256) at the **firmware level**. The Python script never handles raw encryption — it only sees already-decrypted plain text.

The `MESH_CHANNEL_PSK` variable is **informational**: it reminds you what key your devices are configured with. To actually apply a key to a device use the Meshtastic CLI:

```bash
# Apply the default public key (factory default — no real privacy)
meshtastic --ch-index 0 --ch-set psk default

# Apply a custom private key
meshtastic --ch-index 0 --ch-set psk base64:YOUR_BASE64_KEY

# Disable encryption entirely
meshtastic --ch-index 0 --ch-set psk none
```

To generate a private key for a closed group:

```bash
openssl rand -base64 32
# paste the result into .env as MESH_CHANNEL_PSK=...
# then apply to every device in your group with the command above
```

**Rule of thumb:**
- `dm` mode → keys already match (message arrived = proof of shared key)
- `broadcast` mode → all devices must be manually configured with the same PSK

---

## Recommended models

Models must be in OpenVINO IR format (`.xml` + `.bin`). Convert from HuggingFace using `optimum-intel`:

```bash
pip install optimum[openvino]

optimum-cli export openvino \
    --model Qwen/Qwen2.5-1.5B-Instruct \
    --weight-format int4 \
    ./qwen2.5-1.5b-int4
```

| Model                    | RAM (int4) | CPU 8-core | NPU Core Ultra | Notes                        |
|--------------------------|-----------|------------|----------------|------------------------------|
| Qwen2.5-1.5B-Instruct    | ~900 MB   | 2–5 s      | 1–3 s          | Recommended default          |
| Qwen2.5-3B-Instruct      | ~1.8 GB   | 5–10 s     | 2–5 s          | Better quality               |
| Phi-3.5-mini-instruct    | ~2.2 GB   | 8–15 s     | not supported  | Best reasoning for size      |

Use `int4` quantization for radio use — smaller and faster with minimal quality loss.

---

## Special commands (from any radio)

| Message  | Effect                                    |
|----------|-------------------------------------------|
| `!reset` | Clears the conversation history for your node |
| `/reset` | Same as above                             |

---

## Changes made

### Initial implementation (`mesh_llm_gateway.py`)
- Receive Meshtastic text messages via `pubsub` subscription
- Non-blocking message queue with background LLM worker thread
- Per-node conversation sessions with rolling summary compression
- Streaming inference with `<think>` block filtering
- Text chunking to respect the 228-byte Meshtastic packet limit (200-byte safe margin)
- Interactive menus for model and device selection when no CLI args given
- NPU support with `MAX_PROMPT_LEN` constraint

### Reply mode (`--reply-mode`)
- Added `dm` mode: reply goes directly to the sender (default)
- Added `broadcast` mode: reply goes to channel 0 (LongFast), prefixed with `@<sender_id>:`
- `!reset` acknowledgement also respects the reply mode
- Chunk size in broadcast mode accounts for the sender prefix length

### ACK confirmation on DM sends
- Each chunk in `dm` mode is sent with `wantAck=True`
- Gateway blocks up to 15 s waiting for the firmware routing ACK before sending the next chunk
- If a chunk is not acknowledged (all 3 firmware retransmissions failed), remaining chunks are dropped and a warning is logged
- Broadcast mode is fire-and-forget (no single recipient to ACK)
- Full LLM response is always collected before any chunk is transmitted

### CLI help (`--help`)
- Full `--help` output with usage examples, radio commands, encryption notes, and env var reference
- All arguments have descriptive help text including their `[env: VAR]` fallback

### Refactoring — feature-based package layout
- Split monolithic `mesh_llm_gateway.py` into a proper Python package under `chat_mesh/`
- `llm/` and `mesh/` are independent sub-packages — either can be extended without touching the other
- `config.py` is the single source of truth for all tunables
- `main.py` is the new entry point; old filename kept as a backwards-compat shim
- `llm/pipeline.py` isolates the OpenVINO dependency — swap it to plug in a different backend

### Environment configuration (`.env` / `.env_dev`)
- Added `python-dotenv` for loading settings from `.env`
- All CLI arguments now fall back to environment variables
- Added `MESH_CHANNEL_PSK` as an informational variable documenting the channel key in use
- `.env_dev` committed as a documented template; `.env` gitignored
- `.gitignore` added covering `.env`, Python cache, and OpenVINO model binaries
