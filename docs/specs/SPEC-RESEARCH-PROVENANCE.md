# SPEC-RESEARCH-PROVENANCE 研究段产物的溯源字段位

- 版本：v0.1（字段位定稿，渲染格式待 R3）
- 日期：2026-10-31
- 归属：后端架构
- 实现：`src/ladderstock/research/artifacts.py`（`PROVENANCE_KEYS` / `build_document`）
- 关联：`DEC-017`（研究管线骨架）、`DEC-024`（R1-3 后三段）

## 1. 说明（10 行以内）

每个 `<stage>.json` 顶层带 `provenance` 对象，三个键一个都不能少，形状固定：

```json
{"values": {"<指标键>": {"sources": [], "unverified": true}},
 "sources": [{"id": "", "dataset": "", "params": {}, "retrieved_at": ""}],
 "unverified": true}
```

- `values`：程序计算的指标键 → 该数值的 `sources[]` 引用位 + `unverified` 布尔位。
- `sources`：整段共用的有序引用列表，`values[*].sources` 引用它的 `id`。
- `unverified`：整段兜底布尔位；没有引用来源一律 `true`，绝不当已核实。

R3 定稿前只留字段不渲染：`values` 恒 `{}`、`sources` 恒 `[]`、`unverified` 恒 `true`。
`report` 段不新增取数，其溯源只指回 `facts.upstream.<stage>.provenance`，不复制数值。

## 2. 变更记录

- v0.1（2026-10-31，QUOT-192）：字段名与嵌套形状定稿，R1-3 只保证键存在、不渲染。
