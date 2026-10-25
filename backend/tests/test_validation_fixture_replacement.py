"""The offline provider must replace the requested whitelisted POI, not a slot."""
import ast
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import pytest


def fixture_response():
    # Load only the pure response function. Importing the validation server would
    # initialize LangGraph, FastAPI and fixture globals unnecessarily.
    path = Path(__file__).parents[1] / "scripts/validation_server.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "response")
    namespace = {"json": json, "re": re, "os": os,
                 "AIMessage": lambda **fields: SimpleNamespace(**fields)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["response"]


def prompt(target):
    text = (f"Only replace this current activity fixture:replace={target}\n"
            "本天可选景点（只能从中选择，不得编造）：\n"
            + "".join(f"{i}. poi_id={identity} | Fixture\n" for i, identity in enumerate(("A", "B", "C", "D"), 1))
            + "本天可选餐厅\n")
    return SimpleNamespace(to_messages=lambda: [SimpleNamespace(content=text)])


@pytest.mark.parametrize("target", ["A", "B", "C"])
def test_explicit_fixture_target_replaced_at_any_current_position(target):
    response = fixture_response()(prompt(target))
    selected = [item["poi_id"] for item in json.loads(response.content)["attractions"]]
    assert target not in selected
    assert len(selected) == len(set(selected)) == 3
    assert set(selected) == ({"A", "B", "C"} - {target}) | {"D"}
    assert selected.index("D") == ["A", "B", "C"].index(target)


def test_explicit_fixture_target_outside_whitelist_is_rejected():
    with pytest.raises(ValueError, match="whitelist"):
        fixture_response()(prompt("not-in-candidates"))
