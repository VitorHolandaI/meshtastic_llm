"""
Prompt building, history compression, and token streaming helpers.
"""

import re

from chat_mesh.config import SYSTEM_PROMPT, COMPRESS_KEEP


def strip_think(text: str) -> str:
    """Remove <think>…</think> reasoning blocks from model output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def build_prompt(history: list, summary: str, user_input: str) -> str:
    """Assemble a plain-text prompt from session history and the new user message."""
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
    """
    Returns a streamer callback for openvino_genai that:
    - appends every token to *collector*
    - suppresses <think>…</think> blocks from being printed
    """
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
    """
    Summarise old turns with the LLM and return (new_summary, kept_turns).
    Keeps the last COMPRESS_KEEP turns verbatim; everything older is summarised.
    """
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
