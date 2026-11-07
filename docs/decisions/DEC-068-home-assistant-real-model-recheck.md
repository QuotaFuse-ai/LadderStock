# DEC-068 首页 B 块真实模型通道复验（补充 DEC-067）

- 日期：2026-11-07
- 关联：DEC-067、QUOT-472、QUOT-454、QUOT-399

## 背景

DEC-067 落盘时本机没有可用模型 Key，「真实模型调用」这条验收项只能用本机 OpenAI
兼容 stub 打通链路，并在交付报告里标注「不代表模型能力」。2026-11-07 通道到位：
`LDST_LLM_BASE_URL=https://www.xiaoyaoapi.com/v1` + `LDST_LLM_MODEL=deepseek-v4.1-flash`。

## 结论

- DEC-067 的界面与协议决定不变：B 块继续复用
  `POST /api/research/{scope}/assistant`（`scope=home`），不新建第二套对话。
- 真实模型已在临时实例（5953）上跑通：`200`、`model=openai:deepseek-v4.1-flash`，
  回答仅引用页面上下文，`watchlist` 为空时明说「没有取到」；
  「本轮来源」渲染 `大盘与全球指数 · 6 项 · 取数 06:30:00 · 首页 A 块`。
- 上游存在偶发慢响应（同一请求体 11s 成功 / 61s 超时）；超时按既有设计返回
  504 `ai_timeout` + 重试，不静默失败，不需要前端特判。
- DEC-067「遗留」一节的 stub 说明由本文件承接，不再回改 DEC-067 原文。

## 后果

- `docs/tasks/QUOT-472/README.md` 的取证表改用真实模型截图与请求/响应；
- 复跑需要 `LDST_LLM_*` 三个变量，缺任一项时后端回 503 `ai_not_configured`，属预期降级。
