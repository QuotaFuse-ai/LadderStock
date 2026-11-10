# DEC-072: Typer 钉死 0.28.0 修 CI introspection 破坏（QUOT-532）

日期：2026-11-10 · 状态：已定稿 · 关联：QUOT-532（阻塞 521/522/530/531 合并与 stage 2 收口）

## 1. 背景

所有新 PR 的 backend CI（pytest / ruff / mypy）全红，frontend-only PR 也红，
说明与 diff 无关。根因是 `pyproject.toml` 里 `typer>=0.12,<1` 的区间 pin：
CI 每次 `pip install -e ".[all,dev]"` 都解析到当时最新 Typer，行为随上游漂移。

- 绿 run 42675997277（2026-11-09）：解析到 **typer 0.28.0** + click 8.5.1。
- 红 run 42789162469（2026-11-10，PR #186）：解析到 **typer 0.29.0**。
- typer 0.29.0 起 `TyperOption` 不再暴露 `.type` 属性，
  `tests/unit/test_cli_output_option.py:114` 的
  `getattr(param.type, "choices", ())` 抛 `AttributeError`，
  `test_output_option_offers_documented_formats` 49 个参数化用例全崩。

## 2. 决策

`pyproject.toml` 的 `typer` 依赖从 `>=0.12,<1` 改为**精确 pin `==0.28.0`**，
即最后一个仍保留 `TyperOption.type` introspection 的版本，恢复 CI 可复现性。

理由：版本回退到绿 run 的实际解析版本，是最小且完整的修复；
不改测试、不迁就实现对测试可见性做取舍。红线（skip / continue-on-error /
删用例 / 改业务代码）一律未触碰。

## 3. 验证

- 本地 venv 先装到 0.29.0：复现 49 失败。
- pin 到 0.28.0 后：`tests/unit/test_cli_output_option.py` = **186 passed, 1 skipped**
  （该 1 skip 为既有 skip，非本次新增）。
- ruff / mypy 全绿。
- CI 复验：见 PR 及 QUOT-532 评论中的绿 run 链接。

## 4. 后续（脱离本次修复范围，记录待办）

该 pin 是止血。中期应把测试的 introspection 改为**经 click 层取 type/choices**
（`typer.main.get_command(...).params` 已直接返回 click 的 `Option`，`type.choices`
稳定），再放开 Typer 上限；届时可升级到 0.29+ 并追踪上游改动。
在此之前，升级 Typer 必须先改该项测试，否则 49 例必红。
