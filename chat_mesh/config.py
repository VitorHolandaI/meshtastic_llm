"""
Global constants and tunables.
All runtime config (ports, paths, keys) lives in .env — this file is for
behaviour knobs that don't change between deployments.
"""

# ── radio ─────────────────────────────────────────────────────────────────────
MESH_MAX_CHUNK = 200    # bytes per Meshtastic packet (safe margin under 228)
CHUNK_DELAY    = 0.8    # seconds between chunks to avoid flooding the channel
ACK_TIMEOUT    = 15     # seconds to wait for ACK (covers 3 firmware retransmissions)

# ── llm ───────────────────────────────────────────────────────────────────────
COMPRESS_KEEP   = 3     # recent turns kept verbatim after history compression
CHARS_PER_TOKEN = 4     # rough chars-per-token estimate for context budgeting

SYSTEM_PROMPT = (
    "You are a helpful AI assistant running on a Meshtastic LoRa radio gateway. "
    "Keep answers short and concise — messages are limited in length. "
    "Respond in the same language as the user."
)
