# DEC-050 ProxyFleet 作为工具组件展示，不登记为 peer

- 日期：2026-11-04
- 状态：已采纳
- 关联：DEC-047（端口归属）、QUOT-200

## 背景

DEC-047 把 8788 / 8789 划给 ProxyFleet，并明确控制面「不登记、不探测、不抢占」。
但设置页子模块只有四张 peer 卡，用户看不到代理池是否在运行、控制台是否可用。

## 决定

1. 在 `boundary.py` 新增 `COMPONENTS` / `ComponentControl`，与 `PEERS` 分开维护。
   ProxyFleet 不提供数据集；若登记进 `PEERS`，数据集归属校验会把它当成合法数据来源，
   反而破坏四层边界。
2. `GET /api/control/peers` 增加 `components` 字段：目录、8788 控制台 / 8789 网关监听、
   `/readyz` 健康检查，全部只读。
3. 设置页在子模块区下方渲染「工具组件」卡，提供「打开控制台」链接；安装与启停仍归
   `pfctl.py` 与 Windows 计划任务 `ProxyFleet`，控制面不代管。
4. 工具组件端口进入「数据与端口」表，避免 8788 / 8789 被展示成未知占用。

## 代价

- peer 与组件是两套只读模型；未来若组件也需要控制面启停，需要另加任务通道。
- 组件健康检查依赖组件自己的 HTTP 端点（ProxyFleet 为 `/readyz`）。
