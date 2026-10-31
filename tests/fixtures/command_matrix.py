"""集中定义 31 条基线命令的可执行形态，供 CLI 与契约测试共用。

每行 (argv, expect) 中的 expect 是当前期望退出码：
- 0  已实现且执行成功
- 3  配置/端口问题
- 4  数据源不可用（peer 全挂且无本地缓存）
- 6  无结果（没有落盘产物，或核心指标 `available=false`）
- 8  部分数据集失败（有成功的、也有失败的）

（施工期占位码 70 已退役：31 条基线命令全部有实现，见 docs/CLI.md §F。）

R1-1（QUOT-190）在基线之外新增 1 条命令 `ldst research <code>`：基线 31 条 /
33 叶子 / 32 argv 的口径不变，加上它之后是 **34 个叶子路径、33 条可执行 argv**。
R6（QUOT-144）再新增 2 条：`ldst news <date>` / `ldst events --month YYYY-MM`，
合计 **36 个叶子路径、35 条可执行 argv**。
"""

from __future__ import annotations

DATE = "2026-10-22"
MONTH = "2026-11"

#: `weekly` 的固定周标签（`DATE` 落在 2026-W43，这里取下一周以免与复盘产物纠缠）
WEEK = "2026-W44"

COMMAND_MATRIX: tuple[tuple[tuple[str, ...], set[int]], ...] = (
    # 启动与复盘
    # `run` 是长驻进程（会阻塞到 Ctrl+C），不放进矩阵：
    # 端口被占用 -> 3 由 tests/integration/test_journal_privacy.py 覆盖，
    # 服务面（只读 API / 静态页）由同一文件的 create_app 用例覆盖。
    #
    # M3 已实现：离线（无 AI 凭据）时复盘按纯文本降级 -> 9；配好模型则 0。
    # review show / validate 没有落盘结果 -> 6（先跑 review 才有条件）。
    # reconcile 无验证文档 -> 6，有文档但缺下一交易日数据 -> 8（partial）。
    # sentiment / ladder 是程序直出：有数据 0；数据不可用（`available=false`）时
    # 在 stderr 说明原因后以 6（DEC-003「6 = 无结果」）退出（DEC-012）。
    (("review", "--date", DATE), {0, 9}),
    (("review", "show", "--date", DATE), {6}),
    # R11 周度复盘（QUOT-149，基线之外的增量，见 docs/CLI.md §A′）：矩阵跑在临时数据目录，
    # 那一周没有任何每日产物与缓存 → 事实包 available=false → 6（产物仍落盘，要求 4）。
    # 有数据的场景由 tests/unit/weekly/ 覆盖（0 / 9）。
    (("weekly", "--week", WEEK), {6}),
    (("validate", "--date", DATE), {6}),
    (("validate", "reconcile", "--date", DATE), {6, 8}),
    (("sentiment", "show", "--date", DATE), {0, 6}),
    (("ladder", "show", "--date", DATE), {0, 6}),
    # export 已实现（M1）：有复盘 0，窗口内无复盘 6
    (("export", "--format", "md", "--date", DATE), {0, 6}),
    (("state", "inspect"), {0}),
    # 数据层（M2 已实现）：peer 全挂且无缓存 4，部分数据集失败 8；
    # status/list/health 是只读视图，始终 0；validate 探测不通 4、主备违规 3。
    (("sync",), {4, 8}),
    (("sync", "--dataset", "daily_k"), {4, 8}),
    (("sync", "--full"), {4, 8}),
    (("sync", "status"), {0}),
    (("sources", "list"), {0}),
    (("sources", "validate"), {0, 3, 4}),
    (("sources", "health"), {0}),
    # 盘中与提醒（M4 已实现）：watch start 是离线单轮扫描——本机没有分钟样本时为 4，
    # 有样本 0；status / stop 是只读/幂等视图始终 0；notify test 默认 console 渠道 -> 0。
    (("watch", "start"), {0, 4}),
    (("watch", "status"), {0}),
    (("watch", "stop"), {0}),
    (("notify", "test"), {0}),
    # 交易日志（M2 已实现）：只写本机临时数据目录，成功即 0
    (
        (
            "journal",
            "add",
            "--code",
            "600000",
            "--side",
            "buy",
            "--price",
            "10.1",
            "--qty",
            "100",
        ),
        {0},
    ),
    (("journal", "list"), {0}),
    (("journal", "stats"), {0}),
    # R7 候选池（QUOT-145）：程序直出，与 `ladder show` 同口径——缓存里有涨停池 0，
    # 一条候选都解析不出来（本机没同步）6。
    (("pool", "--date", DATE), {0, 6}),
    # R5 产业研究（QUOT-143）：`list` 是配置视图，清单本身永远可读 -> 0（指标缺就逐行显式 null）；
    # `show` 与 `pool`/`ladder show` 同口径——五维有任一字段算得出 0，全空 6（DEC-012）。
    (("industry", "list"), {0}),
    (("industry", "show", "食品饮料"), {0, 6}),
    # R7 我的持仓（QUOT-145）：只写本机临时数据目录。`add` 成功 0；
    # `list` 空库也照常出结构化结果 0（与 `journal list` 同口径）；
    # `update` / `remove` 作用在不存在的代码上 -> 6（无结果，DEC-012）。
    (
        (
            "position",
            "add",
            "--code",
            "600000",
            "--cost",
            "10.5",
            "--qty",
            "100",
        ),
        {0},
    ),
    (("position", "list"), {0}),
    (("position", "update", "--code", "600999", "--qty", "100"), {6}),
    (("position", "remove", "--code", "600999"), {6}),
    # AI（M3 已实现）：status 只读且未配置也能输出 0；retry 未配置按降级 9；
    # chain test 未配置凭据 -> 3（明确告知缺哪个环境变量，不拨网络）。
    (("ai", "status"), {0}),
    (("ai", "retry", "--date", DATE), {0, 9}),
    (("ai", "chain", "test"), {3}),
    # R1-1 新增（在 31 条基线之外，见 docs/CLI.md §0 的「R1-1 增量」行）：
    # 未配置模型凭据时六段按占位降级 -> 9；配好模型则 0。语义与 `ai retry` 同口径。
    (("research", "600000"), {0, 9}),
    # R6 资讯雷达（QUOT-144，同样在 31 条基线之外）：peer 全挂且无缓存 4；
    # 部分栏目成功部分失败 8；都取到 0。日历里「暂无数据源」的三个小节是口径缺口，
    # 不额外拖退出码（有源小节取到就 0），所以期望集合含 4 而不含 2。
    (("news", DATE), {0, 4, 8}),
    (("events", "--month", MONTH), {0, 4}),
    # MCP
    # `mcp stdio` / `mcp http` 是长驻服务（stdio 阻塞到客户端关闭、http 阻塞到
    # Ctrl+C），与 `run` 同口径不进矩阵：`mcp http` 端口占用 -> 3 见
    # tests/unit/test_cli_surface.py::test_mcp_http_refuses_occupied_port；
    # 工具清单与信封由 tests/contract/test_mcp_tools.py 覆盖。
    (("mcp", "tools", "list"), {0}),
    # 配置与调度
    (("config", "validate"), {0, 3}),
    # `config migrate` 已实现（M5）：仓库配置已是 v2 -> up_to_date，幂等且不写任何文件，
    # 所以默认形态也必须稳定 0；`--dry-run` 只出计划，同样 0。
    (("config", "migrate"), {0}),
    # 调度（M4 已实现）：list 只读；run --once 跑一轮即退出（任务失败只降级）-> 0
    (("schedule", "list"), {0}),
    (("schedule", "run", "--once"), {0}),
    (("state", "reset", "--store", "review_draft", "--yes"), {0}),
    # 体检
    (("doctor",), {0, 3}),
)


# --------------------------------------------------------------------------- #
# 全局 `--output` 契约（docs/CLI.md §F）
# --------------------------------------------------------------------------- #
#
# 基线条目是 31 条（第 0 节口径），落到 typer 注册表上是 33 个叶子命令路径：
# `sync` 的三种形态（`sync` / `--dataset` / `--full`）共用一条路径，
# `validate reconcile` 计数在 `validate` 之外——所以这里按「可执行路径」列。
# `review` / `validate` / `sync` 三个组带 `invoke_without_command`，组本身也是叶子。
# R7（QUOT-145）在此之外加了 5 条：`pool` + `position add|list|update|remove`，
# 共 39 个叶子路径（含 R1-1 的 `research`）；R5（QUOT-143）再加 2 条
# `industry list|show` → 41 个叶子路径；R11（QUOT-149）再加 1 条 `weekly` →
# **42 个叶子路径**。它们都是**增量命令**，
# 不改 31 条基线的计数口径（docs/CLI.md §D′ / §E″ / §D‴ / §A′）。
LEAF_PATHS: tuple[tuple[str, ...], ...] = (
    ("run",),
    ("review",),
    ("review", "show"),
    # R11 新增（QUOT-149，不计入 31 条基线，见 docs/CLI.md §A′）
    ("weekly",),
    ("validate",),
    ("validate", "reconcile"),
    ("sentiment", "show"),
    ("ladder", "show"),
    ("sync",),
    ("sync", "status"),
    ("sources", "list"),
    ("sources", "validate"),
    ("sources", "health"),
    ("watch", "start"),
    ("watch", "status"),
    ("watch", "stop"),
    ("notify", "test"),
    ("journal", "add"),
    ("journal", "list"),
    ("journal", "stats"),
    # R7 新增（QUOT-145，不计入 31 条基线，见 docs/CLI.md §D″）
    ("pool",),
    ("position", "add"),
    ("position", "list"),
    ("position", "update"),
    ("position", "remove"),
    # R5 新增（QUOT-143，同样不计入基线计数，见 docs/CLI.md §D‴）
    ("industry", "list"),
    ("industry", "show"),
    ("export",),
    ("state", "inspect"),
    ("state", "reset"),
    ("ai", "status"),
    ("ai", "retry"),
    ("ai", "chain", "test"),
    # R1-1 新增叶子（不属 31 条基线，但同样受「全局 --output」契约管辖）
    ("research",),
    # R6（QUOT-144）新增的两个叶子命令，同上
    ("news",),
    ("events",),
    ("mcp", "stdio"),
    ("mcp", "http"),
    ("mcp", "tools", "list"),
    ("config", "validate"),
    ("config", "migrate"),
    ("schedule", "list"),
    ("schedule", "run"),
    ("doctor",),
)

# 同一条 argv 带上 `--output json`：退出码期望不变，且**绝不能是 2**
# （2 只会来自「No such option: --output」这种契约违约）。
OUTPUT_MATRIX: tuple[tuple[tuple[str, ...], set[int]], ...] = tuple(
    ((*argv, "--output", "json"), expected) for argv, expected in COMMAND_MATRIX
)

# 已实现且**有结构化 stdout** 的路径：`--output json` 必须能被 json.loads 解析，
# `--output csv` 至少不得报选项错误（表头 + 行）。桩命令没有载荷，不在此列。
JSON_PATHS: tuple[tuple[str, ...], ...] = (
    ("sync", "status"),
    ("sources", "list"),
    ("sources", "health"),
    ("state", "inspect"),
    ("mcp", "tools", "list"),
    ("config", "validate"),
    # M5：迁移结果（status / steps / backups / warnings）是结构化载荷，`--dry-run` 亦然
    ("config", "migrate"),
    ("doctor",),
    # R7：`pool` 不在列——本机没同步涨停池时它按 6 退出（本表口径是 0/3），
    # 打分载荷由 tests/unit/core/test_pool_golden.py 覆盖。
    # `position list` 空库也是 0 + 结构化摘要，与 `journal` 同口径。
    ("position", "list"),
    # M4：盯盘/通知/调度都有结构化 stdout。`watch start` 不在列：没有本机分钟样本
    # 时它按 4 退出（JSON_PATHS 的口径是 0/3），样本路径由
    # tests/integration/test_watch_signals.py 覆盖。
    ("watch", "status"),
    ("watch", "stop"),
    ("notify", "test"),
    ("schedule", "list"),
    ("schedule", "run", "--once"),
    # R5：`industry list` 是配置视图，本机没缓存也照常 0 + 结构化清单。
    # `industry show` 不在列——五维全空时它按 6 退出（本表口径是 0/3），
    # 载荷由 tests/unit/industry/ 与 tests/contract/test_mcp_tools.py 覆盖。
    ("industry", "list"),
    # R11：`weekly` 不在列——那一周没有每日产物时它按 6 退出（本表口径是 0/3），
    # 载荷与降级路径由 tests/unit/weekly/ 覆盖。
)

# `--output` 的可选值必须与 docs/CLI.md §F 一字不差
OUTPUT_FORMATS = ("json", "table", "csv")
