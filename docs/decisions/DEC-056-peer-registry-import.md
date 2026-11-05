# DEC-056 模块数以注册表为准 + 设置页「导入模块」只写配置

- 日期：2026-11-05
- 状态：已采纳
- 关联：DEC-050（工具组件）、DEC-051（第 5 张卡）、QUOT-403（peers 块唯一真源）、QUOT-420

## 背景

设置页同一屏出现过两个模块数：页头按 `peers + components` 动态算，侧栏说明与端口区
写死「六个模块」。第 6 个模块 ProxyFleet 早就不在 `peers:` 里（DEC-051 之后按工具组件
观测），写死文案因此长期说谎；同时页面没有任何新增模块的入口，只能手改
`config/sources.yaml` 再重启服务。

## 决定

1. **模块数只有一个口径**：注册表实际出卡数 = `peers:` 条数 + 工具组件数。页头、
   侧栏说明、端口区、卡片列表全部由同一个数生成，禁止任何写死的模块个数或模块名清单。
2. **注册表可查**：`GET /api/control/peers` 的响应新增 `registry` 数组，逐条给出
   id / product / package / default_port / role / repo / installed / status。控制面白名单
   （`PEER_CONTROL`）里没有的条目也照样列出，并补一张只读卡。
3. **导入只写配置**：新增 `POST /api/control/peers/import`，字段白名单为
   id / product / package / default_url / health_path / default_port / role / repo；
   未知字段（`command` / `script` / `path` 等）一律 4xx。写入方式是对
   `config/sources.yaml` 做文本插入以保留注释与顺序，写前备份
   `sources.yaml.bak.<UTC 时间戳>`，写后重新解析校验，任一步失败不落盘。
   接口受控制面路由级 `_guard`（开关 + 回环来源 + Host 白名单 + 写来源证据）保护。
4. **不新增命令通道**：导入只产生注册表条目；安装、启动、停止仍走既有的
   `install/start/stop` 任务语义。新增的 peer 在控制面白名单里没有目录与启动命令，
   因此只能只读观测，卡片按「未安装」呈现。
5. **空/缺状态不冒充**：未安装显示「未安装」，端口在听但健康检查不过显示「失败」并给
   原因，`components` 为空时显式写「当前注册表未声明工具组件」，不留白。

## 代价

- 导入后的 peer 只有「卡」没有「操作」：想真正安装/启停，仍要把它加进
  `PEER_CONTROL` 白名单（代码变更）。这是有意的安全边界——网页写入不能变成任意命令执行。
- 导入写的是运行时的 `config/sources.yaml`（由 `LDST_CONFIG_DIR` 决定），不写仓库里的
  `config/sources.yaml`；仓库内的注册表仍是版本化基线。
