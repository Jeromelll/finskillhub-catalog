#!/usr/bin/env python3
"""Evaluate an incremental business sizing spec using a small, safe expression engine."""

from __future__ import annotations

import argparse
import ast
import json
import math
import re
import sys
from pathlib import Path
from typing import Any, Optional


SPEC_SCHEMA = "incremental-sizing-spec/v1"
RESULT_SCHEMA = "incremental-sizing-results/v1"
ROLES = {"input", "calculation", "reported", "section", "note", "benchmark"}
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
FUNCTIONS = {"min": min, "max": max, "abs": abs}


class SpecError(ValueError):
    """A structural or expression error that makes the spec unsafe to evaluate."""


class Expression:
    def __init__(self, source: str, row_ids: set[str], row_id: str):
        self.source = source
        self.row_id = row_id
        try:
            self.tree = ast.parse(source, mode="eval")
        except SyntaxError as exc:
            raise SpecError(f"row {row_id}: invalid expression syntax: {exc.msg}") from exc
        self.dependencies: set[str] = set()
        self._validate(self.tree.body, row_ids)

    def _validate(self, node: ast.AST, row_ids: set[str]) -> None:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
                raise SpecError(f"row {self.row_id}: only numeric constants are allowed")
            try:
                finite = math.isfinite(float(node.value))
            except OverflowError:
                finite = False
            if not finite:
                raise SpecError(f"row {self.row_id}: constants must be finite")
        elif isinstance(node, ast.Name):
            if node.id in FUNCTIONS:
                return
            if node.id not in row_ids:
                raise SpecError(f"row {self.row_id}: unknown dependency {node.id!r}")
            self.dependencies.add(node.id)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            self._validate(node.left, row_ids)
            self._validate(node.right, row_ids)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            self._validate(node.operand, row_ids)
        elif isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in FUNCTIONS:
                raise SpecError(f"row {self.row_id}: only min, max, and abs calls are allowed")
            if node.keywords:
                raise SpecError(f"row {self.row_id}: keyword arguments are not allowed")
            if node.func.id == "abs" and len(node.args) != 1:
                raise SpecError(f"row {self.row_id}: abs requires one argument")
            if node.func.id in {"min", "max"} and not node.args:
                raise SpecError(f"row {self.row_id}: {node.func.id} requires arguments")
            for arg in node.args:
                self._validate(arg, row_ids)
        else:
            raise SpecError(f"row {self.row_id}: unsafe expression element {type(node).__name__}")

    def evaluate(self, values: dict[str, float]) -> float:
        def visit(node: ast.AST) -> float:
            if isinstance(node, ast.Constant):
                return float(node.value)
            if isinstance(node, ast.Name):
                if node.id in FUNCTIONS:
                    raise SpecError(f"row {self.row_id}: function name used without a call")
                return values[node.id]
            if isinstance(node, ast.UnaryOp):
                val = visit(node.operand)
                return val if isinstance(node.op, ast.UAdd) else -val
            if isinstance(node, ast.BinOp):
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Add):
                    return left + right
                if isinstance(node.op, ast.Sub):
                    return left - right
                if isinstance(node.op, ast.Mult):
                    return left * right
                if right == 0:
                    raise ZeroDivisionError("division by zero")
                return left / right
            if isinstance(node, ast.Call):
                args = [visit(arg) for arg in node.args]
                return float(FUNCTIONS[node.func.id](*args))  # type: ignore[union-attr]
            raise SpecError(f"row {self.row_id}: internal unsupported expression")

        result = visit(self.tree.body)
        if not math.isfinite(float(result)):
            raise ArithmeticError("calculation produced a non-finite value")
        return float(result)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SpecError(message)


def _finite_number(value: Any, where: str) -> Optional[float]:
    if value is None:
        return None
    _require(isinstance(value, (int, float)) and not isinstance(value, bool), f"{where} must be a number or null")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise SpecError(f"{where} must be finite") from exc
    _require(math.isfinite(number), f"{where} must be finite")
    return number


def _scenarios(spec: dict[str, Any]) -> list[dict[str, str]]:
    scenarios = spec.get("scenarios")
    _require(isinstance(scenarios, list) and bool(scenarios), "scenarios must be a non-empty array")
    seen: set[str] = set()
    out = []
    for i, scenario in enumerate(scenarios):
        _require(isinstance(scenario, dict), f"scenarios[{i}] must be an object")
        sid, label = scenario.get("id"), scenario.get("label")
        _require(isinstance(sid, str) and IDENT_RE.fullmatch(sid) is not None, f"scenarios[{i}].id must be an identifier")
        _require(sid not in seen, f"duplicate scenario id {sid!r}")
        _require(isinstance(label, str), f"scenarios[{i}].label must be text")
        seen.add(sid)
        out.append({"id": sid, "label": label})
    return out


def _rows(spec: dict[str, Any], scenario_ids: set[str]) -> tuple[list[dict[str, Any]], dict[str, Expression]]:
    rows = spec.get("rows")
    _require(isinstance(rows, list), "rows must be an array")
    ids: set[str] = set()
    for i, row in enumerate(rows):
        _require(isinstance(row, dict), f"rows[{i}] must be an object")
        rid = row.get("id")
        _require(isinstance(rid, str) and IDENT_RE.fullmatch(rid) is not None, f"rows[{i}].id must be an identifier")
        _require(rid not in FUNCTIONS, f"rows[{i}].id is reserved for a built-in function")
        _require(rid not in ids, f"duplicate row id {rid!r}")
        ids.add(rid)
        _require(row.get("role") in ROLES, f"row {rid}: role must be one of {sorted(ROLES)}")
        for field in ("label", "unit", "format"):
            _require(isinstance(row.get(field), str), f"row {rid}: {field} must be text")
        if "min" in row:
            row["min"] = _finite_number(row["min"], f"row {rid}.min")
        if "max" in row:
            row["max"] = _finite_number(row["max"], f"row {rid}.max")
        _require(row.get("min") is None or row.get("max") is None or row["min"] <= row["max"], f"row {rid}: min exceeds max")
        _require(isinstance(row.get("indent", 0), int) and not isinstance(row.get("indent", 0), bool) and row.get("indent", 0) >= 0,
                 f"row {rid}: indent must be a non-negative integer")
    expressions: dict[str, Expression] = {}
    for row in rows:
        if row["role"] == "calculation":
            _require(isinstance(row.get("expression"), str) and bool(row["expression"].strip()), f"row {row['id']}: calculation requires expression")
            expressions[row["id"]] = Expression(row["expression"], ids, row["id"])
        if row["role"] in {"input", "benchmark", "reported"}:
            _validate_value_fields(row, scenario_ids)
    _check_cycles(expressions)
    return rows, expressions


def _validate_value_fields(row: dict[str, Any], scenario_ids: set[str]) -> None:
    rid = row["id"]
    if "value" in row:
        _finite_number(row["value"], f"row {rid}.value")
    if "values" in row:
        _require(isinstance(row["values"], dict), f"row {rid}.values must be an object")
        unknown = set(row["values"]) - scenario_ids
        _require(not unknown, f"row {rid}: values has unknown scenarios {sorted(unknown)}")
        for sid, val in row["values"].items():
            _finite_number(val, f"row {rid}.values.{sid}")
    _require("value" in row or "values" in row, f"row {rid}: requires value or values")


def _check_cycles(expressions: dict[str, Expression]) -> None:
    visiting: list[str] = []
    done: set[str] = set()

    def visit(rid: str) -> None:
        if rid in done:
            return
        if rid in visiting:
            cycle = visiting[visiting.index(rid):] + [rid]
            raise SpecError("calculation dependency cycle: " + " -> ".join(cycle))
        visiting.append(rid)
        for dep in expressions[rid].dependencies:
            if dep in expressions:
                visit(dep)
        visiting.pop()
        done.add(rid)

    for rid in expressions:
        visit(rid)


def _row_value(row: dict[str, Any], sid: str) -> float | None:
    values = row.get("values", {})
    return values[sid] if sid in values else row.get("value")


def _check_bounds(row: dict[str, Any], value: float, sid: str, diagnostics: list[str]) -> float | None:
    if row.get("min") is not None and value < row["min"]:
        diagnostics.append(f"row {row['id']} scenario {sid}: value {value} is below min {row['min']}")
        return None
    if row.get("max") is not None and value > row["max"]:
        diagnostics.append(f"row {row['id']} scenario {sid}: value {value} is above max {row['max']}")
        return None
    return value


def _evaluate_case(rows: list[dict[str, Any]], expressions: dict[str, Expression], sid: str,
                   overrides: Optional[dict[str, Optional[float]]] = None) -> tuple[dict[str, Optional[float]], dict[str, str], list[str]]:
    overrides = overrides or {}
    by_id = {row["id"]: row for row in rows}
    values: dict[str, Optional[float]] = {}
    statuses: dict[str, str] = {}
    diagnostics: list[str] = []
    for row in rows:
        rid, role = row["id"], row["role"]
        if role in {"input", "benchmark", "reported"}:
            value = overrides[rid] if rid in overrides else _row_value(row, sid)
            if value is None:
                values[rid] = None
                statuses[rid] = "missing"
                continue
            value = _check_bounds(row, float(value), sid, diagnostics)
            values[rid] = value
            statuses[rid] = ("reported" if role == "reported" else "input") if value is not None else "blocked"
        elif role in {"section", "note"}:
            values[rid] = None
            statuses[rid] = "missing"
    pending = set(expressions)
    while pending:
        progress = False
        for rid in list(pending):
            expr = expressions[rid]
            if any(dep in pending for dep in expr.dependencies):
                continue
            row = by_id[rid]
            if any(values.get(dep) is None for dep in expr.dependencies):
                values[rid] = None
                statuses[rid] = "blocked"
                diagnostics.append(f"row {rid} scenario {sid}: blocked by missing or blocked dependency")
            else:
                resolved = {dep: values[dep] for dep in expr.dependencies}
                try:
                    value = expr.evaluate(resolved)  # type: ignore[arg-type]
                    value = _check_bounds(row, value, sid, diagnostics)
                    values[rid] = value
                    statuses[rid] = "computed" if value is not None else "blocked"
                except ZeroDivisionError:
                    values[rid] = None
                    statuses[rid] = "blocked"
                    diagnostics.append(f"row {rid} scenario {sid}: blocked by division by zero")
                except (ArithmeticError, OverflowError) as exc:
                    values[rid] = None
                    statuses[rid] = "blocked"
                    diagnostics.append(f"row {rid} scenario {sid}: blocked: {exc}")
            pending.remove(rid)
            progress = True
            break
        if not progress:
            raise SpecError("calculation dependency cycle")
    return values, statuses, diagnostics


def evaluate_spec(spec: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(spec, dict), "spec must be a JSON object")
    _require(spec.get("schema_version") == SPEC_SCHEMA, f"schema_version must be {SPEC_SCHEMA!r}")
    for field in ("title", "period", "currency"):
        _require(isinstance(spec.get(field), str), f"{field} must be text")
    scenarios = _scenarios(spec)
    scenario_ids = {item["id"] for item in scenarios}
    rows, expressions = _rows(spec, scenario_ids)
    diagnostics: list[str] = []
    row_results: dict[str, dict[str, Any]] = {row["id"]: {"values": {}, "status": {}} for row in rows}
    cases: dict[str, tuple[dict[str, float | None], dict[str, str]]] = {}
    for scenario in scenarios:
        sid = scenario["id"]
        values, statuses, case_diagnostics = _evaluate_case(rows, expressions, sid)
        diagnostics.extend(case_diagnostics)
        cases[sid] = (values, statuses)
        for rid in row_results:
            row_results[rid]["values"][sid] = values.get(rid)
            row_results[rid]["status"][sid] = statuses.get(rid, "missing")

    out_rows = []
    for row in rows:
        enriched = dict(row)
        enriched["values"] = row_results[row["id"]]["values"]
        enriched["status"] = row_results[row["id"]]["status"]
        out_rows.append(enriched)

    sens_out = []
    sensitivities = spec.get("sensitivities", [])
    _require(isinstance(sensitivities, list), "sensitivities must be an array")
    by_id = {row["id"]: row for row in rows}
    for i, sensitivity in enumerate(sensitivities):
        _require(isinstance(sensitivity, dict), f"sensitivities[{i}] must be an object")
        rid = sensitivity.get("id", f"sensitivity_{i + 1}")
        sid = sensitivity.get("scenario_id")
        _require(sid in scenario_ids, f"sensitivity {rid}: unknown scenario_id")
        row_axis, col_axis = sensitivity.get("row_axis"), sensitivity.get("column_axis")
        _require(isinstance(row_axis, dict) and isinstance(col_axis, dict), f"sensitivity {rid}: row_axis and column_axis are required")
        axes = []
        for axis_name, axis in (("row_axis", row_axis), ("column_axis", col_axis)):
            axis_row_id = axis.get("row_id")
            _require(axis_row_id in by_id, f"sensitivity {rid}: {axis_name} references unknown row")
            _require(by_id[axis_row_id]["role"] in {"input", "benchmark"}, f"sensitivity {rid}: axes must vary input or benchmark rows")
            _require(isinstance(axis.get("label"), str) and isinstance(axis.get("unit"), str), f"sensitivity {rid}: axis label and unit must be text")
            axis_values = axis.get("values")
            _require(isinstance(axis_values, list) and bool(axis_values), f"sensitivity {rid}: axis values must be a non-empty array")
            nums = [_finite_number(v, f"sensitivity {rid}.{axis_name}.values") for v in axis_values]
            _require(all(number is not None for number in nums), f"sensitivity {rid}: axis values cannot be null")
            axes.append((axis_row_id, nums))
        _require(axes[0][0] != axes[1][0], f"sensitivity {rid}: axes must use two different input rows")
        output_id = sensitivity.get("output_id")
        _require(output_id in by_id, f"sensitivity {rid}: unknown output_id")
        matrix: list[list[Optional[float]]] = []
        for row_value in axes[0][1]:
            matrix_row: list[Optional[float]] = []
            for col_value in axes[1][1]:
                overrides = {axes[0][0]: row_value, axes[1][0]: col_value}
                vals, _, diag = _evaluate_case(rows, expressions, sid, overrides)
                diagnostics.extend(f"sensitivity {rid}: {message}" for message in diag)
                matrix_row.append(vals.get(output_id))
            matrix.append(matrix_row)
        sens_out.append({
            **sensitivity,
            "row_axis": dict(row_axis),
            "column_axis": dict(col_axis),
            "matrix": matrix,
        })

    footnotes = spec.get("footnotes", [])
    sources = spec.get("sources", [])
    _require(isinstance(footnotes, list) and all(isinstance(item, str) for item in footnotes), "footnotes must be an array of text")
    _require(isinstance(sources, list) and all(isinstance(item, dict) for item in sources), "sources must be an array of objects")
    for field in ("diagnostics",):
        if field in spec:
            _require(isinstance(spec[field], list) and all(isinstance(item, str) for item in spec[field]), f"{field} must be an array of text")
    diagnostics.extend(spec.get("diagnostics", []))

    recs = spec.get("report_reconciliations", [])
    _require(isinstance(recs, list), "report_reconciliations must be an array")
    for i, rec in enumerate(recs):
        _require(isinstance(rec, dict), f"report_reconciliations[{i}] must be an object")
        target = rec.get("row_id")
        _require(target in by_id, f"report_reconciliations[{i}]: unknown row_id")
        report_values = rec.get("reported_values")
        _require(isinstance(report_values, dict), f"report_reconciliations[{i}].reported_values must be an object")
        tolerance = _finite_number(rec.get("tolerance_abs", 0), f"report_reconciliations[{i}].tolerance_abs")
        _require(tolerance is not None and tolerance >= 0, f"report_reconciliations[{i}].tolerance_abs must be non-negative")
        for sid, reported in report_values.items():
            _require(sid in scenario_ids, f"report_reconciliations[{i}]: unknown scenario {sid}")
            rep = _finite_number(reported, f"report_reconciliations[{i}].reported_values.{sid}")
            calculated = cases[sid][0].get(target)
            if rep is not None and calculated is not None and abs(calculated - rep) > tolerance:
                diagnostics.append(f"reconciliation {rec.get('label', target)} scenario {sid}: calculated {calculated} differs from reported {rep} by more than {tolerance}")

    required = spec.get("required_results", [])
    _require(isinstance(required, list) and all(isinstance(item, str) for item in required), "required_results must be an array of row ids")
    for rid in required:
        _require(rid in by_id, f"required_results references unknown row {rid!r}")
    incomplete = []
    for rid in required:
        for scenario in scenarios:
            sid = scenario["id"]
            if cases[sid][0].get(rid) is None:
                incomplete.append(f"required result {rid} is unavailable in scenario {sid}")
    diagnostics.extend(incomplete)
    return {
        "schema_version": RESULT_SCHEMA,
        "title": spec["title"],
        "period": spec["period"],
        "currency": spec["currency"],
        "scenarios": scenarios,
        "rows": out_rows,
        "sensitivities": sens_out,
        "footnotes": footnotes,
        "sources": sources,
        "diagnostics": diagnostics,
        "status": "incomplete" if incomplete else "complete",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="path to spec JSON")
    parser.add_argument("--out", required=True, help="path for results JSON")
    args = parser.parse_args(argv)
    try:
        spec = json.loads(Path(args.input).read_text(encoding="utf-8"))
        result = evaluate_spec(spec)
        destination = Path(args.out)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    except (OSError, json.JSONDecodeError, SpecError) as exc:
        print(f"size_opportunity: {exc}", file=sys.stderr)
        return 2
    print(f"wrote {args.out} ({result['status']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
