# QUOT-546：修 peer 认领 identify 签名缺陷（FinanceCoffer / ProxyFleet 对齐 BaseAdapter 契约）

关联：QUOT-528（peer adopt-only 边界验收）、QUOT-545（adopted_peer 停止边界）、
QUOT-519 产品化伞单、DEC-014（只终止账本可证明的进程）。
状态：已交付，待验收

## 1. 缺陷

`page_boot.py:_identify`（`src/ladderstock/supervisor/page_boot.py:580`）按
`BaseAdapter` 契约调 `adapter.identify(port)`，期望拿回单端口 `PortEvidence`
（含 `.pid / .parent_chain / .name / .cmdline / .matched`）用于 peer 认领记账。
但两个 peer adapter 的 `identify` 偏离契约：

- `FinanceCofferAdapter.identify(self) -> Identification`
- `ProxyFleetAdapter.identify(self) -> Identification`

二者均**不收 port 参数**、只返回聚合 `Identification`（无 `.pid`）。当对应 peer
端口在监听时，`_identify` 调 `identify(port)` → `TypeError: identify() takes 1
positional argument but 2 were given`，被 `page_boot.py:363-365` 的
`try/except Exception`（单路失败不拖垮启动）兜住。

后果（非致命）：financecoffer(8100) / proxyfleet 监听时**不被认领记账**（ledger 无
adopted 记录），但**不终止 / 不重启 peer，adopt-only 安全红线不破**。属低危认领 /
记账缺陷。

## 2. 实现（最小、加性、对齐契约）

把两个 adapter 的 `identify` 改成与 `BaseAdapter.identify`
（`module_adapters.py:952`）完全一致的形态：

```python
def identify(self, port: int | None = None) -> Identification | PortEvidence:
    if port is not None:
        return self._evidence(port)          # 单端口 PortEvidence，供 _identify 认领
    items = tuple(self._evidence(item) for item in self.ports)
    return Identification(module=self.name, matched=any(i.matched for i in items), ports=items)
```

- 直接复用两个 adapter 已有的 `_evidence(port)`（带缓存）与 `_build_evidence(port)`，
  不新造识别逻辑；
- 保留无参 `identify()` 的聚合语义，`probe()`（`return self.identify().matched`）
  与引擎 `engine._matched_evidence` 的无参调用不受影响；
- 不改 `_identify` 调用方契约（它本就按 BaseAdapter 契约写）；不改数据 / 缺数口径。

改动仅 `src/ladderstock/supervisor/module_adapters.py` 两处 `identify`（各 +3/-1 行）。

## 3. 边界（红线遵守）

- peer（8000/9100/9000/8100 等）只 adopt 不终止不重启；新增测试全部走 fake host，
  不连接 / 不终止 / 不重启生产 peer 或生产看板 5950；
- 不改端口白名单（`MODULE_PORTS` / `MODULE_ORDER` 原样）；不改停止 / 自愈逻辑；
- 未整文件 skip / continue-on-error / 删用例 / expect(true) / 提高 timeout / --retry；
  断言真实校验认领 PID 与账本 `adopted_peer` 标记；
- 未触碰 `D:\vibe-astock`；无密钥 / 连接串进 yaml / 日志 / 提交；纯后端，无 UI。

## 4. 验收证据

新增 13 项测试（6 项契约 + 7 项真实适配器端到端认领），全部假宿主：

契约层（`tests/unit/supervisor/test_module_adapters.py`）：
- financecoffer / proxyfleet `identify(port)` 返回单端口 `PortEvidence`（端口 / PID /
  matched 正确）；
- 无参 `identify()` 仍返回聚合 `Identification`（`.module / .matched / .ports` 口径不变）；
- 端口未监听 / 外部命令行不命中 → `matched=False`（失败关闭）。

端到端（`tests/unit/supervisor/test_page_boot.py`，注入真实 FinanceCofferAdapter /
ProxyFleetAdapter，而非天生带 port 参数的 FakePeerAdapter）：
- `_adopt_peer("financecoffer")` / `_adopt_peer("proxyfleet")` 返回 `STATE_ADOPTED`、
  PID 正确、无 TypeError / AttributeError；账本 `origin=adopted` 且 `adopted_peer=True`；
- `ensure()` 全路径：修复前 TypeError 被兜成 failed，修复后如实 adopted；二次打开幂等、
  peer PID 不变、绝不终止；
- 端口未监听 → `not_listening`、不认领；外部占口 / wslhost 残留转发无容器 →
  `port_occupied`、不认领、绝不终止。

**测试真实性的反向验证**：临时回退源码修复后，契约层 2 项单端口用例与端到端 5 项
认领用例均复现 `TypeError: ... identify() takes 1 positional argument but 2 were
given`（端到端精确落在 `page_boot.py:580`）；恢复修复后全绿。证明用例非空断言。

本地检查（Windows / Python 3.12，`PYTHONPATH` 指向本检出 src，规避宿主 editable 污染）：

| 检查 | 结果 |
|---|---|
| 新增 13 项测试 | 全过 |
| supervisor 单元 + `test_peer_adopt_only` 集成（170 项） | 全过 |
| web supervisor 端点回归（26 项） | 全过 |
| `ruff check .` | 干净 |
| `mypy`（190 文件） | 干净 |
| CI 双绿（backend pytest/ruff/mypy + frontend） | 见 PR 门禁与交付评论 |

`tests/ops/*`（真实宿主生命周期）会起停生产端口，本机未执行（沿用 QUOT-526 / 545
先例，CI ubuntu 亦 skip）。全量 pytest 由 CI 从固定依赖安装权威执行。

## 5. 已知取舍

- 选「对齐 BaseAdapter 契约」而非改 `_identify` 调用方：调用方本就按契约写，缺陷在
  两个 adapter 的签名偏离；对齐后四个 `identify`（Base / FinanceCoffer / ProxyFleet /
  ProxyPool）形态统一，后续新增 peer adapter 有唯一范式。
- 复用既有 `_evidence(port)` 缓存路径，不引入新识别逻辑，行为与聚合口径逐端口一致。