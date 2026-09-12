import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


PROBE = Path(__file__).with_name("optimized_validation_probe.py")
RUNTIME_SOURCE = Path(__file__).parents[2] / "src" / "moneywiz_api"


def run_probe(*options, optimize_environment=None):
    environment = os.environ.copy()
    environment.pop("PYTHONOPTIMIZE", None)
    source_path = str(RUNTIME_SOURCE.parent)
    existing_pythonpath = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{source_path}{os.pathsep}{existing_pythonpath}"
        if existing_pythonpath
        else source_path
    )
    if optimize_environment is not None:
        environment["PYTHONOPTIMIZE"] = optimize_environment
    completed = subprocess.run(
        [sys.executable, *options, str(PROBE)],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    return json.loads(completed.stdout)


@pytest.fixture(scope="module")
def optimization_results():
    return [
        run_probe(),
        run_probe("-O"),
        run_probe("-OO"),
        run_probe(optimize_environment="1"),
    ]


def test_validation_results_are_optimization_invariant(optimization_results) -> None:
    assert [result["optimize"] for result in optimization_results] == [0, 1, 2, 1]
    baseline = optimization_results[0]["cases"]
    assert all(result["cases"] == baseline for result in optimization_results[1:])


def test_probe_covers_success_error_and_completeness_paths(
    optimization_results,
) -> None:
    cases = optimization_results[0]["cases"]

    assert cases["decimal_valid"] == {"status": "ok", "value": "1.25"}
    assert cases["decimal_string"] == {
        "status": "error",
        "type": "AssertionError",
        "message": "decimal field must be numeric",
    }
    assert cases["date_string"]["type"] == "AssertionError"
    assert cases["record_invalid_identity"]["type"] == "AssertionError"
    assert cases["account_nullable_info"]["status"] == "ok"
    assert cases["account_invalid_name_public"]["type"] == "AssertionError"

    named_entities = cases["named_entities"]
    assert all(
        named_entities[name]["status"] == "ok"
        for name in ("payee_valid", "tag_valid", "category_valid")
    )
    assert all(
        named_entities[name]["type"] == "AssertionError"
        for name in ("payee_invalid", "tag_invalid", "category_invalid")
    )
    assert cases["manager"]["parsed_count"] == 1
    assert cases["manager"]["skipped"][0]["error"] == "validation"
    assert cases["holding_missing_quantity"]["type"] == "AssertionError"
    assert cases["deposit_invalid_sign"]["type"] == "AssertionError"
    assert cases["deposit_invalid_fx"]["type"] == "AssertionError"
    assert cases["deposit_zero_rate"] == {"status": "ok", "value": None}
    assert cases["investment_buy_invalid_quantity"]["type"] == "AssertionError"
    assert cases["investment_buy_invalid_total"]["type"] == "AssertionError"
    assert cases["transfer_zero_rate"]["type"] == "ValueError"

    relationships = cases["relationships"]
    assert relationships["category_report"]["parsed_count"] == 1
    assert [item["error"] for item in relationships["category_report"]["skipped"]] == [
        "validation",
        "validation",
    ]
    assert relationships["refund_report"]["parsed_count"] == 1
    assert relationships["tag_report"]["parsed_count"] == 1
    assert not relationships["aggregate"]["complete"]

    serialized = json.dumps(cases["sentinel"], sort_keys=True)
    assert "PRIVATE_PAYLOAD_SERIALIZED" not in serialized
    assert all(
        outcome["type"] == "AssertionError" for outcome in cases["sentinel"].values()
    )
    assert "PRIVATE_PAYLOAD" not in json.dumps(cases, sort_keys=True)


def test_runtime_sources_contain_no_active_assert_statements() -> None:
    active_asserts = []
    dynamic_validation_messages = []
    for source_path in sorted(RUNTIME_SOURCE.rglob("*.py")):
        tree = ast.parse(source_path.read_text(), filename=str(source_path))
        active_asserts.extend(
            (source_path.relative_to(RUNTIME_SOURCE), node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Assert)
        )
        dynamic_validation_messages.extend(
            (source_path.relative_to(RUNTIME_SOURCE), node.lineno)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "require_valid"
            and (
                len(node.args) < 2
                or not isinstance(node.args[1], ast.Constant)
                or not isinstance(node.args[1].value, str)
            )
        )

    assert active_asserts == []
    assert dynamic_validation_messages == []
