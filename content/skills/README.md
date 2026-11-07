# ladderstock-skills —— 8 个 Skill 包（已落盘，可直接入库）

2026-11-07 人类拍板两条：①缺适配器的包**先发**，卡片标「未接通」；②步骤上的「问 Agent」= **直接开本页 AiDock 并预填这一步的提示词**（另留「复制提示词」后门）。
本目录按这两条落地：8 个包，5 个 ready、1 个 partial、2 个 unconnected。

## 目录

```
ladderstock-skills/
├── index.json              总清单（前端 /skills 列表页只读这一份）
├── a-share-dossier/        个股全景        7 步 · 23 工具   ready
├── a-share-tape/           盘面复盘        6 步 · 16 工具   ready
├── sector-rotation/        板块与指数      6 步 · 14 工具   ready
├── event-calendar/         事件日历        5 步 · 13 工具   ready
├── review-journal/         复盘与日志      5 步 ·  6 工具   ready（含本地 /api/journal）
├── macro-crossasset/       宏观与跨资产    5 步 · 17 工具   partial（美债段未接通）
├── us-dossier/             美股港股全景    6 步 · 17 工具   unconnected
└── cross-market/           跨市场联动      5 步 · 13 工具   unconnected
```

每个包 = `SKILL.md`（六段式：何时用 / 何时不用 / 前置 / 核心原则 / 取数纪律 / fast path）+ `steps.json`（步骤机读版）。

## steps.json 结构

```json
{
  "name": "a-share-dossier",
  "label": "个股全景",
  "entry": { "page": "home|research|my-stocks", "button": "问 Agent", "action": "open_ai_dock" },
  "status": "ready",                     // ready | partial | unconnected
  "blocked_by": "...",                    // 有值时卡片必须显示原因
  "offline_notice": "...",                // unconnected 包的抽屉顶部说明
  "tools_allow": ["...真实工具名..."],     // 全包白名单
  "steps": [
    { "n": 1, "title": "...", "output": "profile",
      "tools": ["..."],                   // 本步白名单，超出即失败
      "prompt": "..." }                   // 既是给 AI 的指令，也是给用户抄走的提示词
  ]
}
```

与参考库 `sectorPrompts.ts`（`Track{focus,tabs}` → `SectorStep{n,title,prompt}`）的区别：本版每步多一条 `tools` 白名单，
并在 SKILL.md 里钉死「数字必须来自工具结果 / data_as_of / confidence A·B·C」，参考库那条红线「只给方法，不替用户填内容」照旧成立。

## 校验结果（2026-11-07 实测）

- 工具名来源：TickHub-CN `http://127.0.0.1:8787/api/mcp/tools` 返回 **125** 个 canonical；
  TickHub-US `http://127.0.0.1:8002/mcp` 握手后 `tools/list` 返回 **41** 个。
- 8 个 `steps.json` 全部为合法 JSON；所有 `tools` 引用在这两份清单内，**未知引用 0**（唯一例外是 review-journal 的
  `local:/api/journal|positions|realized`，这是本机 LadderStock 接口，刻意标 `local:` 前缀）。
- 每步都有 `title` 与非空 `prompt`，无空步。

## 两个未接通包为什么先发

`us-dossier` / `cross-market` 依赖 `th_us_*`，而 LadderStock `provider_registry.py` 里 `th_us_*` 目前 0 命中（QUOT-471 在补）。
照参考库 `sectors.json` 里 `verified=false` 的占位做法：**先发骨架、卡片标「未接通」**，执行时返回 `offline_notice`，不输出任何数字。
`macro-crossasset` 是 `partial`：A 股宏观段（GDP/CPI/PMI/社融/LPR/利率/收益率曲线/A50/北向）齐全可跑，只有美债与全球政策利率那段标未接通。

## 与产品侧的接线（QUOT-474）

已开单 QUOT-474（前端架构师）：
1. 一级组 `deep-dive`（个股深挖）整组改为 `research-tools`（研究工具），组内两项：`/skills`（工具总览）、`/research`（个股深挖，路径不变）。
2. `/skills` 列表页读 `content/skills/index.json`（即本目录 `index.json`，原样复制入库），卡片显示状态徽标与「N 步 · M 工具」。
3. `/skills/:name` 详情页给步骤时间轴，每步「问 Agent」→ 复用 `AiDock` 抽屉并预填该步 `prompt`。
4. 同步 `nav-routes.test.tsx` 断言 + 新写一条 DEC 修订 DEC-044 第 9 组。

## 怎么跑一个包

```bash
th-cn doctor                                   # 先看源健康
th-cn tool call stock_profile --params '{"code":"600000"}'
```

在 LadderStock 里则由「工具总览 → 卡片 → 步骤 → 问 Agent」进入，抽屉带着该步的 prompt 开局。
