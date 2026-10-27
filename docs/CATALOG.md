# FinSkillHub Skill Catalog

This catalog covers three original finance research skills published in this repository. It is a free discovery and documentation resource, not an execution certification or investment recommendation.

## Included skills

| Skill | What it does | Inputs and outputs | Dependencies | Status |
| --- | --- | --- | --- | --- |
| [incremental-business-sizing](../skills/incremental-business-sizing/SKILL.md) | Builds a traceable revenue, operating profit, net income, and EPS bridge for a new business or capacity opportunity. | Sourced assumptions in JSON; scenario results, sensitivity matrix, and missing-input diagnostics. | Python standard library. | Arithmetic and interface checks are distinct from source quality, accounting review, and investment judgment. |
| [sellside-model-exhibits](../skills/sellside-model-exhibits/SKILL.md) | Turns completed model results into calculation bridges, scenario tables, and two-variable sensitivities. | A results JSON; HTML, PNG, Excel, audit XML, and document-ready table fragments. | Python, Pillow, openpyxl, and a font that supports the requested characters. | Generated workbooks request recalculation on open; Microsoft Excel recalculation and target-document rendering are not verified here. |
| [pre-trade-quick-brief](../skills/pre-trade-quick-brief/SKILL.md) | Organizes a first-pass stock review around changed expectations, earnings capacity, forward valuation, and disconfirming evidence. | A company or theme, dated market context, prior expectations, and authorized source material; a concise brief with evidence gaps and next checks. | A host that can read custom `SKILL.md` packages; evidence sources are supplied by the user. | Does not authorize trading, paid research, or external communication. |

All examples included with these packages use synthetic data. See each skill's `SKILL.md` for its method and usage, and its `LICENSE` for terms.

## Provenance and version links

The three packages were taken from the FinSkillHub source repository at commit `2f669885632141f367de7bb5e4d0b951f72cfe2d`. This is the recorded source revision, not a commit in this catalog repository. The exact original revision links are recorded in [`data/catalog.json`](../data/catalog.json) for provenance. Package paths in this repository are the public copies.

The original repository may later become private. After this repository has its first commit, maintainers should add commit-pinned links into this repository for each skill and license, while retaining the original source commit as provenance. Do not replace the recorded upstream revision with a newly generated hash or describe a mirror commit as the original source commit.

## License boundaries

The root [MIT License](../LICENSE) covers this repository's original catalog and documentation. Each included skill carries its own MIT license. The root license does not change the skill package terms or grant rights to external dependencies, data, trademarks, or works linked from the catalog.

## Limitations

- Listing and source review do not establish that a host discovers or correctly executes a skill.
- Generated files do not establish that Excel or a target document editor recalculates or renders them correctly.
- Users remain responsible for source verification, permissions, dependencies, financial assumptions, and any decisions based on outputs.
