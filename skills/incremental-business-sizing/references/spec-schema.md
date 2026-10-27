# Incremental sizing JSON interface

The `size_opportunity.py` helper evaluates a scenario model from a JSON spec and writes `incremental-sizing-results/v1`. It is a numeric engine only: it does not validate source quality, accounting treatment, forecast timing, valuation suitability, or whether a result is decision-ready.

## Required top-level fields

```json
{
  "schema_version": "incremental-sizing-spec/v1",
  "title": "Illustrative incremental revenue",
  "period": "FY2028E",
  "currency": "USD",
  "scenarios": [{"id": "base", "label": "Base"}],
  "rows": []
}
```

`scenarios` is a non-empty array of unique identifier/label pairs. Scenario IDs and row IDs use letters, digits, and underscores, and start with a letter or underscore. `rows` is an ordered array; order is retained in results for display, while calculations may reference rows that appear later.

## Row fields

Each row requires `id`, `label`, `role`, `unit`, and `format`. Optional display/source fields such as `key`, `indent`, `comment`, `evidence`, `min`, and `max` pass through to the result. `min` and `max` constrain supplied or calculated values; an out-of-range value is blocked and reported in diagnostics.

| Role | Numeric input | Result behavior |
|---|---|---|
| `input`, `benchmark` | `value` or per-scenario `values` | Preserved as input; either may feed calculations. |
| `calculation` | `expression` | Recomputed for each scenario from the dependency graph. |
| `reported` | `value` or `values` | Preserved as reported source data; never replaced by a reconstructed bridge. |
| `section`, `note` | None | Display-only row, with null values. |

Use `value` for a scalar shared across scenarios, or `values` for scenario-specific numbers/nulls. A scenario-specific entry takes precedence over `value`; an omitted scenario falls back to `value`. `null` means unknown, never zero. Required source rows without a numeric value produce `missing` status.

Example row:

```json
{"id":"unit_price","label":"Synthetic unit price","role":"input","unit":"USD/unit","format":"money:2","values":{"base":12,"upside":18},"evidence":"House assumption","min":0}
```

## Calculation expressions

Expressions use row IDs as names. The safe evaluator permits numeric constants, parentheses, unary `+`/`-`, `+`, `-`, `*`, `/`, and calls to `min`, `max`, and `abs`. It rejects attributes, indexing, comprehensions, exponentiation, imports, other calls, and unknown names. Dependency cycles and unsafe syntax are structural errors: the CLI exits non-zero and does not write a successful result.

Example: `units_sold * unit_price`. A missing or blocked dependency makes the calculation blocked. Division by zero, a non-finite result, and a violated bound are also blocked with a diagnostic. The engine retains full floating-point precision through dependent calculations and does not pre-round outputs.

## Sensitivity tables

Each item in optional `sensitivities` has `id`, `title`, `scenario_id`, `row_axis`, `column_axis`, `output_id`, `format`, `output_unit`, `fixed_assumptions`, and `footnote`. An axis has `row_id`, `label`, `unit`, and a non-empty list of numeric values. Axes must refer to two different `input` or `benchmark` rows. The helper substitutes each axis pair into the selected scenario and evaluates the same dependency graph used for the base case. The returned sensitivity preserves the axis definitions and adds `matrix`, whose rows follow `row_axis.values` and columns follow `column_axis.values`; unavailable calculations are null.

## Completeness and source reconciliation

`required_results` is an optional array of row IDs that must have numeric values in every scenario. Missing required outputs set result-level `status` to `incomplete` and add diagnostics; partial rows remain available. Other cases return `complete` even when optional rows are missing, with row statuses and diagnostics showing the gap.

`report_reconciliations` can compare a calculated row with a separately reported number without changing the calculated result:

```json
{"row_id":"incremental_revenue","reported_values":{"base":2000},"tolerance_abs":0.05,"label":"Published rounded estimate"}
```

The helper adds a diagnostic when the absolute difference exceeds `tolerance_abs`. It does not overwrite either input or calculated value. Top-level `footnotes` and `diagnostics` are text arrays; `sources` is an array of objects and is passed through unchanged, including any `citation`, `pdf_page`, or evidence metadata.

## Output

Run:

```sh
python3 scripts/size_opportunity.py --input spec.json --out results.json
```

Output uses `schema_version: "incremental-sizing-results/v1"`, carries through the model metadata and source/footnote arrays, and returns each row with per-scenario `values` and `status`. Status values are `input`, `computed`, `reported`, `missing`, or `blocked`. This output is a reproducible arithmetic aid, not an investment recommendation or accounting conclusion.
