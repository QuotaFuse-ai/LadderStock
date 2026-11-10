import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Play } from 'lucide-react'

import { isApiError } from '../../api'
import {
  fetchBacktestStrategies,
  isBacktestEndpointMissing,
  type BacktestConfigDefaults,
  type BacktestRunRequest,
  type BacktestStrategiesPayload,
  type BacktestStrategy,
  type BacktestStrategyParam,
} from '../../api/backtest'
import { Badge, Banner, Button, Field, Input, SectionCard, Select } from '../../components/ui'
import { useAsync } from '../../lib/useAsync'
import { exitCodeText, jobStatusText, useBacktestRun } from './useBacktestRun'

/**
 * 参数区（QUOT-534 + QUOT-535 契约）。
 *
 * 数据来源只有一个：`GET /api/backtest/strategies`。
 * - 策略、参数 schema（label / type / default / min / max）、成本默认值与券商三档预设
 *   全部来自后端，本文件不写任何策略名或成本常量；
 * - 提交走 `POST /api/backtest/run`，之后 1s 轮询任务；`run_id` 交给页面载入运行卡；
 * - 成本字段（万分之口径）按输入原样透传，前端不做单位换算、不重算任何指标。
 */

const CUSTOM_PRESET = 'custom'

interface CostFields {
  commission_rate_wan: string
  commission_floor_yuan: string
  transfer_fee_wan: string
  stamp_duty_wan: string
  slippage: string
}

function textOf(value: number | null): string {
  return value === null ? '' : String(value)
}

function defaultParams(spec: BacktestStrategy | null): Record<string, string> {
  const out: Record<string, string> = {}
  for (const param of spec?.params ?? []) {
    out[param.key] = param.default === null ? '' : String(param.default)
  }
  return out
}

function costsFromDefaults(defaults: BacktestConfigDefaults): CostFields {
  return {
    commission_rate_wan: textOf(defaults.commission_rate_wan),
    commission_floor_yuan: textOf(defaults.commission_floor_yuan),
    transfer_fee_wan: textOf(defaults.transfer_fee_wan),
    stamp_duty_wan: textOf(defaults.stamp_duty_wan),
    slippage: textOf(defaults.slippage),
  }
}

/** 后端默认值命中某一档预设时，下拉直接选中它；否则落到「自定义」。 */
function presetFor(defaults: BacktestConfigDefaults): string {
  const hit = defaults.presets.find(
    (preset) =>
      preset.commission_rate_wan === defaults.commission_rate_wan &&
      preset.commission_floor_yuan === defaults.commission_floor_yuan,
  )
  return hit?.key ?? CUSTOM_PRESET
}

function paramHint(param: BacktestStrategyParam): string {
  const bounds: string[] = []
  if (param.min !== null) bounds.push(`≥ ${param.min}`)
  if (param.max !== null) bounds.push(`≤ ${param.max}`)
  const kind = param.type === 'int' ? '整数' : param.type === 'float' ? '小数' : '文本'
  return [`后端默认 ${String(param.default)}`, kind, bounds.join(' / ')].filter(Boolean).join(' · ')
}

function errorText(error: unknown): string {
  // ApiError.message 已含「路径 + HTTP 码 + 后端 detail」，比只取 detail 更适合排障。
  if (isApiError(error)) return error.message
  return error instanceof Error ? error.message : String(error)
}

interface ParamsSectionProps {
  /** 最新运行卡的区间：仅作为日期初值，用户可改；没有运行卡时留空。 */
  defaultPeriod: { start: string | null; end: string | null } | null
  onSettled: (runId: string) => void
}

export function ParamsSection({ defaultPeriod, onSettled }: ParamsSectionProps) {
  const strategies = useAsync((signal) => fetchBacktestStrategies(signal), [])
  const payload = strategies.data

  const [strategyName, setStrategyName] = useState('')
  const [params, setParams] = useState<Record<string, string>>({})
  const [symbol, setSymbol] = useState('')
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [costs, setCosts] = useState<CostFields>({
    commission_rate_wan: '',
    commission_floor_yuan: '',
    transfer_fee_wan: '',
    stamp_duty_wan: '',
    slippage: '',
  })
  const [presetKey, setPresetKey] = useState(CUSTOM_PRESET)
  const [errors, setErrors] = useState<Record<string, string>>({})

  const initialisedFor = useRef<BacktestStrategiesPayload | null>(null)
  const datesTouched = useRef(false)
  const run = useBacktestRun(onSettled)

  // 策略清单到手后只初始化一次：改名/改参数的默认值都取后端给的 schema。
  useEffect(() => {
    if (payload === null || initialisedFor.current === payload) return
    initialisedFor.current = payload
    const first = payload.strategies[0] ?? null
    setStrategyName(first?.name ?? '')
    setParams(defaultParams(first))
    setSymbol(payload.symbol_default)
    setCosts(costsFromDefaults(payload.config_defaults))
    setPresetKey(presetFor(payload.config_defaults))
  }, [payload])

  // 运行卡区间到达时补日期初值，但不覆盖用户已经动过的输入。
  useEffect(() => {
    if (datesTouched.current) return
    if (defaultPeriod?.start) setStart(defaultPeriod.start)
    if (defaultPeriod?.end) setEnd(defaultPeriod.end)
  }, [defaultPeriod?.start, defaultPeriod?.end])

  const spec = payload?.strategies.find((entry) => entry.name === strategyName) ?? null
  const presets = payload?.config_defaults.presets ?? []
  const disabled = run.phase === 'submitting' || run.phase === 'running'
  const strategiesMissing = strategies.status === 'error' && isBacktestEndpointMissing(strategies.error)
  const canEdit = payload !== null && !disabled

  const changeStrategy = (name: string) => {
    setStrategyName(name)
    setParams(defaultParams(payload?.strategies.find((entry) => entry.name === name) ?? null))
    setErrors({})
  }

  const changeCost = (key: keyof CostFields, value: string) => {
    setCosts((prev) => ({ ...prev, [key]: value }))
    // 手改佣金两字段即离开预设档，避免下拉写着「万0.9」但输入框是别的数。
    if (key === 'commission_rate_wan' || key === 'commission_floor_yuan') {
      setPresetKey(CUSTOM_PRESET)
    }
  }

  const changePreset = (key: string) => {
    setPresetKey(key)
    if (key === CUSTOM_PRESET) return
    const preset = presets.find((entry) => entry.key === key)
    if (!preset) return
    setCosts((prev) => ({
      ...prev,
      commission_rate_wan: preset.commission_rate_wan === null ? '' : String(preset.commission_rate_wan),
      commission_floor_yuan:
        preset.commission_floor_yuan === null ? '' : String(preset.commission_floor_yuan),
    }))
  }

  /** 提交前按后端 schema 约束做一次本地体检；范围错误仍以后端 400/422 为准。 */
  const validate = (): Record<string, string> => {
    const problems: Record<string, string> = {}
    if (!strategyName) problems.strategy = '请选择策略'
    if (symbol.trim() === '') problems.symbol = '请填写标的代码'
    if (!start) problems.start = '请选择开始日期'
    if (!end) problems.end = '请选择结束日期'
    if (start && end && start > end) problems.end = '开始日期不能晚于结束日期'

    for (const param of spec?.params ?? []) {
      const raw = (params[param.key] ?? '').trim()
      if (raw === '') {
        problems[param.key] = `请填写${param.label}`
        continue
      }
      const value = Number(raw)
      if (!Number.isFinite(value)) {
        problems[param.key] = `${param.label}必须是数值`
      } else if (param.type === 'int' && !Number.isInteger(value)) {
        problems[param.key] = `${param.label}必须是整数`
      } else if (param.min !== null && value < param.min) {
        problems[param.key] = `${param.label}不能小于 ${param.min}`
      } else if (param.max !== null && value > param.max) {
        problems[param.key] = `${param.label}不能大于 ${param.max}`
      }
    }

    const numeric: Array<[keyof CostFields, string, number]> = [
      ['commission_rate_wan', '佣金率', 0],
      ['commission_floor_yuan', '单笔最低佣金', 0],
      ['transfer_fee_wan', '过户费', 0],
      ['stamp_duty_wan', '印花税', 0],
      ['slippage', '滑点', 0],
    ]
    for (const [key, label, floor] of numeric) {
      const raw = costs[key].trim()
      const value = Number(raw)
      if (raw === '' || !Number.isFinite(value)) {
        problems[`cost.${key}`] = `请填写${label}`
      } else if (value <= floor) {
        problems[`cost.${key}`] = `${label}必须大于 ${floor}`
      }
    }
    return problems
  }

  const onSubmit = (event: FormEvent) => {
    event.preventDefault()
    if (!payload || disabled) return
    const problems = validate()
    setErrors(problems)
    if (Object.keys(problems).length > 0) return

    const strategyParams: Record<string, number | string> = {}
    for (const param of spec?.params ?? []) {
      const raw = params[param.key]
      strategyParams[param.key] = param.type === 'int' || param.type === 'float' ? Number(raw) : raw
    }
    const request: BacktestRunRequest = {
      strategy: strategyName,
      strategy_params: strategyParams,
      symbol: symbol.trim(),
      start,
      end,
      // 成本字段原样透传（万分之口径，字符串 → 数值的机械转换不算重算）。
      commission_rate_wan: Number(costs.commission_rate_wan),
      commission_floor_yuan: Number(costs.commission_floor_yuan),
      transfer_fee_wan: Number(costs.transfer_fee_wan),
      stamp_duty_wan: Number(costs.stamp_duty_wan),
      slippage: Number(costs.slippage),
    }
    run.submit(request)
  }

  let status: ReactNode = null
  if (strategies.status === 'loading' && payload === null) {
    status = (
      <Banner tone="info" title="正在读取后端策略清单">
        <span className="ldst-num">GET /api/backtest/strategies</span> 返回后表单才可提交；
        策略、参数范围与成本默认值全部取自该接口。
      </Banner>
    )
  } else if (strategiesMissing) {
    status = (
      <Banner
        tone="warn"
        title="策略清单接口未接入"
        action={
          <Button variant="secondary" onClick={strategies.reload}>
            重试
          </Button>
        }
      >
        本机后端没有 <code className="ldst-num">GET /api/backtest/strategies</code>。
        升级到含 QUOT-535 的后端后重试；页面不会用前端常量顶替清单。
      </Banner>
    )
  } else if (strategies.status === 'error') {
    status = (
      <Banner
        tone="danger"
        title="策略清单读取失败"
        action={
          <Button variant="secondary" onClick={strategies.reload}>
            重试
          </Button>
        }
      >
        {errorText(strategies.error)}
      </Banner>
    )
  } else if (run.phase === 'submitting') {
    status = (
      <Banner tone="info" title="已提交，等待后端受理…">
        正在调用 <span className="ldst-num">POST /api/backtest/run</span>。
      </Banner>
    )
  } else if (run.phase === 'running') {
    status = (
      <Banner tone="info" title="回测运行中">
        <span className="ldst-num">job {run.jobId}</span> ·{' '}
        {run.job ? jobStatusText(run.job.status) : '等待任务状态'}；页面每 1s 轮询任务，
        完成后自动载入运行卡。
      </Banner>
    )
  } else if (run.phase === 'settled') {
    status = (
      <Banner
        tone={run.job?.gate_passed === false ? 'warn' : 'ok'}
        title={run.job?.gate_passed === false ? '回测完成，校验门未通过' : '回测完成'}
      >
        <span className="ldst-num">{run.runId}</span> · {exitCodeText(run.job?.exit_code ?? null)}
        ；结果区与证据区已切换到这张运行卡。
      </Banner>
    )
  } else if (run.phase === 'failed') {
    status = (
      <Banner tone="danger" title={run.job === null ? '回测没有提交成功' : '回测运行失败'}>
        {run.job?.exit_code != null && (
          <span className="block">{exitCodeText(run.job.exit_code)}</span>
        )}
        <span className="block">{errorText(run.error)}</span>
      </Banner>
    )
  }

  return (
    <SectionCard
      title="参数区"
      subtitle="策略 / 标的 / 区间 / 成本 → 提交后轮询任务，结果仍只读运行卡"
      tone="info"
      actions={
        <Badge tone={canEdit ? 'ok' : 'neutral'}>
          {payload === null
            ? '清单加载中'
            : run.phase === 'running'
              ? '运行中'
              : run.phase === 'submitting'
                ? '提交中'
                : '已接线'}
        </Badge>
      }
    >
      <div className="flex flex-col gap-4">
        {status}

        <form className="flex flex-col gap-4" aria-label="回测参数" onSubmit={onSubmit} noValidate>
          <div className="grid gap-3 sm:grid-cols-2 desk:grid-cols-4">
            <Field
              label="策略"
              htmlFor="backtest-strategy"
              required
              error={errors.strategy}
              hint="清单来自 GET /api/backtest/strategies"
            >
              <Select
                id="backtest-strategy"
                value={strategyName}
                disabled={!canEdit}
                onChange={(event) => changeStrategy(event.target.value)}
              >
                {payload === null && <option value="">等待后端策略清单…</option>}
                {(payload?.strategies ?? []).map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.label}（{entry.name}）
                  </option>
                ))}
              </Select>
            </Field>
            <Field
              label="标的"
              htmlFor="backtest-symbol"
              required
              error={errors.symbol}
              hint={`后端默认 ${payload?.symbol_default || '—'}`}
            >
              <Input
                id="backtest-symbol"
                value={symbol}
                disabled={!canEdit}
                onChange={(event) => setSymbol(event.target.value)}
                autoComplete="off"
                inputMode="numeric"
              />
            </Field>
            <Field label="开始日期" htmlFor="backtest-start" required error={errors.start} hint="默认取最新运行卡区间">
              <Input
                id="backtest-start"
                type="date"
                value={start}
                disabled={!canEdit}
                onChange={(event) => {
                  datesTouched.current = true
                  setStart(event.target.value)
                }}
              />
            </Field>
            <Field label="结束日期" htmlFor="backtest-end" required error={errors.end} hint="默认取最新运行卡区间">
              <Input
                id="backtest-end"
                type="date"
                value={end}
                disabled={!canEdit}
                onChange={(event) => {
                  datesTouched.current = true
                  setEnd(event.target.value)
                }}
              />
            </Field>
          </div>

          {(spec?.params.length ?? 0) > 0 && (
            <div className="grid gap-3 sm:grid-cols-2 desk:grid-cols-4">
              {spec?.params.map((param) => {
                const id = `backtest-param-${param.key}`
                const numeric = param.type === 'int' || param.type === 'float'
                return (
                  <Field
                    key={param.key}
                    label={param.label}
                    htmlFor={id}
                    required
                    hint={paramHint(param)}
                    error={errors[param.key]}
                  >
                    <Input
                      id={id}
                      type={numeric ? 'number' : 'text'}
                      step={param.type === 'int' ? 1 : 'any'}
                      min={param.min ?? undefined}
                      max={param.max ?? undefined}
                      value={params[param.key] ?? ''}
                      disabled={!canEdit}
                      onChange={(event) =>
                        setParams((prev) => ({ ...prev, [param.key]: event.target.value }))
                      }
                    />
                  </Field>
                )
              })}
            </div>
          )}

          <div className="flex flex-col gap-3 border-t border-border pt-3">
            <div className="grid gap-3 sm:grid-cols-2 desk:grid-cols-4">
              <Field
                label="成本预设"
                htmlFor="backtest-cost-preset"
                hint="来自后端 config_defaults.presets"
              >
                <Select
                  id="backtest-cost-preset"
                  value={presetKey}
                  disabled={!canEdit}
                  onChange={(event) => changePreset(event.target.value)}
                >
                  {presets.map((preset) => (
                    <option key={preset.key} value={preset.key}>
                      {preset.label}
                    </option>
                  ))}
                  <option value={CUSTOM_PRESET}>自定义</option>
                </Select>
              </Field>
              <Field
                label="佣金率（万分之，双向）"
                htmlFor="backtest-cost-rate"
                required
                error={errors['cost.commission_rate_wan']}
                hint="万 0.9 填 0.9"
              >
                <Input
                  id="backtest-cost-rate"
                  type="number"
                  step="any"
                  value={costs.commission_rate_wan}
                  disabled={!canEdit}
                  onChange={(event) => changeCost('commission_rate_wan', event.target.value)}
                />
              </Field>
              <Field
                label="单笔最低佣金（元）"
                htmlFor="backtest-cost-floor"
                required
                error={errors['cost.commission_floor_yuan']}
                hint="5 = 不免五；0.01 / 1 = 免五起收"
              >
                <Input
                  id="backtest-cost-floor"
                  type="number"
                  step="any"
                  value={costs.commission_floor_yuan}
                  disabled={!canEdit}
                  onChange={(event) => changeCost('commission_floor_yuan', event.target.value)}
                />
              </Field>
              <Field
                label="过户费（万分之，双向）"
                htmlFor="backtest-cost-transfer"
                required
                error={errors['cost.transfer_fee_wan']}
                hint="现行官方口径万 0.1"
              >
                <Input
                  id="backtest-cost-transfer"
                  type="number"
                  step="any"
                  value={costs.transfer_fee_wan}
                  disabled={!canEdit}
                  onChange={(event) => changeCost('transfer_fee_wan', event.target.value)}
                />
              </Field>
            </div>
            <div className="grid gap-3 sm:grid-cols-2 desk:grid-cols-4">
              <Field
                label="印花税（万分之，卖出单边）"
                htmlFor="backtest-cost-stamp"
                required
                error={errors['cost.stamp_duty_wan']}
                hint="2023-08-28 起万 5"
              >
                <Input
                  id="backtest-cost-stamp"
                  type="number"
                  step="any"
                  value={costs.stamp_duty_wan}
                  disabled={!canEdit}
                  onChange={(event) => changeCost('stamp_duty_wan', event.target.value)}
                />
              </Field>
              <Field
                label="滑点（小数）"
                htmlFor="backtest-cost-slippage"
                required
                error={errors['cost.slippage']}
                hint="按成交价，如 0.001 = 千 1"
              >
                <Input
                  id="backtest-cost-slippage"
                  type="number"
                  step="any"
                  value={costs.slippage}
                  disabled={!canEdit}
                  onChange={(event) => changeCost('slippage', event.target.value)}
                />
              </Field>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3">
            <Button type="submit" variant="primary" disabled={!canEdit}>
              <Play className="size-3.5" aria-hidden="true" />
              {disabled ? '回测进行中…' : '运行回测'}
            </Button>
            <p className="text-caption text-text-muted">
              提交只发参数；页面不重算指标，结果区/证据区的所有数字仍读运行卡。
            </p>
          </div>
        </form>
      </div>
    </SectionCard>
  )
}
