# SPEC-RESEARCH-PROVENANCE 研究段产物的溯源字段

- 版本：v0.2（R3 定稿：字段填值规则 + 渲染格式 + 审计口径）
- 日期：2026-10-31
- 归属：后端架构
- 实现：`src/ladderstock/research/artifacts.py`（`PROVENANCE_KEYS` / `provenance_slot` / `merge_slots` / `build_document`）、
  `src/ladderstock/research/citation.py`（渲染与审计）、`src/ladderstock/core/boundary.py`（`Provenance` / `assert_provenance`）
- 关联：`DEC-017`（研究管线骨架）、`DEC-024`（R1-3 后三段 0 次取数）、`DEC-027`（本版本的来源）

## 1. 顶层形状（v0.1 定稿，v0.2 未改）

每个 `<stage>.json` 顶层带 `provenance` 对象，三个键一个都不能少：

```json
{"values": {"<指标键>": {"sources": ["S1"], "unverified": false}},
 "sources": [{"id": "S1", "dataset": "", "params": {}, "retrieved_at": ""}],
 "unverified": false}
```

- `values`：程序计算的指标键 → 该数值的 `sources[]` 引用位 + `unverified` 布尔位。数值本身只有一份，落在 `facts`。
- `sources`：整段共用的有序引用列表，**列表序号就是人读件里的角标 `[n]`**（n 从 1 起）。
- `unverified`：整段兜底布尔位。`false` 的条件比看起来严：本段每个数值字段都得有出处，且每条出处 `verified=true`。

## 2. `sources[]` 条目字段（v0.2 新增后半组）

| 字段 | 类型 | 规则 |
| --- | --- | --- |
| `id` | `S<n>` | `<n>` = 条目在 `sources[]` 里的序号，与角标一致 |
| `dataset` | `string` | 出处族，必须命中 `PROVENANCE_SOURCES`：`tickhub-cn` / `tickhub-us` / `wire` / `senate` / `computed` / `user` / `web` |
| `params` | `object` | `metric`（归属指标键）、`stage`（产出该条的段）；`report` 段并集时另带 `upstream_id` 指回原段的条目号 |
| `retrieved_at` | `string` | 取用时刻（`fetched_at`） |
| `value` / `unit` | `number\|string` / `string` | 出处记录的原始值与计量单位；审计据此判「数字与来源值不符」 |
| `endpoint` | `string\|null` | peer 侧落点，peer 来源必填 |
| `as_of` | `string` | 该数值的时效边界（「截至什么时候有效」） |
| `url` | `string\|null` | 非 peer 来源的落点 |
| `verified` | `bool` | `true` 才允许被角标引用；`web` 来源永远 `false`（`DEC-027` §2） |

## 3. 渲染（`<stage>.md`）

- 小节顺序：标题 → 元信息 → `## 叙述` → `## 指标` → `## 备注` → `## 来源`。
- 每个数值 token 后面必须跟 `[n]`（有已核实出处）或纯文本 `〔未核实〕`（其余）；角标由**程序**补，提示词明确禁止模型自己写引用。
- 角标落在单位整体之后：`37.2%[2]`，不是 `37.2[2]%`。
- 已有标记的 token 不重复处理，所以渲染幂等：`annotate(annotate(x)) == annotate(x)`。
- `## 来源` 每行格式：`[n] dataset · endpoint · as_of · url`，未核实的条目行尾追加 `〔未核实〕`；本段没有任何来源时写「（本段无引用来源；正文中的数字一律标〔未核实〕。）」，此时 `## 指标` 一节仍逐条列出程序指标并挂标记。

## 4. 豁免名单（不要求角标的数字形态）

`citation.EXEMPTIONS` 是显式一份，逐条带理由：角标自身、日期、时刻、封板时间（`HHMMSS` 原样串，`METRICS.md` §4.1）、证券代码、行首与括注序号、序数位次、百分位记号、版本号与决策号（`v2.1` / `R3` / `DEC-027` / `QUOT-141` / `§4.1`）。产物自己的证券代码由调用方以 `identifiers` 显式传入，不靠正则猜。新增豁免要能说明它为什么不是指标。

## 5. 审计（`ldst research <code> [--only <stage>] --check`）

只读已落盘的两件产物，不调模型、不取数、不需要 `config/ai.yaml`。四类不合格数字：无角标的数字、角标指向不存在的来源、数字与角标记录的来源值不符、角标指向未核实的来源；结构化件另有自洽检查（`values` 回指的来源必须存在、声称已核实必须真有人给出处、没有出处的数值字段不许把整件标成已核实）。退出码：通过 `0`、产物不存在 `6`、有不合格数字 `8`。`audited` 与 `missing` 两个分支的载荷键集合一致。

## 变更记录

- v0.2（2026-10-31，QUOT-141 / `DEC-027`）：`sources[]` 条目补 `value`/`unit`/`endpoint`/`as_of`/`url`/`verified` 六个引用要素；`provenance_slot()` 开始填值（程序写入，模型写不进去）；定稿 Markdown 渲染格式、豁免名单与 `--check` 审计口径；`unverified` 的判定改为「本段每个数值字段都得有出处」。
- v0.1（2026-10-31，QUOT-192）：字段名与嵌套形状定稿，R1-3 只保证键存在、不渲染。
