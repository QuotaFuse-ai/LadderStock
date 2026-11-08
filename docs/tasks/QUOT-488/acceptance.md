# QUOT-488 验收证据（8 项）

执行环境：本机（Windows + WSL Ubuntu-24.04 docker），2026-11-09。
档案层数据区 `D:\QuotaFuseModules\LadderStock\data\knowledge\`；
验证实例：本 PR 代码 uvicorn 起于 127.0.0.1:5971（验证后已停）。

## 验收 1：真实档案 + `GET /api/knowledge/SH:600000`

数据来源：TickHub-CN MCP 真实调用（`fetch_600000.py` 走 LadderStock 自带
`HttpMcpTransport`，与产品同一条取数链路），tools/list 返回 150 个工具，实际调用：

- `tencent_quote_batch`（sh600000）→ 收盘 10.03 / 前收 9.87 / +1.62% / PE 6.45 / PB 0.44 /
  总市值 3340.58 亿（source=tencent, confidence=A, trace_id=a9581b29e56f48de,
  行情时间戳 20261106161448）
- `tencent_kline`（daily/qfq/60）→ 2026-08-07 ~ 2026-11-06 共 60 根
  （source=tencent, confidence=A, trace_id=821f25f631434d8e）
- `eastmoney_finance_indicators`（600000）→ 2026三季报 EPS 1.17 / BPS 22.8 /
  营收 1381.33 亿 / 净利 405.72 亿 / ROE 5.2% / 营收同比 +4.42% / 净利同比 +4.52%
  （source=eastmoney, confidence=B, trace_id=59f2b84ddd54402e）
- `baostock_valuation_history` → 失败（QFSD-9002: baostock is not installed，本机
  缺依赖），档案里如实记录「估值历史缺位」，未编数。

`build_dossier_600000.py` 将上述真实数字逐项转录进档案（正文每节标注来源工具/
source/trace_id/抓取时间 2026-11-09T05:37:29+08:00），落盘
`knowledge/SH_600000/{profile.md, latest.md, runs/run-20261109-600000.md}` + manifest。

```
GET /api/knowledge/SH/600000?include_recalled=true → 200
{"market":"SH","symbol":"600000","name":"浦发银行","as_of":"2026-11-09",
 "status":"fresh","valid_days":90,"body":"# 浦发银行（SH:600000）研究档案…
 - 收盘价：10.03 元（前收 9.87 元，涨跌幅 +1.62%）… EPS 1.17 元 … 营业收入 1381.33 亿元…",
 "recalled":{"status":"fresh","truncated":false,…}}
```

注：`sources` 结构化清单由 frontmatter 承载（flow list），读路径 `dossier_from_text`
当前不反解，接口照实返回 `[]`——单测同口径，不在本单扩范围。

## 验收 2：valid_days 过期 → stale；refuted 不召回

2a. 把 profile.md / latest.md 的 `as_of` 临时改为 2026-07-31（距当日 2026-11-09
100 天 > valid_days 90）：

```
GET → {"as_of":"2026-07-31","status":"stale","valid_days":90}
recalled.status = stale     # stale 仍召回，状态随行，由注入方声明数据可能过期
```

验证后已恢复 `as_of: 2026-11-09`（status 回 fresh）。

2b. `record_refutation()` 显式反证后：

```
GET → {"as_of":"2026-11-09","status":"refuted"}
recalled = None             # refuted 档案不召回
```

## 验收 3：超长档案截断到 12000 字，保章节结构

SH:999999 占位档案（正文明确标注「截断演示用占位档案，不含真实市场数据」，10 章
29683 字），调 `recall()`：

```
原文正文字符数: 29683
召回文本字符数: 12000  truncated=True
保留的章节头: ['## 演示章节 1' … '## 演示章节 5']   # 整节装不下丢节
尾部 40 字: …'5-1：本档案为召回截断演示用占位档案（QU\n…(本章已截断，完整档案见文件)\n'
```

演示后临时档案已删除（目录与 manifest 条目均已清理）。

## 验收 4：冲突裁决（实时优先 + 反证记录 + 旧结论保留）

对 SH:600000 调 `record_refutation(old_conclusion="收盘价 10.03 元（2026-11-06）",
realtime_value="实时快照价 10.55 元（模拟下一交易日实时源，裁决演示）", evidence=…)`：

```
profile.md:41
| 2026-11-09 | 实时数据推翻旧结论：收盘价 10.03 元（2026-11-06） → 现为 实时快照价 10.55 元
（模拟下一交易日实时源，裁决演示） | 裁决演示：实时行情源优先于档案快照（QUOT-488 验收 4） | 90 | refuted |
```

- 反证行插入既有「历史结论」表（不另起一节、不删旧行）；
- 档案级 `status: refuted`（frontmatter），`as_of` 更新为裁决日；
- 表内原有两条 fresh 结论原样保留。
- 验证后档案已恢复为验收 1 原状（status=fresh）。

## 验收 5：失败 run 归档不产生文件（目录对比）

临时 data_root 内直接调管线归档入口 `archive_run()`：

```
failed run（failed=["report"]）    -> archived=False event=archive.skipped
no-report run（generated=["signal"]）-> archived=False event=archive.skipped
归档后目录: SH_600000 目录不存在（无任何文件生成）

successful run（generated=["report"]）-> archived=True event=archive.completed
归档后目录: ['knowledge\\SH_600000\\latest.md', 'knowledge\\SH_600000\\runs\\run-ok-demo.md']
```

## 验收 6：切库（只改配置，不改代码）

第二实例 `qf-qdrant-alt`（同镜像 v1.20.0，主机 6335，与 mcp 同 compose 网络），
灌同一份 `points.json`（6 点）后：

```
1) 停主实例：qf-qdrant → Exited；主端口探测 http://127.0.0.1:6333 → 000 DOWN
2) .env 只改一行：QDRANT_URL=http://qdrant:6333 → http://qf-qdrant-alt:6333
3) docker compose up -d --no-deps mcp-server-qdrant（重启 mcp 容器，代码零改动）
4) MCP qdrant-find 三问法全部命中：
   「浦发银行的分红政策怎么样」      → top1 pufa_dividend
   「资产质量有风险吗，不良贷款情况如何」→ top1 pufa_asset_quality
   「零售业务转型进展如何」          → top1 pufa_retail
5) 恢复 .env → 起回主实例 → 重启 mcp → 检索正常（points_count=6）→ 删除 alt
```

主实例停机状态下检索仍工作 = 切库真实生效（不是主实例在服务）。

## 验收 7：Qdrant 与 mem0 的 pgvector 存储独立

```
docker volume inspect（WSL docker）
qf-qdrant_qf_qdrant_storage   driver=local  mountpoint=/var/lib/docker/volumes/qf-qdrant_qf_qdrant_storage/_data
qf-mem0_qf_mem0_postgres      driver=local  mountpoint=/var/lib/docker/volumes/qf-mem0_qf_mem0_postgres/_data

docker inspect 挂载
/qf-qdrant        | volume:qf-qdrant_qf_qdrant_storage => /qdrant/storage
/qf-mem0-postgres | volume:qf-mem0_qf_mem0_postgres   => /var/lib/postgresql/data
```

引擎不同（Qdrant 1.20.0 vs postgres pgvector）、卷不同、容器不同、compose project
不同（qf-qdrant vs qf-mem0）。未触碰 mem0 任何容器/数据/配置/端口。

## 验收 8：非法输入全部被拒（状态码）

```
GET /api/knowledge/XX/600000              → 400（market 非法枚举，SH|SZ|BJ 之外）
GET /api/knowledge/SH/abc123              → 400（symbol 只放行 6 位数字）
GET /api/knowledge/SH/../../etc/passwd    → 404（--path-as-is，路由层未放行）
GET /api/knowledge/SH/%2e%2e%2f…passwd    → 404（URL 编码穿越）
GET /api/knowledge/SH/..%5C..%5Cetc       → 400（反斜杠被 normalize_symbol 拒绝）
GET /api/knowledge/SH/999999              → 404（合法但不存在的档案，不伪造）
Host: evil.example.com                    → 403（Host 白名单）
GET /api/knowledge/sh/600000              → 200（market 大小写宽容，同口径）
```

路径层双保险：`normalize_symbol` 词法拒绝 + `safe_path()`（resolve 前缀比对 +
逐级禁符号链接 + realpath 复核）。

## 回归

- `tests/unit/knowledge`：40 passed, 1 skipped。
- 全量单测（不带 -x）：除 2 个基线既有失败
  （`test_cli_output_option.py::test_output_json_is_never_a_usage_error[ai chain test --output json-]`、
  `test_cli_surface.py::test_command_registered_and_exits_as_baseline[argv35-expected35]`，
  在本 PR 分支之前的 main 上已失败，与本单无关）外全部通过。
- ruff / mypy：changed files 全部通过。
