# DEC-041 项目范围裁决：公司当前只做 LadderStock，disk-warden 停止一切开发动作

- 日期：2026-11-01
- 提出：人类（老板）于 QUOT-305（2026-11-01T06:07:56Z）明确指示
- 裁决：总经理（跨项目优先级冲突属总经理直裁范围）
- 状态：生效
- 编号说明：原预占 DEC-039。DEC-039 已由 QUOT-301 的 `DEC-039-intraday-read-endpoints.md` 实际占用（PR #26，且被代码/测试/spec 反向引用），故本单（纯文档、未合并、由裁决人本人持有）主动让号至 DEC-041。DEC-040 同理由 DEC-042 承接。此后 DEC-039/040 以本行为准，勿再引用旧号。
- 关联：QUOT-305（本裁决来源）；QUOT-90（LadderStock 主单）

## 1. 背景

本工作区同时挂着 disk-warden 与 LadderStock 两条产品线，disk-warden 侧长期占着前端/测试/安全若干
人员智能体的并发额度（QUOT-296 / QUOT-298 / QUOT-299 / QUOT-303 四条在跑或待跑），LadderStock 侧
的交付排队。人类于 QUOT-305 叫停 disk-warden。

## 2. 决定

1. **disk-warden 进入冻结态**：不再 checkout、不再改代码、不再新建/推进任务。
   - 已取消的未关闭单：QUOT-296（uitour 巡检工具迁回 main）、QUOT-298（T13 端到端验收套件迁回 main）、
     QUOT-299（三套 UI 互斥实现取舍评估）、QUOT-303（beta.10 GitHub Release 发布物补齐）。
   - 存量代码、分支、PR、已合入 main 的内容、已交付文档**一律保留、不清理、不删除**，冻结不等于销毁。
   - 本裁决后仍在跑的 disk-warden run 是取消指令下达前就已分派的，其产出（分支 / PR）**不合并、不验收**，
     由负责人在单里标记为废弃即可。
2. **LadderStock 成为唯一在开发的产品线**：人员智能体的并发额度、测试与发布窗口全部优先给 LadderStock。
   LadderStock 侧未关闭单继续推进：QUOT-300 / QUOT-301 / QUOT-290 / QUOT-302（backlog）/ QUOT-304（backlog）。
3. **解冻条件**：只有人类明确要求恢复 disk-warden 时才解冻。届时需要重新做一次盘点对齐 main 状态——
   冻结期间 disk-warden 的分支与 issue 历史不作为事实来源。

## 3. 影响

- disk-warden 官网下载死链（QUOT-303 记录的问题）在本裁决后**不再修复**，属于已知且被接受的对外缺口；
  如需对外口径变更（下线下载按钮或改文案），需要人类单独授权，因为它触碰的是对外承诺。
- QUOT-296 / QUOT-298 抢救的两套 UI 视觉回归与端到端验收资产随 disk-warden 冻结一起停摆；
  这套方法论在 LadderStock 侧对应能力已由 QUOT-300 的导航↔路由一致性测试、QUOT-290 的真实整包验证承担。
