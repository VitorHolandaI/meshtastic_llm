"""
Tests for find_models and choose in mesh/radio.py.
"""

import pytest
from unittest.mock import patch
from chat_mesh.mesh.radio import find_models, choose


# ── find_models ───────────────────────────────────────────────────────────────

def test_find_models_returns_dir_with_xml(tmp_path):
    model_dir = tmp_path / "mymodel"
    model_dir.mkdir()
    (model_dir / "model.xml").write_text("")
    result = find_models(str(tmp_path))
    assert str(model_dir) in result


def test_find_models_ignores_dir_without_xml(tmp_path):
    other_dir = tmp_path / "notamodel"
    other_dir.mkdir()
    (other_dir / "weights.bin").write_text("")
    result = find_models(str(tmp_path))
    assert str(other_dir) not in result


def test_find_models_returns_empty_list_when_none(tmp_path):
    result = find_models(str(tmp_path))
    assert result == []


def test_find_models_ignores_hidden_dirs(tmp_path):
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "model.xml").write_text("")
    result = find_models(str(tmp_path))
    assert str(hidden) not in result


def test_find_models_finds_nested_model(tmp_path):
    nested = tmp_path / "models" / "v2"
    nested.mkdir(parents=True)
    (nested / "model.xml").write_text("")
    result = find_models(str(tmp_path))
    assert str(nested) in result


def test_find_models_result_is_sorted(tmp_path):
    for name in ["zzz", "aaa", "mmm"]:
        d = tmp_path / name
        d.mkdir()
        (d / "model.xml").write_text("")
    result = find_models(str(tmp_path))
    assert result == sorted(result)


# ── choose ────────────────────────────────────────────────────────────────────

def test_choose_valid_selection():
    with patch("builtins.input", return_value="2"):
        result = choose("Pick one:", ["alpha", "beta", "gamma"])
    assert result == "beta"


def test_choose_first_option():
    with patch("builtins.input", return_value="1"):
        result = choose("Pick one:", ["only"])
    assert result == "only"


def test_choose_last_option():
    with patch("builtins.input", return_value="3"):
        result = choose("Pick one:", ["a", "b", "c"])
    assert result == "c"


def test_choose_invalid_then_valid(capsys):
    inputs = iter(["99", "abc", "2"])
    with patch("builtins.input", side_effect=inputs):
        result = choose("Pick one:", ["x", "y"])
    assert result == "y"


def test_choose_custom_path():
    inputs = iter(["0", "/my/custom/path"])
    with patch("builtins.input", side_effect=inputs):
        result = choose("Pick one:", ["a", "b"], allow_custom=True)
    assert result == "/my/custom/path"


def test_choose_allow_custom_shows_zero_option(capsys):
    with patch("builtins.input", return_value="1"):
        choose("Pick one:", ["a"], allow_custom=True)
    captured = capsys.readouterr()
    assert "[0]" in captured.out
