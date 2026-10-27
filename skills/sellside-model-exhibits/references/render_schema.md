# 渲染器输入接口

`scripts/render_exhibits.py` 接收一个 JSON 对象，版本必须是 `incremental-sizing-results/v1`。运行方式：

```bash
python3 scripts/render_exhibits.py --input results.json --outdir rendered
```

输入文件只读。成功时会生成 `exhibits.html`、`exhibits.xml`、`feishu.xml`、`exhibits.xlsx`、`bridge.png`，以及每个敏感性表对应的 `sensitivity_<id>.png`。

脚本依赖 Pillow 绘制 PNG、openpyxl 写入工作簿。当前本机验收运行时已提供这两个库；其他环境需安装相应依赖，并安装可用的中文字体。渲染器会优先寻找 PingFang、Heiti、Arial Unicode；字体缺失时会回退系统默认字体，中文效果可能变差。

## 顶层字段

```json
{
  "schema_version": "incremental-sizing-results/v1",
  "title": "合成业务收入与 EPS 情景",
  "period": "FY2027E",
  "currency": "USD",
  "scenarios": [{"id": "A", "label": "A 情景"}],
  "rows": [],
  "sensitivities": [],
  "footnotes": [],
  "sources": [],
  "diagnostics": []
}
```

`scenarios` 的 ID 必须唯一。ID 用来索引每一行的 `values` 和 `status`。标签是展示文字，可写年份、A/B/C 或其他清楚的情景名。

## 测算行

每个 `rows` 项使用以下字段：

```json
{
  "id": "incremental_revenue",
  "label": "增量收入",
  "unit": "USD",
  "format": "money:1",
  "role": "calculation",
  "key": true,
  "indent": 0,
  "unit_label": "百万美元",
  "values": {"A": 1200, "B": 2000, "C": 3000},
  "expression": "capacity * price",
  "comment": "数量乘同期价格；原创合成示例",
  "evidence": "Original synthetic example",
  "status": {"A": "computed", "B": "computed", "C": "computed"}
}
```

`id` 在整张桥里唯一。表达式引用行 ID。普通 ID 使用 Python 标识符格式，例如 `incremental_revenue`；若 ID 含连字符或其他字符，可写 `row("capacity-sold")` 或 `ref("capacity-sold")`。

Excel 只翻译数字常量、加减乘除、幂、正负号，以及 `min()`、`max()`、`abs()`。表达式不能使用属性访问、索引、导入、任意函数、比较或逻辑表达式。`min/max` 转成 Excel 的 `MIN/MAX`，`abs` 转成 `ABS`。引用行的值缺失或被阻断时，公式不生成，并在诊断中说明原因。循环引用也不会写入工作簿。

`values` 保存原始数值。`unit` 是输入值的实际口径；渲染器不缩放数值，但会把常见代码（如 `USD_mn`、`USD_bn`、`users_mn`、`ratio`）转换为“百万美元”“十亿美元”“百万人”“%”等读者标签。可用 `unit_label` 指定单行展示标签。数值 `null`、状态 `missing` 或 `blocked` 都显示 ASCII `-`，不会用零替代。

可用 `role`：

- `input`：情景假设。
- `calculation`：测算结果。提供表达式且状态为 `computed` 时，Excel 写入公式；没有表达式时按快照值输出并诊断。
- `reported`：原文直接报告的数值。保留输入值，不重算；工作簿批注标注原报告值。
- `benchmark`：共识、基准或历史对照值。
- `section`：分块标题行。
- `note`：文字说明行。

每个情景的状态取值为 `input`、`computed`、`reported`、`missing`、`blocked`。输入假设和原报告硬填值用红色字体；`benchmark` 用蓝色；Excel 公式和计算快照用黑色。公式由渲染器生成，不会把 `reported` 状态改成公式。

支持的 `format`：

| 格式 | 示例 | 约定 |
|---|---|---|
| `integer` | `1463` | 千分位分隔、无小数 |
| `decimal:N` | `decimal:2` | 固定 N 位小数 |
| `percent:N` | `percent:1` | 输入为比例值，如 `0.126` 显示 `12.6%` |
| `money:N` | `money:1` | 按顶层币种加货币符号，不改变数值尺度 |
| `eps:N` | `eps:2` | 按顶层币种加货币符号并保留每股小数位 |

## 敏感性表

```json
{
  "id": "revenue",
  "title": "数量与价格对收入的影响",
  "row_axis": {"label": "数量", "unit": "units", "values": [80, 100, 120]},
  "column_axis": {"label": "价格", "unit": "USD/unit", "values": [15, 20, 25]},
  "matrix": [[1200, 1600, 2000], [1500, 2000, 2500], [1800, 2400, 3000]],
  "output_unit": "USD",
  "format": "money:1",
  "fixed_assumptions": ["其他输入固定"],
  "footnote": "原创合成示例"
}
```

矩阵行数必须等于 `row_axis.values` 长度，每行列数必须等于 `column_axis.values` 长度。轴可选 `format` 和 `unit_label`；未提供格式时，`ratio` 轴默认按百分比显示，其他数值轴保留紧凑的小数位。矩阵里的 `null` 保持未知并显示 `-`。敏感性表不增加空白的结果单位列，输出单位放在标题副题或图注。固定假设和脚注会出现在 HTML、XML、XLSX 和 PNG。

## 来源与诊断

`sources` 项至少包含 `id` 和 `citation`，并可带 `pdf_page`、`url`、`source_hash`。若 URL 含签名参数或访问令牌，应在上游去除，改用非签名定位信息。`footnotes` 和 `diagnostics` 是字符串数组。人读脚注保留在 PNG；机器诊断不放进 PNG，HTML 使用可展开详情，XML 放入 diagnostics appendix，XLSX 的 `Audit` 与 `Sources` 保留完整诊断。渲染器不会验证外部来源、计算口径或卖方模型正确性。

桥表的 `Comment` 列相邻展示每行 `comment` 和 `evidence`，长文本可换行；`reported` 且 `key: true` 的结果会注明其为原报告关键结果。

## XML 输出

`exhibits.xml` 是带 `<exhibits>` 根节点的良构审计 XML，保留原始数值、显示值、单位代码、状态、敏感性轴、固定假设、脚注、来源和完整诊断。它供机器审计，不直接导入飞书正文。

`feishu.xml` 是由飞书支持标签组成的 XML 文档片段，直接供 `lark-cli docs +update --content @feishu.xml` 插入。片段只含标题、段落、原生表格、列定义及行内加粗；桥表含单位和相邻 Comment 列，敏感性表含两轴、结果单位、固定假设，文末保留人读来源和脚注。技术诊断不进入 `feishu.xml` 正文。本机 `lark-cli docs +script --command parse --content @feishu.xml --format json` 可验证片段结构；更新云文档后仍需回读核验。
