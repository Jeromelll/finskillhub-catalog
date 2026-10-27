#!/usr/bin/env python3
"""Render source-backed incremental-sizing exhibits from a versioned JSON input.

Outputs a reader-facing HTML exhibit, a portable XML table fragment, an XLSX
workbook with formula/audit sheets, and static PNG tables. Input values are
never rescaled. Missing or blocked values render as an ASCII hyphen.
"""
from __future__ import annotations

import argparse
import ast
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import html
import json
import math
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "incremental-sizing-results/v1"
ROLES = {"input", "calculation", "reported", "section", "note", "benchmark"}
STATUSES = {"input", "computed", "reported", "missing", "blocked"}
FORMATS = re.compile(r"^(integer|decimal:\d+|percent:\d+|money:\d+|eps:\d+)$")
INK = "#1A1A1A"
MUTED = "#595959"
BLUE = "#0000FF"
RED = "#FF0000"
BLACK = "#000000"
WHITE = "#FFFFFF"
SECTION = "#E7E6E6"
KEY = "#FFF2CC"
GRID = "#9E9E9E"
FILL_BLUE = "#EAF2F8"
UNIT_LABELS = {
    "USD_mn": "百万美元", "USD_bn": "十亿美元", "users_mn": "百万人",
    "queries_mn": "百万次查询", "ratio": "%", "USD/share": "美元/股",
    "USD_mn/year": "百万美元/年", "USD_mn/yearly": "百万美元/年",
    "USD_mn/MW": "百万美元/MW", "USD_mn/MW_year": "百万美元/MW·年", "USD_mn/MW-year": "百万美元/MW·年",
    "shares_mn": "百万股", "USD/query": "美元/次查询",
    "queries/user/day": "次/人/天", "MW": "MW", "days/year": "天/年",
}


class InputError(ValueError):
    pass


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v))


def validate(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not isinstance(data, dict):
        raise InputError("top-level JSON must be an object")
    if data.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION!r}")
    for key in ("title", "period", "currency"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            errors.append(f"{key} must be a non-empty string")
    scenarios = data.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        errors.append("scenarios must be a non-empty array")
        scenarios = []
    scenario_ids: list[str] = []
    for i, s in enumerate(scenarios):
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not s["id"]:
            errors.append(f"scenarios[{i}].id must be a non-empty string")
            continue
        scenario_ids.append(s["id"])
        if not isinstance(s.get("label"), str) or not s["label"]:
            errors.append(f"scenarios[{i}].label must be a non-empty string")
    if len(scenario_ids) != len(set(scenario_ids)):
        errors.append("scenario ids must be unique")
    rows = data.get("rows")
    if not isinstance(rows, list) or not rows:
        errors.append("rows must be a non-empty array")
        rows = []
    row_ids: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"rows[{i}] must be an object")
            continue
        rid = row.get("id")
        if not isinstance(rid, str) or not rid:
            errors.append(f"rows[{i}].id must be a non-empty string")
        else:
            row_ids.append(rid)
        for key in ("label", "unit", "format", "role"):
            if not isinstance(row.get(key), str):
                errors.append(f"rows[{i}].{key} must be a string")
        if row.get("role") not in ROLES:
            errors.append(f"rows[{i}].role must be one of {sorted(ROLES)}")
        fmt = row.get("format", "")
        if not isinstance(fmt, str) or not FORMATS.match(fmt):
            errors.append(f"rows[{i}].format is invalid: {fmt!r}")
        if not isinstance(row.get("indent", 0), int) or row.get("indent", 0) < 0:
            errors.append(f"rows[{i}].indent must be a non-negative integer")
        values = row.get("values", {})
        statuses = row.get("status", {})
        if not isinstance(values, dict):
            errors.append(f"rows[{i}].values must be an object")
            values = {}
        if not isinstance(statuses, dict):
            errors.append(f"rows[{i}].status must be an object")
            statuses = {}
        for sid in scenario_ids:
            value = values.get(sid)
            status = statuses.get(sid, "missing" if value is None else "input")
            if value is not None and not _is_number(value):
                errors.append(f"rows[{i}].values[{sid!r}] must be a finite number or null")
            if status not in STATUSES:
                errors.append(f"rows[{i}].status[{sid!r}] must be one of {sorted(STATUSES)}")
        if row.get("expression") is not None and not isinstance(row.get("expression"), str):
            errors.append(f"rows[{i}].expression must be a string when present")
        if row.get("unit_label") is not None and not isinstance(row.get("unit_label"), str):
            errors.append(f"rows[{i}].unit_label must be a string when present")
    if len(row_ids) != len(set(row_ids)):
        errors.append("row ids must be unique")
    known_scenarios = set(scenario_ids)
    for i, sens in enumerate(data.get("sensitivities", []) or []):
        if not isinstance(sens, dict):
            errors.append(f"sensitivities[{i}] must be an object")
            continue
        for key in ("id", "title", "output_unit", "format"):
            if not isinstance(sens.get(key), str):
                errors.append(f"sensitivities[{i}].{key} must be a string")
        if not isinstance(sens.get("format", ""), str) or not FORMATS.match(sens.get("format", "")):
            errors.append(f"sensitivities[{i}].format is invalid")
        if sens.get("output_unit_label") is not None and not isinstance(sens.get("output_unit_label"), str):
            errors.append(f"sensitivities[{i}].output_unit_label must be a string")
        for axis_name in ("row_axis", "column_axis"):
            axis = sens.get(axis_name)
            if not isinstance(axis, dict) or not isinstance(axis.get("values"), list):
                errors.append(f"sensitivities[{i}].{axis_name} must include label, unit, and values")
            elif axis.get("format") is not None and (not isinstance(axis.get("format"), str) or not FORMATS.match(axis["format"])):
                errors.append(f"sensitivities[{i}].{axis_name}.format is invalid")
            elif axis.get("unit_label") is not None and not isinstance(axis.get("unit_label"), str):
                errors.append(f"sensitivities[{i}].{axis_name}.unit_label must be a string")
        matrix = sens.get("matrix")
        row_axis = sens.get("row_axis", {})
        col_axis = sens.get("column_axis", {})
        if isinstance(matrix, list) and isinstance(row_axis, dict) and isinstance(col_axis, dict):
            rvals, cvals = row_axis.get("values", []), col_axis.get("values", [])
            if len(matrix) != len(rvals):
                errors.append(f"sensitivities[{i}].matrix row count must match row_axis.values")
            for j, mrow in enumerate(matrix):
                if not isinstance(mrow, list) or len(mrow) != len(cvals):
                    errors.append(f"sensitivities[{i}].matrix[{j}] width must match column_axis.values")
                elif any(v is not None and not _is_number(v) for v in mrow):
                    errors.append(f"sensitivities[{i}].matrix[{j}] values must be finite numbers or null")
        fixed = sens.get("fixed_assumptions", [])
        if not isinstance(fixed, list) or any(not isinstance(x, str) for x in fixed):
            errors.append(f"sensitivities[{i}].fixed_assumptions must be an array of strings")
    for key in ("footnotes", "diagnostics"):
        if key in data and (not isinstance(data[key], list) or any(not isinstance(x, str) for x in data[key])):
            errors.append(f"{key} must be an array of strings")
    sources = data.get("sources", [])
    if not isinstance(sources, list):
        errors.append("sources must be an array")
    else:
        for i, src in enumerate(sources):
            if not isinstance(src, dict) or not isinstance(src.get("id"), str) or not isinstance(src.get("citation"), str):
                errors.append(f"sources[{i}] requires string id and citation")
    return errors


def currency_symbol(currency: str) -> str:
    return {"USD": "$", "CNY": "¥", "RMB": "¥", "EUR": "€", "GBP": "£", "JPY": "¥", "HKD": "HK$"}.get(currency.upper(), currency.upper() + " ")


def display_unit(unit: str, explicit: str | None = None) -> str:
    return explicit if explicit is not None else UNIT_LABELS.get(unit, unit)


def format_axis_value(value: Any, axis: dict[str, Any], currency: str) -> str:
    fmt = axis.get("format")
    if not fmt and axis.get("unit") == "ratio":
        fmt = "percent:1"
    if fmt:
        return format_value(value, fmt, currency)
    if value is None:
        return "-"
    try:
        v = Decimal(str(value))
        return f"{v:,.{max(0, -v.as_tuple().exponent)}f}".rstrip("0").rstrip(".") if "." in f"{v:,.{max(0, -v.as_tuple().exponent)}f}" else f"{v:,.0f}"
    except (InvalidOperation, ValueError):
        return str(value)


def row_comment(row: dict[str, Any]) -> str:
    parts = [str(x).strip() for x in (row.get("comment"), row.get("evidence")) if x and str(x).strip()]
    if row.get("role") == "reported" and row.get("key"):
        parts.append("原报告关键结果，保留原值，未重算。")
    return "；".join(dict.fromkeys(parts))


def format_value(value: Any, fmt: str, currency: str) -> str:
    if value is None:
        return "-"
    if not _is_number(value):
        return "-"
    try:
        v = Decimal(str(value))
    except InvalidOperation:
        return "-"
    if fmt == "integer":
        rounded = v.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        return f"{rounded:,.0f}"
    kind, digits_text = fmt.split(":", 1)
    digits = int(digits_text)
    quantum = Decimal("1").scaleb(-digits)
    if kind == "decimal":
        rounded = v.quantize(quantum, rounding=ROUND_HALF_UP)
        return f"{rounded:,.{digits}f}"
    if kind == "percent":
        rounded = (v * Decimal("100")).quantize(quantum, rounding=ROUND_HALF_UP)
        return f"{rounded:,.{digits}f}%"
    if kind in {"money", "eps"}:
        symbol = currency_symbol(currency)
        if v < 0:
            rounded = abs(v).quantize(quantum, rounding=ROUND_HALF_UP)
            return f"({symbol}{rounded:,.{digits}f})"
        rounded = v.quantize(quantum, rounding=ROUND_HALF_UP)
        return f"{symbol}{rounded:,.{digits}f}"
    return "-"


def visible_value(row: dict[str, Any], sid: str) -> Any:
    status = row.get("status", {}).get(sid)
    val = row.get("values", {}).get(sid)
    if status in {"missing", "blocked"} or val is None:
        return None
    return val


def _row_lookup(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["id"]: r for r in data["rows"]}


def _expression_refs(expr: str) -> set[str]:
    tree = ast.parse(expr, mode="eval")
    refs: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in {"min", "max", "abs", "row", "ref"}:
            refs.add(node.id)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"row", "ref"}:
            if len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                refs.add(node.args[0].value)
    return refs


def _excel_expression(expr: str, refs: dict[str, str]) -> str:
    """Translate a constrained arithmetic AST into an Excel expression."""
    tree = ast.parse(expr, mode="eval")
    binary = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/", ast.Pow: "^"}
    unary = {ast.UAdd: "+", ast.USub: "-"}

    def emit(node: ast.AST) -> str:
        if isinstance(node, ast.Expression):
            return emit(node.body)
        if isinstance(node, ast.Constant) and _is_number(node.value):
            return str(node.value)
        if isinstance(node, ast.Name):
            if node.id in {"min", "max", "abs", "row", "ref"}:
                raise InputError(f"function name {node.id!r} cannot be used as a row id")
            if node.id not in refs:
                raise InputError(f"unknown row id {node.id!r} in expression")
            return refs[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in binary:
            return f"({emit(node.left)}{binary[type(node.op)]}{emit(node.right)})"
        if isinstance(node, ast.UnaryOp) and type(node.op) in unary:
            return f"({unary[type(node.op)]}{emit(node.operand)})"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            fn = node.func.id
            if fn in {"row", "ref"} and len(node.args) == 1 and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                rid = node.args[0].value
                if rid not in refs:
                    raise InputError(f"unknown row id {rid!r} in expression")
                return refs[rid]
            if fn in {"min", "max", "abs"} and not node.keywords:
                args = ",".join(emit(arg) for arg in node.args)
                if fn == "abs" and len(node.args) != 1:
                    raise InputError("abs() takes exactly one argument")
                if fn in {"min", "max"} and not node.args:
                    raise InputError(f"{fn}() requires at least one argument")
                return f"{fn.upper()}({args})"
        raise InputError(f"unsupported expression syntax: {ast.dump(node, include_attributes=False)}")

    return emit(tree)


def _excel_number_format(fmt: str, currency: str) -> str:
    if fmt == "integer":
        return '#,##0;[Red](#,##0);-'
    kind, digits_text = fmt.split(":", 1)
    digits = int(digits_text)
    places = ("." + "0" * digits) if digits else ""
    if kind == "percent":
        return f"0{places}%"
    if kind in {"money", "eps"}:
        symbol = currency_symbol(currency).replace('"', '""')
        return f'"{symbol}"#,##0{places};[Red]("{symbol}"#,##0{places});-'
    return f"#,##0{places};[Red](#,##0{places});-"


def _xml_text(parent: ET.Element, tag: str, text: Any = "", attrs: dict[str, Any] | None = None) -> ET.Element:
    el = ET.SubElement(parent, tag, {k: str(v) for k, v in (attrs or {}).items() if v is not None})
    el.text = "" if text is None else str(text)
    return el


def render_xml(data: dict[str, Any]) -> str:
    root = ET.Element("exhibits", {"schema_version": SCHEMA_VERSION, "title": data["title"], "period": data["period"], "currency": data["currency"]})
    bridge = ET.SubElement(root, "table", {"id": "bridge", "kind": "feishu-native-table-fragment"})
    _xml_text(bridge, "caption", f"{data['title']} — {data['period']}")
    head = ET.SubElement(bridge, "thead")
    _xml_text(ET.SubElement(head, "tr"), "th", "项目")
    _xml_text(head[-1], "th", "单位")
    for sc in data["scenarios"]:
        _xml_text(head[-1], "th", sc["label"], {"scenario_id": sc["id"]})
    _xml_text(head[-1], "th", "Comment")
    body = ET.SubElement(bridge, "tbody")
    for row in data["rows"]:
        tr = ET.SubElement(body, "tr", {"row_id": row["id"], "role": row["role"], "indent": str(row.get("indent", 0)), "key": str(bool(row.get("key"))).lower()})
        _xml_text(tr, "th", row["label"])
        _xml_text(tr, "td", display_unit(row["unit"], row.get("unit_label")), {"unit_code": row["unit"]})
        for sc in data["scenarios"]:
            sid = sc["id"]
            status = row.get("status", {}).get(sid, "missing" if row.get("values", {}).get(sid) is None else "input")
            val = visible_value(row, sid)
            cell = ET.SubElement(tr, "td", {"scenario_id": sid, "status": status, "format": row["format"], "value": "" if val is None else str(val)})
            cell.text = "" if row["role"] in {"section", "note"} and val is None else format_value(val, row["format"], data["currency"])
        _xml_text(tr, "td", row_comment(row), {"evidence": row.get("evidence", "")})
    for sens in data.get("sensitivities", []) or []:
        table = ET.SubElement(root, "table", {"id": sens["id"], "kind": "feishu-native-table-fragment"})
        _xml_text(table, "caption", sens["title"])
        _xml_text(table, "axis", sens["row_axis"]["label"], {"position": "row", "unit": sens["row_axis"]["unit"], "unit_label": display_unit(sens["row_axis"]["unit"], sens["row_axis"].get("unit_label")), "format": sens["row_axis"].get("format")})
        _xml_text(table, "axis", sens["column_axis"]["label"], {"position": "column", "unit": sens["column_axis"]["unit"], "unit_label": display_unit(sens["column_axis"]["unit"], sens["column_axis"].get("unit_label")), "format": sens["column_axis"].get("format")})
        _xml_text(table, "output_unit", display_unit(sens["output_unit"], sens.get("output_unit_label")), {"unit_code": sens["output_unit"]})
        hrow = ET.SubElement(ET.SubElement(table, "thead"), "tr")
        col_axis=sens["column_axis"]
        _xml_text(hrow, "th", f"{col_axis['label']} ({display_unit(col_axis['unit'], col_axis.get('unit_label'))})")
        for cv in sens["column_axis"]["values"]:
            _xml_text(hrow, "th", format_axis_value(cv, col_axis, data["currency"]), {"value": cv})
        tbody = ET.SubElement(table, "tbody")
        for rv, matrix_row in zip(sens["row_axis"]["values"], sens["matrix"]):
            tr = ET.SubElement(tbody, "tr")
            _xml_text(tr, "th", format_axis_value(rv, sens["row_axis"], data["currency"]), {"row_axis_value": str(rv)})
            for cv, val in zip(sens["column_axis"]["values"], matrix_row):
                cell = ET.SubElement(tr, "td", {"row_axis_value": str(rv), "column_axis_value": str(cv), "value": "" if val is None else str(val), "unit": sens["output_unit"]})
                cell.text = format_value(val, sens["format"], data["currency"])
        _xml_text(table, "fixed_assumptions", "\n".join(sens.get("fixed_assumptions", [])))
        if sens.get("footnote"):
            _xml_text(table, "footnote", sens["footnote"])
    footnotes = ET.SubElement(root, "footnotes")
    for note in data.get("footnotes", []) or []:
        _xml_text(footnotes, "footnote", note)
    diagnostics = ET.SubElement(root, "appendix", {"id": "diagnostics"})
    for note in _diagnostics(data):
        _xml_text(diagnostics, "diagnostic", note)
    sources = ET.SubElement(root, "sources")
    for src in data.get("sources", []) or []:
        el = ET.SubElement(sources, "source", {"id": src["id"]})
        _xml_text(el, "citation", src["citation"])
        for key in ("pdf_page", "url", "source_hash"):
            if src.get(key) is not None:
                _xml_text(el, key, src[key])
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def _feishu_p(parent: ET.Element, text: Any, bold: bool = False) -> None:
    p = ET.SubElement(parent, "p")
    if bold:
        ET.SubElement(p, "b").text = "" if text is None else str(text)
    else:
        p.text = "" if text is None else str(text)


def _feishu_table(parent: ET.Element, headers: list[str], rows: list[list[tuple[Any, bool]]]) -> None:
    table = ET.SubElement(parent, "table")
    colgroup = ET.SubElement(table, "colgroup")
    if headers:
        ET.SubElement(colgroup, "col", {"width": "230"})
        for label in headers[1:]:
            ET.SubElement(colgroup, "col", {"width": "300" if label == "Comment" else "95"})
    thead = ET.SubElement(table, "thead")
    tr = ET.SubElement(thead, "tr")
    for label in headers:
        th = ET.SubElement(tr, "th", {"vertical-align": "top", "background-color": "light-gray"})
        _feishu_p(th, label, bold=True)
    tbody = ET.SubElement(table, "tbody")
    for values in rows:
        tr = ET.SubElement(tbody, "tr")
        key_row = bool(headers and headers[-1] == "Comment" and any(bold for _, bold in values[2:-1]))
        for text, bold in values:
            attrs = {"vertical-align": "top"}
            if key_row:
                attrs["background-color"] = "light-yellow"
            td = ET.SubElement(tr, "td", attrs)
            _feishu_p(td, text, bold=bold)


def render_feishu_xml(data: dict[str, Any]) -> str:
    """Return a Feishu-importable XML block fragment, without audit diagnostics."""
    root = ET.Element("fragment")
    h1 = ET.SubElement(root, "h1")
    h1.text = data["title"]
    _feishu_p(root, f"{data['period']} · 币种 {data['currency']}")
    h = ET.SubElement(root, "h2")
    h.text = "测算桥"
    scenarios = data["scenarios"]
    headers = ["项目", "单位"] + [sc["label"] for sc in scenarios] + ["Comment"]
    bridge_rows = []
    for row in data["rows"]:
        role = row["role"]
        key = bool(row.get("key")) or role == "section"
        label = "　" * min(int(row.get("indent", 0)), 8) + row["label"]
        values: list[tuple[Any, bool]] = [(label, key), (display_unit(row["unit"], row.get("unit_label")), False)]
        for sc in scenarios:
            val = visible_value(row, sc["id"])
            shown = "" if role in {"section", "note"} and val is None else format_value(val, row["format"], data["currency"])
            values.append((shown, bool(row.get("key"))))
        values.append((row_comment(row), False))
        bridge_rows.append(values)
    _feishu_table(root, headers, bridge_rows)

    for sens in data.get("sensitivities", []) or []:
        h = ET.SubElement(root, "h2")
        h.text = sens["title"]
        row_axis, col_axis = sens["row_axis"], sens["column_axis"]
        row_unit = display_unit(row_axis["unit"], row_axis.get("unit_label"))
        col_unit = display_unit(col_axis["unit"], col_axis.get("unit_label"))
        out_unit = display_unit(sens["output_unit"], sens.get("output_unit_label"))
        _feishu_p(root, f"行：{row_axis['label']}（{row_unit}）；列：{col_axis['label']}（{col_unit}）；结果单位：{out_unit}")
        sens_headers = [f"{row_axis['label']}（{row_unit}）"] + [format_axis_value(v, col_axis, data["currency"]) for v in col_axis["values"]]
        sens_rows = []
        for row_value, matrix_row in zip(row_axis["values"], sens["matrix"]):
            vals = [(format_axis_value(row_value, row_axis, data["currency"]), True)]
            vals.extend((format_value(v, sens["format"], data["currency"]), False) for v in matrix_row)
            sens_rows.append(vals)
        _feishu_table(root, sens_headers, sens_rows)
        for assumption in sens.get("fixed_assumptions", []) or []:
            _feishu_p(root, f"固定假设：{assumption}")
        if sens.get("footnote"):
            _feishu_p(root, f"注：{sens['footnote']}")

    if data.get("sources") or data.get("footnotes"):
        h = ET.SubElement(root, "h2")
        h.text = "来源与注释"
        for src in data.get("sources", []) or []:
            page = f"，PDF p.{src['pdf_page']}" if src.get("pdf_page") is not None else ""
            _feishu_p(root, f"来源：{src['citation']}{page}")
        for note in data.get("footnotes", []) or []:
            _feishu_p(root, f"注：{note}")

    # Feishu CLI accepts a sequence of supported block tags. The artificial root is
    # only used to build and escape the fragment, so emit its child blocks directly.
    return "".join(ET.tostring(child, encoding="unicode", short_empty_elements=True) for child in root)


def _diagnostics(data: dict[str, Any]) -> list[str]:
    notes = list(data.get("diagnostics", []) or [])
    original_notes = set(notes)
    lookup = _row_lookup(data)
    row_ids = set(lookup)
    for row in data["rows"]:
        if row.get("role") != "calculation":
            continue
        expr = row.get("expression")
        if not expr:
            notes.append(f"{row['id']}: calculation has no expression; values are rendered as snapshots, not formulas.")
            continue
        try:
            refs = _expression_refs(expr)
            unknown = refs - row_ids
            if unknown:
                notes.append(f"{row['id']}: expression references unknown row ids: {', '.join(sorted(unknown))}.")
            else:
                _excel_expression(expr, {rid: "A1" for rid in refs})
                for sc in data["scenarios"]:
                    sid = sc["id"]
                    status = row.get("status", {}).get(sid, "missing" if row.get("values", {}).get(sid) is None else "input")
                    if status != "computed" or visible_value(row, sid) is None:
                        continue
                    for rid in refs:
                        refrow = lookup[rid]
                        if visible_value(refrow, sid) is None:
                            notes.append(f"{row['id']} / {sid}: formula blocked because reference {rid!r} is missing or blocked.")
        except (SyntaxError, InputError) as exc:
            notes.append(f"{row['id']}: expression cannot be translated safely: {exc}.")
    appended = []
    seen = set(original_notes)
    for note in notes[len(data.get("diagnostics", []) or []):]:
        if note not in seen:
            appended.append(note)
            seen.add(note)
    return notes[:len(data.get("diagnostics", []) or [])] + appended


def _html_table(data: dict[str, Any]) -> str:
    scs = data["scenarios"]
    chunks = ['<table class="bridge"><thead><tr><th>项目</th><th>单位</th>']
    chunks.extend(f'<th>{html.escape(sc["label"])}</th>' for sc in scs)
    chunks.append("<th>Comment</th>")
    chunks.append("</tr></thead><tbody>")
    for row in data["rows"]:
        role = row["role"]
        cls = "section" if role == "section" else ("key" if row.get("key") else "")
        label = "&nbsp;" * (int(row.get("indent", 0)) * 3) + html.escape(row["label"])
        chunks.append(f'<tr class="{cls}" data-row-id="{html.escape(row["id"])}"><th>{label}</th><td class="unit">{html.escape(display_unit(row["unit"], row.get("unit_label")))}</td>')
        for sc in scs:
            sid = sc["id"]
            val = visible_value(row, sid)
            status = row.get("status", {}).get(sid, "missing" if val is None else "input")
            value_cls = "input" if status == "input" or row["role"] == "input" else "computed"
            display = "" if row["role"] in {"section", "note"} and val is None else format_value(val, row["format"], data["currency"])
            chunks.append(f'<td class="num {value_cls}" data-status="{status}">{html.escape(display)}</td>')
        chunks.append(f'<td class="comment">{html.escape(row_comment(row))}</td>')
        chunks.append("</tr>")
    chunks.append("</tbody></table>")
    return "".join(chunks)


def render_html(data: dict[str, Any]) -> str:
    out = ["<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"]
    out.append("<style>body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Heiti SC',sans-serif;color:#1a1a1a;max-width:1500px;margin:32px auto;padding:0 18px}h1{font-size:24px;margin:0 0 6px}h2{font-size:18px;margin:30px 0 8px}.meta,.caption{color:#595959;font-size:13px;margin:0 0 14px}.bridge,.sens{border-collapse:collapse;width:100%;font-size:14px;margin:12px 0 8px}.bridge th,.bridge td,.sens th,.sens td{border-top:1px solid #aaa;border-bottom:1px solid #aaa;padding:7px 9px;vertical-align:top}.bridge thead,.sens thead{background:#e7e6e6}.bridge th,.sens th{text-align:left}.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.unit{color:#595959;white-space:nowrap}.comment{white-space:normal;min-width:240px;max-width:440px;color:#444;font-size:12px}.section{background:#e7e6e6;font-weight:700;border-top:2px solid #555}.key{background:#fff2cc;font-weight:700}.input{color:#245b83}.computed{color:#111}.note,.source,.diag{font-size:12px;color:#444;margin:5px 0}.footnotes,.sources,.diagnostics{margin-top:18px}.sens thead{background:#203864;color:#fff}.sens tbody td{background:#fff}.sensitivity-wrap{overflow-x:auto}.axis{color:#595959;font-size:12px;margin:0 0 4px}</style></head><body>")
    out.append(f"<h1>{html.escape(data['title'])}</h1><p class=\"meta\">{html.escape(data['period'])} · 币种 {html.escape(data['currency'])}</p>")
    out.append("<h2>测算桥</h2>")
    out.append(_html_table(data))
    for sens in data.get("sensitivities", []) or []:
        out.append(f"<h2>{html.escape(sens['title'])}</h2>")
        out.append(f"<p class=\"axis\">行：{html.escape(sens['row_axis']['label'])}（{html.escape(display_unit(sens['row_axis']['unit'], sens['row_axis'].get('unit_label')))}）；列：{html.escape(sens['column_axis']['label'])}（{html.escape(display_unit(sens['column_axis']['unit'], sens['column_axis'].get('unit_label')))}）；结果单位：{html.escape(display_unit(sens['output_unit'], sens.get('output_unit_label')))}</p>")
        out.append('<div class="sensitivity-wrap"><table class="sens"><thead><tr>')
        out.append(f"<th>{html.escape(sens['row_axis']['label'])} \\ {html.escape(sens['column_axis']['label'])}</th>")
        out.extend(f"<th class=\"num\">{html.escape(format_axis_value(v, sens['column_axis'], data['currency']))}</th>" for v in sens['column_axis']['values'])
        out.append("</tr></thead><tbody>")
        for rv, matrix_row in zip(sens['row_axis']['values'], sens['matrix']):
            out.append(f"<tr><th>{html.escape(format_axis_value(rv, sens['row_axis'], data['currency']))}</th>")
            out.extend(f"<td class=\"num\">{html.escape(format_value(v, sens['format'], data['currency']))}</td>" for v in matrix_row)
            out.append("</tr>")
        out.append("</tbody></table></div>")
        for item in sens.get("fixed_assumptions", []):
            out.append(f"<p class=\"note\">固定假设：{html.escape(item)}</p>")
        if sens.get("footnote"):
            out.append(f"<p class=\"note\">{html.escape(sens['footnote'])}</p>")
    if data.get("footnotes"):
        out.append("<div class=\"footnotes\"><h2>注释</h2><ol>")
        out.extend(f"<li class=\"note\">{html.escape(x)}</li>" for x in data['footnotes'])
        out.append("</ol></div>")
    if data.get("sources"):
        out.append("<div class=\"sources\"><h2>来源</h2><ol>")
        for src in data['sources']:
            page = f", PDF p.{html.escape(str(src['pdf_page']))}" if src.get('pdf_page') is not None else ""
            link = f' <a href="{html.escape(src["url"], quote=True)}">原文链接</a>' if src.get('url') else ""
            digest = f' <code>{html.escape(src["source_hash"])}</code>' if src.get('source_hash') else ""
            out.append(f"<li class=\"source\" id=\"source-{html.escape(src['id'])}\">{html.escape(src['citation'])}{page}{link}{digest}</li>")
        out.append("</ol></div>")
    diagnostics = _diagnostics(data)
    if diagnostics:
        out.append("<details class=\"diagnostics\"><summary>完整诊断记录</summary><ul>")
        out.extend(f"<li class=\"diag\">{html.escape(x)}</li>" for x in diagnostics)
        out.append("</ul></details>")
    out.append("</body></html>")
    return "".join(out)


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    candidates = [
        ("/System/Library/Fonts/PingFang.ttc", 1 if bold else 0),
        ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
        ("/System/Library/AssetsV2/com_apple_MobileAsset_Font8/86ba2c91f017a3749571a82f2c6d890ac7ffb2fb.asset/AssetData/PingFang.ttc", 1 if bold else 0),
        ("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 0),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 0),
    ]
    for path, index in candidates:
        try:
            return ImageFont.truetype(path, size, index=index)
        except (OSError, TypeError):
            continue
    return ImageFont.load_default()


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    lines: list[str] = []
    for para in str(text).split("\n"):
        if not para:
            lines.append("")
            continue
        line = ""
        for char in para:
            trial = line + char
            if not line or draw.textlength(trial, font=font) <= max_width:
                line = trial
            else:
                lines.append(line)
                line = char
        lines.append(line)
    return lines or [""]


def _render_png_table(path: Path, title: str, subtitle: str, headers: list[str], rows: list[list[dict[str, Any]]], footers: list[str]) -> None:
    from PIL import Image, ImageDraw
    font = _font(18)
    bold = _font(18, True)
    small = _font(14)
    title_font = _font(23, True)
    label_w = 370
    unit_w = 120
    scenario_w = 145
    has_comment = bool(headers and headers[-1] == "Comment")
    compact = bool(headers and headers[0] not in {"项目", "Item"})
    if compact:
        widths = [label_w] + [scenario_w] * (len(headers) - 1)
    else:
        widths = [label_w, unit_w] + [scenario_w] * max(0, len(headers) - 2 - int(has_comment))
        if has_comment:
            widths.append(390)
    full_w = sum(widths)
    x0, margin = 26, 26
    draw_probe = Image.new("RGB", (10, 10), WHITE)
    probe = ImageDraw.Draw(draw_probe)
    title_lines = _wrap_text(probe, title, title_font, full_w)
    subtitle_lines = _wrap_text(probe, subtitle, small, full_w)
    top_h = 22 + len(title_lines) * 32 + len(subtitle_lines) * 22 + 18
    header_h = 44
    line_h = 25
    heights=[]
    for row in rows:
        wrapped_counts=[]
        for i, cell in enumerate(row):
            if i >= len(widths):
                break
            text = str(cell.get("text", ""))
            if i == 0:
                pad=min(int(cell.get("indent",0))*18, 110)
                available=widths[i]-14-pad
                cellfont=bold if cell.get("bold") or cell.get("section") else font
            elif i == 1 and not compact:
                available=widths[i]-14
                cellfont=small
            elif i == len(widths)-1 and has_comment:
                available=widths[i]-14
                cellfont=small
            else:
                available=widths[i]-14
                cellfont=font
            wrapped_counts.append(len(_wrap_text(probe,text,cellfont,available)))
        heights.append(max(40, max(wrapped_counts or [1]) * line_h + 10))
    footer_h=sum(max(24, len(_wrap_text(probe, t, small, full_w - 18)) * 20) + 3 for t in footers)
    canvas_h=top_h + header_h + sum(heights) + footer_h + 26
    image=Image.new("RGB",(full_w+2*x0,canvas_h),WHITE)
    draw=ImageDraw.Draw(image)
    y=20
    for line in title_lines:
        draw.text((x0,y),line,font=title_font,fill=INK); y+=32
    for line in subtitle_lines:
        draw.text((x0,y),line,font=small,fill=MUTED); y+=22
    y+=18
    x_positions=[x0]
    for w in widths: x_positions.append(x_positions[-1]+w)
    draw.rectangle((x0,y,x0+full_w,y+header_h),fill="#203864" if compact else SECTION)
    for i,h in enumerate(headers):
        lines=_wrap_text(draw,h,font,widths[i]-14)
        yy=y+5
        for line in lines[:2]:
            draw.text((x_positions[i]+7,yy),line,font=bold,fill=WHITE if compact else INK,stroke_width=1); yy+=20
    y+=header_h
    for row,height in zip(rows,heights):
        meta=row[0]
        if meta.get("section"):
            draw.rectangle((x0,y,x0+full_w,y+height),fill=SECTION)
        elif meta.get("key"):
            draw.rectangle((x0,y,x0+full_w,y+height),fill=KEY)
        elif meta.get("input"):
            draw.rectangle((x0,y,x0+full_w,y+height),fill=FILL_BLUE)
        xx=x0
        for i,cell in enumerate(row):
            w=widths[i]
            text=cell.get("text", "-")
            cellfont=bold if cell.get("bold") or meta.get("section") else font
            color=cell.get("color",INK)
            if i==0:
                pad=min(int(cell.get('indent',0))*18,110)
                lines=_wrap_text(draw,text,cellfont,w-14-pad)
                yy=y+5
                for line in lines:
                    draw.text((xx+7+pad,yy),line,font=cellfont,fill=color,stroke_width=1 if cell.get("bold") or meta.get("section") else 0); yy+=line_h
            elif i==1 and not compact:
                lines=_wrap_text(draw,text,small,w-14)
                yy=y+5
                for line in lines:
                    draw.text((xx+6,yy),line,font=small,fill=MUTED); yy+=line_h
            elif i==len(widths)-1 and has_comment:
                lines=_wrap_text(draw,text,small,w-14)
                yy=y+5
                for line in lines:
                    draw.text((xx+7,yy),line,font=small,fill=color); yy+=line_h
            else:
                tw=draw.textlength(text,font=cellfont)
                draw.text((xx+w-tw-9,y+(height-21)//2),text,font=cellfont,fill=color,stroke_width=1 if cell.get("bold") else 0)
            xx+=w
        draw.line((x0,y+height,x0+full_w,y+height),fill=GRID,width=1)
        y+=height
    # Column rules make the result readable as a table when downscaled.
    for xp in x_positions:
        draw.line((xp,top_h,xp,y),fill=GRID,width=1)
    for footer in footers:
        lines=_wrap_text(draw,footer,small,full_w-18)
        y+=5
        for line in lines:
            draw.text((x0+6,y),line,font=small,fill=MUTED); y+=20
    image.save(path)


def render_pngs(data: dict[str, Any], outdir: Path) -> list[Path]:
    scs=data["scenarios"]
    headers=["项目","单位"]+[s["label"] for s in scs]+["Comment"]
    rows=[]
    for row in data["rows"]:
        cells=[{"text":row["label"],"indent":row.get("indent",0),"section":row["role"]=="section","key":bool(row.get("key")),"input":row["role"]=="input","bold":bool(row.get("key")) or row["role"]=="section"}]
        cells.append({"text":display_unit(row["unit"],row.get("unit_label"))})
        for sc in scs:
            sid=sc["id"]; val=visible_value(row,sid)
            status=row.get("status",{}).get(sid,"missing" if val is None else "input")
            color=BLUE if (status=="input" and row["role"]!="calculation") or row["role"] in {"input","benchmark"} else BLACK
            display = "" if row["role"] in {"section", "note"} and val is None else format_value(val,row["format"],data["currency"])
            cells.append({"text":display,"color":color,"bold":bool(row.get("key"))})
        cells.append({"text":row_comment(row),"color":MUTED})
        rows.append(cells)
    footers=[]
    for src in data.get("sources",[]) or []:
        page=f"，PDF p.{src['pdf_page']}" if src.get("pdf_page") is not None else ""
        footers.append(f"来源：{src['citation']}{page}")
    footers.extend(f"注：{x}" for x in data.get("footnotes",[]) or [])
    paths=[]
    bridge=outdir/"bridge.png"
    _render_png_table(bridge,data["title"],f"{data['period']} · 币种 {data['currency']}",headers,rows,footers)
    paths.append(bridge)
    for sens in data.get("sensitivities",[]) or []:
        # Axis names and units are stated in the subtitle; header cells retain only each axis value.
        row_axis=sens["row_axis"]
        col_axis=sens["column_axis"]
        headers2=[f"{row_axis['label']} ({display_unit(row_axis['unit'],row_axis.get('unit_label'))})"]+[format_axis_value(x,col_axis,data["currency"]) for x in col_axis["values"]]
        sens_footer=[f"结果单位：{display_unit(sens['output_unit'],sens.get('output_unit_label'))}"]+[f"固定假设：{x}" for x in sens.get('fixed_assumptions',[])]
        if sens.get('footnote'): sens_footer.append(f"注：{sens['footnote']}")
        path=outdir/f"sensitivity_{sens['id']}.png"
        title=f"{data['title']} — {sens['title']}"
        subtitle=f"行：{row_axis['label']}（{display_unit(row_axis['unit'],row_axis.get('unit_label'))}）；列：{col_axis['label']}（{display_unit(col_axis['unit'],col_axis.get('unit_label'))}）；结果：{display_unit(sens['output_unit'],sens.get('output_unit_label'))}"
        png_rows=[]
        for rv,matrix_row in zip(row_axis['values'],sens['matrix']):
            cells=[{"text":format_axis_value(rv,row_axis,data["currency"]),"bold":True}]
            cells.extend({"text":format_value(v,sens['format'],data['currency']),"color":BLACK,"bold":False} for v in matrix_row)
            png_rows.append(cells)
        _render_png_table(path,title,subtitle,headers2,png_rows,sens_footer)
        paths.append(path)
    return paths


def render_xlsx(data: dict[str, Any], outpath: Path) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.comments import Comment
    from openpyxl.utils import get_column_letter

    wb=Workbook()
    ws=wb.active; ws.title="Bridge"
    scs=data["scenarios"]
    last_col=3+len(scs)
    ws.merge_cells(start_row=1,start_column=1,end_row=1,end_column=last_col)
    ws.cell(1,1,data["title"]).font=Font(name="Arial",size=15,bold=True,color="1A1A1A")
    ws.merge_cells(start_row=2,start_column=1,end_row=2,end_column=last_col)
    ws.cell(2,1,f"{data['period']} | Currency: {data['currency']}").font=Font(name="Arial",size=10,color="595959")
    headers=["Item","Unit"]+[s["label"] for s in scs]+["Comment"]
    for col,val in enumerate(headers,1):
        cell=ws.cell(4,col,val); cell.font=Font(bold=True,color="1A1A1A"); cell.fill=PatternFill("solid",fgColor="E7E6E6"); cell.alignment=Alignment(horizontal="center",vertical="center",wrap_text=True)
    thin=Side(style="thin",color="B7B7B7")
    med=Side(style="medium",color="666666")
    row_excel={r["id"]:5+i for i,r in enumerate(data["rows"])}
    sc_col={s["id"]:3+i for i,s in enumerate(scs)}
    lookup=_row_lookup(data)
    diagnostics=[]
    # Dependency cycles are diagnosed and left as original values, not emitted as circular formulas.
    graph={r["id"]:set() for r in data["rows"] if r.get("role")=="calculation" and r.get("expression")}
    for rid in graph:
        try: graph[rid]=_expression_refs(lookup[rid]["expression"]) & set(graph)
        except Exception: graph[rid]=set()
    cyclic=set()
    def visit(node,stack,done):
        if node in stack:
            cyclic.update(stack[stack.index(node):]); return
        if node in done: return
        stack.append(node)
        for child in graph.get(node,set()): visit(child,stack,done)
        stack.pop(); done.add(node)
    done=set()
    for node in graph: visit(node,[],done)
    if cyclic: diagnostics.append("Circular calculation references blocked: "+", ".join(sorted(cyclic)))

    for i,row in enumerate(data["rows"]):
        rr=5+i; role=row["role"]
        label="  "*row.get("indent",0)+row["label"]
        c=ws.cell(rr,1,label); c.alignment=Alignment(vertical="center",wrap_text=True)
        ws.cell(rr,2,display_unit(row["unit"],row.get("unit_label"))).font=Font(color="666666",size=9)
        comment_cell=ws.cell(rr,last_col,row_comment(row))
        comment_cell.alignment=Alignment(vertical="top",wrap_text=True)
        comment_cell.font=Font(color="595959",size=9)
        if role=="section":
            for col in range(1,last_col+1):
                ws.cell(rr,col).fill=PatternFill("solid",fgColor="E7E6E6")
                ws.cell(rr,col).font=Font(bold=True,color="1A1A1A")
        elif row.get("key"):
            for col in range(1,last_col+1):
                ws.cell(rr,col).fill=PatternFill("solid",fgColor="FFF2CC")
                ws.cell(rr,col).font=Font(bold=True,color="1A1A1A")
        for sc in scs:
            sid=sc["id"]; col=sc_col[sid]
            raw=row.get("values",{}).get(sid)
            status=row.get("status",{}).get(sid,"missing" if raw is None else "input")
            val=visible_value(row,sid)
            cell=ws.cell(rr,col)
            cell.number_format=_excel_number_format(row["format"],data["currency"])
            cell.alignment=Alignment(horizontal="right",vertical="center")
            formula_used=False
            if role=="calculation" and row.get("expression") and status=="computed" and val is not None and row["id"] not in cyclic:
                try:
                    refs={}
                    for rid in _expression_refs(row["expression"]):
                        if rid not in lookup: raise InputError(f"unknown row id {rid!r}")
                        refrow=lookup[rid]
                        ref_status=refrow.get("status",{}).get(sid,"missing" if refrow.get("values",{}).get(sid) is None else "input")
                        if visible_value(refrow,sid) is None or ref_status in {"missing","blocked"}:
                            raise InputError(f"reference {rid!r} is missing/blocked in scenario {sid!r}")
                        refs[rid]=f"{get_column_letter(col)}{row_excel[rid]}"
                    cell.value="="+_excel_expression(row["expression"],refs)
                    cell.font=Font(color="000000",bold=bool(row.get("key")))
                    formula_used=True
                except (SyntaxError,InputError) as exc:
                    diagnostics.append(f"{row['id']} / {sid}: formula blocked: {exc}")
            if not formula_used:
                if val is None:
                    cell.value=None if role in {"section","note"} else "-"
                    cell.font=Font(color="777777",bold=bool(row.get("key")))
                else:
                    cell.value=val
                    if role=="calculation": color="000000"
                    elif role=="benchmark": color="0000FF"
                    else: color="FF0000" if status in {"input","reported"} else "0000FF"
                    cell.font=Font(color=color,bold=bool(row.get("key")))
            details=[f"role={role}",f"status={status}"]
            if row.get("expression"): details.append(f"expression={row['expression']}")
            if row.get("evidence"): details.append(f"evidence={row['evidence']}")
            if row.get("comment"): details.append(str(row["comment"]))
            if role=="reported": details.append("Reported source value; retained as entered, not recomputed.")
            if role=="calculation" and not row.get("expression"): details.append("Snapshot value: expression not provided; no formula emitted.")
            cell.comment=Comment("\n".join(details),"Sellside exhibit renderer")
        for col in range(1,last_col+1):
            cell=ws.cell(rr,col)
            cell.border=Border(bottom=med if role=="section" else thin)
    ws.column_dimensions["A"].width=46; ws.column_dimensions["B"].width=22
    for col in range(3,last_col+1): ws.column_dimensions[get_column_letter(col)].width=18
    ws.column_dimensions[get_column_letter(last_col)].width=58
    ws.freeze_panes="C5"; ws.auto_filter.ref=f"A4:{get_column_letter(last_col)}{4+len(data['rows'])}"
    ws.sheet_view.showGridLines=False
    ws.row_dimensions[4].height=32
    for i,row in enumerate(data["rows"]):
        lines=max(1,math.ceil(len(row_comment(row))/72))
        ws.row_dimensions[5+i].height=max(23,15*lines+8)

    # Sensitivity matrices are source/house values and remain literal inputs.
    ss=wb.create_sheet("Sensitivities")
    sr=1
    for sens in data.get("sensitivities",[]) or []:
        ncols=1+len(sens["column_axis"]["values"])
        ss.merge_cells(start_row=sr,start_column=1,end_row=sr,end_column=ncols)
        ss.cell(sr,1,sens["title"]).font=Font(size=13,bold=True); sr+=1
        ra=sens["row_axis"]; ca=sens["column_axis"]
        ss.cell(sr,1,f"Rows: {ra['label']} ({display_unit(ra['unit'],ra.get('unit_label'))}) | Columns: {ca['label']} ({display_unit(ca['unit'],ca.get('unit_label'))}) | Output: {display_unit(sens['output_unit'],sens.get('output_unit_label'))}").font=Font(color="595959",size=9); sr+=1
        ss.cell(sr,1,f"{ra['label']} ({display_unit(ra['unit'],ra.get('unit_label'))})")
        for j,v in enumerate(ca["values"],2):
            cell=ss.cell(sr,j,v)
            axis_fmt=ca.get("format") or ("percent:1" if ca.get("unit")=="ratio" else None)
            if axis_fmt: cell.number_format=_excel_number_format(axis_fmt,data["currency"])
        for col in range(1,ncols+1):
            ss.cell(sr,col).fill=PatternFill("solid",fgColor="E7E6E6"); ss.cell(sr,col).font=Font(bold=True)
        sr+=1
        for rv,mrow in zip(ra["values"],sens["matrix"]):
            axis_cell=ss.cell(sr,1,rv)
            axis_fmt=ra.get("format") or ("percent:1" if ra.get("unit")=="ratio" else None)
            if axis_fmt: axis_cell.number_format=_excel_number_format(axis_fmt,data["currency"])
            for j,val in enumerate(mrow,2):
                cell=ss.cell(sr,j,"-" if val is None else val)
                cell.number_format=_excel_number_format(sens["format"],data["currency"])
                cell.alignment=Alignment(horizontal="right")
                cell.font=Font(color="777777" if val is None else "000000")
                cell.border=Border(bottom=thin)
            sr+=1
        ss.cell(sr,1,f"Output unit: {sens['output_unit']}").font=Font(color="595959",italic=True,size=9); sr+=1
        for assumption in sens.get("fixed_assumptions",[]):
            ss.cell(sr,1,"Fixed assumption: "+assumption).font=Font(color="595959",size=9); sr+=1
        if sens.get("footnote"):
            ss.cell(sr,1,"Note: "+sens["footnote"]).font=Font(color="595959",size=9); sr+=1
        sr+=2
    ss.column_dimensions["A"].width=45
    for col in range(2,30): ss.column_dimensions[get_column_letter(col)].width=18
    ss.freeze_panes="B4"; ss.sheet_view.showGridLines=False

    audit=wb.create_sheet("Audit")
    audit_headers=["Row ID","Label","Role","Unit","Format","Scenario ID","Value","Status","Expression","Evidence","Comment","Excel cell"]
    audit.append(audit_headers)
    for cell in audit[1]: cell.font=Font(bold=True,color="1A1A1A"); cell.fill=PatternFill("solid",fgColor="E7E6E6")
    for row in data["rows"]:
        for sc in scs:
            sid=sc["id"]; rownum=row_excel[row["id"]]; col=sc_col[sid]
            value=visible_value(row,sid)
            cell_ref=f"Bridge!{get_column_letter(col)}{rownum}"
            audit.append([row["id"],row["label"],row["role"],row["unit"],row["format"],sid,value if value is not None else "-",row.get("status",{}).get(sid,"missing" if value is None else "input"),row.get("expression",""),row.get("evidence",""),row.get("comment",""),cell_ref])
    for col,width in enumerate([24,36,16,16,14,14,20,14,36,36,36,20],1): audit.column_dimensions[get_column_letter(col)].width=width
    audit.freeze_panes="A2"; audit.auto_filter.ref=audit.dimensions; audit.sheet_view.showGridLines=False
    audit_diagnostics=_diagnostics(data)+diagnostics
    if audit_diagnostics:
        audit.append([]); audit.append(["Diagnostics"])
        for item in audit_diagnostics: audit.append([item])
    source_ws=wb.create_sheet("Sources")
    source_ws.append(["Source ID","Citation","PDF page","URL","Source hash"])
    for cell in source_ws[1]:
        cell.font=Font(bold=True,color="1A1A1A"); cell.fill=PatternFill("solid",fgColor="E7E6E6")
    for src in data.get("sources",[]) or []:
        source_ws.append([src.get("id",""),src.get("citation",""),src.get("pdf_page",""),src.get("url",""),src.get("source_hash","")])
    source_ws.append([]); source_ws.append(["Footnotes"])
    for note in data.get("footnotes",[]) or []: source_ws.append([note])
    source_ws.append([]); source_ws.append(["Diagnostics"])
    for note in _diagnostics(data)+diagnostics: source_ws.append([note])
    for col,width in enumerate([20,58,12,58,68],1): source_ws.column_dimensions[get_column_letter(col)].width=width
    source_ws.freeze_panes="A2"; source_ws.auto_filter.ref=f"A1:E{max(1,1+len(data.get('sources',[]) or []))}"; source_ws.sheet_view.showGridLines=False
    wb.calculation.fullCalcOnLoad=True
    wb.calculation.forceFullCalc=True
    wb.calculation.calcMode="auto"
    outpath.parent.mkdir(parents=True,exist_ok=True)
    wb.save(outpath)


def render(data: dict[str, Any], outdir: Path) -> list[Path]:
    errors=validate(data)
    if errors: raise InputError("Invalid input:\n- "+"\n- ".join(errors))
    outdir.mkdir(parents=True,exist_ok=True)
    paths=[]
    html_path=outdir/"exhibits.html"; html_path.write_text(render_html(data),encoding="utf-8"); paths.append(html_path)
    xml_path=outdir/"exhibits.xml"; xml_path.write_text(render_xml(data),encoding="utf-8"); ET.parse(xml_path); paths.append(xml_path)
    xlsx_path=outdir/"exhibits.xlsx"; render_xlsx(data,xlsx_path); paths.append(xlsx_path)
    feishu_path=outdir/"feishu.xml"; feishu_path.write_text(render_feishu_xml(data),encoding="utf-8"); paths.append(feishu_path)
    paths.extend(render_pngs(data,outdir))
    return paths


def main(argv: list[str] | None = None) -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",required=True,type=Path)
    parser.add_argument("--outdir",required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        data=json.loads(args.input.read_text(encoding="utf-8"))
        paths=render(data,args.outdir)
    except (OSError,json.JSONDecodeError,InputError) as exc:
        print(f"render_exhibits: {exc}",file=sys.stderr)
        return 2
    for path in paths: print(path)
    for item in dict.fromkeys(_diagnostics(data)): print("DIAGNOSTIC: "+item,file=sys.stderr)
    return 0


if __name__=="__main__":
    raise SystemExit(main())
