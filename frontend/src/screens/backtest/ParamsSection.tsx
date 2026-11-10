import { Badge, Banner, Button, Field, Input, SectionCard, Select } from '../../components/ui'

/**
 * 参数区骨架（QUOT-534）。
 *
 * 策略清单与「提交运行」端点由后端子单 QUOT-535 提供；落地前这里**只搭字段骨架**：
 * - 不塞前端常量（策略下拉只有一句「等待后端清单」，不是三天内置名字）；
 * - 不预填任何成本默认值（成本字段等 `config_defaults`）；
 * - 不发任何请求，按钮禁用并写明原因。
 *
 * 标的按本单规格保留 M1 默认 `600000`，它是参数取值、不是「清单」。
 */
export function ParamsSection() {
  return (
    <SectionCard
      title="参数区"
      subtitle="M1 回测参数 · 字段骨架"
      tone="info"
      actions={<Badge tone="warn">待接线</Badge>}
    >
      <div className="flex flex-col gap-4">
        <Banner tone="info" title="本区暂不发起回测">
          策略清单与运行提交端点由后端子单 QUOT-535 提供，合并前这里只展示字段与占位：不读取前端常量、
          不发送 POST。当前请用本机 CLI <code className="ldst-num">ldst backtest run</code> 生成运行卡。
        </Banner>

        <form
          className="flex flex-col gap-4"
          aria-label="回测参数（待接线，不可提交）"
          onSubmit={(event) => event.preventDefault()}
        >
          <div className="grid gap-3 sm:grid-cols-2 desk:grid-cols-4">
            <Field label="策略" htmlFor="backtest-strategy" hint="清单来自 GET /api/backtest/strategies">
              <Select id="backtest-strategy" disabled>
                <option>等待后端策略清单接口…</option>
              </Select>
            </Field>
            <Field label="标的" htmlFor="backtest-symbol" hint="M1 默认 600000">
              <Input id="backtest-symbol" value="600000" disabled readOnly />
            </Field>
            <Field label="开始日期" htmlFor="backtest-start" hint="以运行卡实际区间为准">
              <Input id="backtest-start" type="date" disabled />
            </Field>
            <Field label="结束日期" htmlFor="backtest-end" hint="以运行卡实际区间为准">
              <Input id="backtest-end" type="date" disabled />
            </Field>
          </div>

          <div className="grid gap-3 sm:grid-cols-2 desk:grid-cols-3">
            <Field label="手续费率" htmlFor="backtest-commission" hint="默认值来自后端 config">
              <Input id="backtest-commission" placeholder="后端 config_defaults" disabled />
            </Field>
            <Field label="滑点" htmlFor="backtest-slippage" hint="默认值来自后端 config">
              <Input id="backtest-slippage" placeholder="后端 config_defaults" disabled />
            </Field>
            <Field label="印花税（卖出单边）" htmlFor="backtest-stamp-duty" hint="默认值来自后端 config">
              <Input id="backtest-stamp-duty" placeholder="后端 config_defaults" disabled />
            </Field>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
            <Button
              type="submit"
              variant="primary"
              disabled
              disabledReason="QUOT-535 合并后接线"
            >
              运行回测
            </Button>
            <p className="text-caption text-text-muted">
              接线后这里会提交参数并轮询任务，页面仍只渲染后端返回的运行卡。
            </p>
          </div>
        </form>
      </div>
    </SectionCard>
  )
}
