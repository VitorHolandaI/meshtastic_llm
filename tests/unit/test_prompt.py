"""
Tests for strip_think, build_prompt, and collect_streamer.
These run with no LLM and no radio — pure Python logic.
"""

import pytest
from chat_mesh.llm.prompt import strip_think, build_prompt, collect_streamer
from chat_mesh.config import SYSTEM_PROMPT


# ── strip_think ───────────────────────────────────────────────────────────────

def test_strip_think_removes_block():
    text = "<think>internal reasoning</think>Final answer"
    assert strip_think(text) == "Final answer"


def test_strip_think_no_block_unchanged():
    text = "Just a plain response"
    assert strip_think(text) == text


def test_strip_think_multiline_block():
    text = "<think>\nline one\nline two\n</think>Answer here"
    assert strip_think(text) == "Answer here"


def test_strip_think_multiple_blocks():
    text = "<think>first</think>middle<think>second</think>end"
    assert strip_think(text) == "middleend"


def test_strip_think_empty_block():
    text = "<think></think>response"
    assert strip_think(text) == "response"


def test_strip_think_empty_string():
    assert strip_think("") == ""


def test_strip_think_only_think_block():
    text = "<think>nothing to show</think>"
    assert strip_think(text) == ""


def test_strip_think_strips_surrounding_whitespace():
    text = "  <think>x</think>  answer  "
    assert strip_think(text) == "answer"


# ── build_prompt ──────────────────────────────────────────────────────────────

def test_build_prompt_starts_with_system_prompt():
    result = build_prompt([], "", "hello")
    assert result.startswith(SYSTEM_PROMPT)


def test_build_prompt_ends_with_assistant_tag():
    result = build_prompt([], "", "hello")
    assert result.strip().endswith("Assistant:")


def test_build_prompt_includes_user_input():
    result = build_prompt([], "", "what is 2+2?")
    assert "what is 2+2?" in result


def test_build_prompt_includes_history():
    history = [("user", "hi"), ("assistant", "hello")]
    result = build_prompt(history, "", "how are you?")
    assert "User: hi" in result
    assert "Assistant: hello" in result


def test_build_prompt_includes_summary_when_present():
    result = build_prompt([], "We talked about math.", "continue")
    assert "We talked about math." in result


def test_build_prompt_no_summary_section_when_empty():
    result = build_prompt([], "", "hello")
    assert "Conversation summary" not in result


def test_build_prompt_history_order():
    history = [
        ("user",      "first"),
        ("assistant", "second"),
        ("user",      "third"),
        ("assistant", "fourth"),
    ]
    result = build_prompt(history, "", "fifth")
    assert result.index("first") < result.index("second")
    assert result.index("second") < result.index("third")
    assert result.index("third") < result.index("fourth")
    assert result.index("fourth") < result.index("fifth")


def test_build_prompt_empty_history():
    result = build_prompt([], "", "hello")
    # Should not raise and should still produce a valid prompt
    assert "User: hello" in result


# ── collect_streamer ──────────────────────────────────────────────────────────

def test_collect_streamer_collects_tokens():
    collector = []
    streamer  = collect_streamer(collector)
    for token in ["Hello", " ", "world"]:
        streamer(token)
    assert collector == ["Hello", " ", "world"]


def test_collect_streamer_returns_false():
    collector = []
    streamer  = collect_streamer(collector)
    assert streamer("token") is False


def test_collect_streamer_filters_think_tokens():
    collector = []
    streamer  = collect_streamer(collector)
    # Simulate tokens that form a <think> block then a real answer
    for token in ["<think>", "reasoning", "</think>", "answer"]:
        streamer(token)
    # collector still has all raw tokens (streamer collects everything)
    # but joining and calling strip_think gives the clean result
    from chat_mesh.llm.prompt import strip_think
    assert strip_think("".join(collector)) == "answer"


def test_collect_streamer_multiple_calls_accumulate():
    collector = []
    streamer  = collect_streamer(collector)
    streamer("a")
    streamer("b")
    streamer("c")
    assert "".join(collector) == "abc"


def test_collect_streamer_empty_token():
    collector = []
    streamer  = collect_streamer(collector)
    streamer("")
    assert collector == [""]


def test_collect_streamer_think_close_with_trailing_text():
    # Covers the buf.append(after) branch — text after </think> in the same token
    collector = []
    streamer  = collect_streamer(collector)
    streamer("<think>")
    streamer("reasoning</think>visible text")
    assert "visible text" in strip_think("".join(collector))


# ── compress_history ──────────────────────────────────────────────────────────

def test_compress_history_calls_pipe_generate():
    from unittest.mock import MagicMock
    from chat_mesh.llm.prompt import compress_history

    pipe = MagicMock()
    def fake_generate(prompt, max_new_tokens=None, streamer=None, **kwargs):
        if streamer:
            streamer("brief summary")
    pipe.generate.side_effect = fake_generate

    history = [("user", "a"), ("assistant", "b"), ("user", "c"),
               ("assistant", "d"), ("user", "e"), ("assistant", "f")]
    summary, kept = compress_history(pipe, history, "")

    assert pipe.generate.called
    assert isinstance(summary, str)
    assert len(kept) <= 3   # COMPRESS_KEEP


def test_compress_history_returns_kept_turns():
    from unittest.mock import MagicMock
    from chat_mesh.llm.prompt import compress_history

    pipe = MagicMock()
    pipe.generate.side_effect = lambda *a, **kw: None

    history = [("user", "old1"), ("assistant", "old2"),
               ("user", "keep1"), ("assistant", "keep2"), ("user", "keep3")]
    _, kept = compress_history(pipe, history, "")
    # COMPRESS_KEEP=3 → last 3 turns are kept verbatim
    assert kept == [("user", "keep1"), ("assistant", "keep2"), ("user", "keep3")]


def test_compress_history_with_existing_summary():
    from unittest.mock import MagicMock
    from chat_mesh.llm.prompt import compress_history

    captured = {}
    def fake_generate(prompt, max_new_tokens=None, streamer=None, **kwargs):
        captured["prompt"] = prompt
        if streamer:
            streamer("new summary")
    pipe = MagicMock()
    pipe.generate.side_effect = fake_generate

    history = [("user", "a"), ("assistant", "b"), ("user", "c"),
               ("assistant", "d"), ("user", "e"), ("assistant", "f")]
    compress_history(pipe, history, "prior summary")
    assert "prior summary" in captured["prompt"]
