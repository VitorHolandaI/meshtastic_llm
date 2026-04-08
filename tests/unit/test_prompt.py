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
