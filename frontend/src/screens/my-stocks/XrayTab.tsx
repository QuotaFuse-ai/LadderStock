import { useMemo, useState } from 'react'
import { RefreshCw, ScanLine, SlidersHorizontal } from 'lucide-react'

import { describeApiError } from '../../api'
import { fetchPortfolioXray, type PortfolioXrayPayload, type XrayGap, type XrayItem } from '../../api/radar'
import { EChart } from '../../charts/EChart'
import { useChartPalette } from '../../charts/cssVar'
import {
  Banner,
  Button,
  EmptyState,
  ErrorBlock,
  Field,
  Input,
  MetricCard,
  SectionCard,
  SkeletonRows,
  DataTable,
  type Column,
} from '../../components/ui'
import { MISSING } from '../../lib/format'
import { useAsync } from '../../lib/useAsync'
import { moneyText } from './portfolioFormat'
import { PrivacyText } from './PrivacyText'
import {
  DEFAULT_THRESHOLDS,
  THRESHOLD_FIELDS,
  findBreaches,
  industryWeightOption,
  readThresholds,
  saveThresholds,
  singleWeightOption,
  weightText,
  type ThresholdKey,
  type XrayThresholds,
} from './xrayView'

/**
 * 持仓体检（X 光）子页（QUOT-437）：`/my-stocks?tab=hold` 里的第三个标签。
 *
 * 每个数字都取自 `GET /api/portfolio/xray`，页面不重算：权重、前三合计、行业占比、
 * 相关系数、缺口全部照载荷展示；`null` 就是「没有」，画成 `—` 并带上后端给的原因，
 * 不补 0、不拿成本价顶替缺价的现价。
 *
 * 提醒线由用户自己定（存 `ldst.portfolio.xray.thresholds`）：超线横幅只说
 * 「哪一项、超到哪」，不打分、不给操作建议。
 */

export interface XrayTabProps {
  /** 流水变了以后由页面加一，触发重算（避免拿旧体检配新持仓）。 */
  refreshKey: number
}

const GAP_LABEL: Record<XrayGap['affects'], string> = {
  weight: '没算进权重',
  correlation: '只影响相关系数',
}

export function XrayTab({ refreshKey }: XrayTabProps) {
  const palette = useChartPalette()
  const view = useAsync((signal) => fetchPortfolioXray(signal), [refreshKey])
  const payload = view.data
  const [thresholds, setThresholds] = useState<XrayThresholds>(readThresholds)
  const [editing, setEditing] = useState(false)

  const breaches = useMemo(
    () => (payload ? findBreaches(payload, thresholds) : []),
    [payload, thresholds],
  )

  const pick = (next: XrayThresholds) => {
    setThresholds(next)
    saveThresholds(next)
  }

  if (view.status === 'loading' && !payload) {
    return (
      <SectionCard title="持仓体检" subtitle="权重、集中度、行业暴露与相关性">
        <SkeletonRows rows={4} />
      </SectionCard>
    )
  }

  if (view.status === 'error' && !payload) {
    const error = describeApiError(view.error)
    return <ErrorBlock title={error.title} detail={error.detail} code={error.code} onRetry={view.reload} />
  }

  if (!payload) return null

  return (
    <SectionCard
      title="持仓体检"
      subtitle="只按本机流水回放出的持仓与本机行情算；不打分、不给建议"
      actions={
        <div className="flex gap-2">
          <Button
            variant="ghost"
            leadingIcon={<SlidersHorizontal className="size-3.5" aria-hidden="true" />}
            aria-expanded={editing}
            onClick={() => setEditing((value) => !value)}
          >
            提醒线
          </Button>
          <Button
            variant="ghost"
            loading={view.refreshing}
            leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
            onClick={() => view.refresh({ manual: true })}
          >
            重新体检
          </Button>
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        {editing && <ThresholdEditor value={thresholds} onChange={pick} />}

        <BreachBanner breaches={breaches} loading={view.refreshing} />

        {payload.items.length === 0 ? (
          <EmptyState
            icon={<ScanLine />}
            title="还没有可体检的持仓"
            description="记一笔之后这里会给出权重、集中度、行业暴露与相关性；缺现价的持仓会单列在缺口里。"
          />
        ) : (
          <>
            <Metrics payload={payload} />
            <Charts payload={payload} thresholds={thresholds} palette={palette} />
            <Weights payload={payload} />
            <Buckets payload={payload} />
            <Gaps payload={payload} />
          </>
        )}

        <div className="flex flex-col gap-0.5 border-t border-border pt-3 text-caption leading-body text-text-muted">
          {payload.notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
          <p>
            价格来源：<span className="ldst-num">{payload.price_lookup.dataset || MISSING}</span> · 算到{' '}
            <span className="ldst-num">{payload.as_of ?? MISSING}</span>
            {payload.price_lookup.reason && ` · ${payload.price_lookup.reason}`}
          </p>
        </div>
      </div>
    </SectionCard>
  )
}

function BreachBanner({
  breaches,
  loading,
}: {
  breaches: ReturnType<typeof findBreaches>
  loading: boolean
}) {
  if (breaches.length > 0) {
    return (
      <Banner tone="warn" title={`有 ${breaches.length} 项超过你设的提醒线`}>
        <ul className="mt-1 flex flex-col gap-0.5 text-caption">
          {breaches.map((breach, index) => (
            <li key={`${breach.key}-${index}`}>
              <span className="text-text-secondary">{breach.label}</span>：{breach.text}
            </li>
          ))}
        </ul>
        <p className="mt-1 text-caption text-text-muted">
          超线只说明这个数超过了你自己设的线，不代表好坏；要改线点上面的「提醒线」。
        </p>
      </Banner>
    )
  }
  if (loading) return null
  return (
    <Banner tone="ok" title="没有超过你设的提醒线">
      <p className="text-caption text-text-muted">提醒线可以自己改；留空的那一项不提醒。</p>
    </Banner>
  )
}

function Metrics({ payload }: { payload: PortfolioXrayPayload }) {
  // 占比不是涨跌：一律 `weightText`（`60.0%`），不套 `+` 号、不套红绿
  const priced = payload.items.filter((item) => item.weight !== null).length
  const corr = payload.correlation
  const pair = corr.max_pair
  const nameOf = (symbol: string) => {
    const item = payload.items.find((row) => row.symbol === symbol)
    return item?.name || symbol
  }

  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
      <MetricCard
        label="总市值（已取价）"
        value={<PrivacyText text={moneyText(payload.total_value)} />}
        note={
          payload.total_value === null
            ? '一只现价都没取到'
            : `${priced} 只取到价 · ${payload.items.length - priced} 只缺价`
        }
      />
      <MetricCard
        label="单只最大占比"
        value={weightText(payload.top1)}
        note={
          payload.items[0]
            ? `${payload.items[0].name || payload.items[0].symbol}${
                payload.items[0].weight === null ? ' · 现价缺失，没算进权重' : ''
              }`
            : undefined
        }
      />
      <MetricCard
        label="前三只合计"
        value={weightText(payload.top3)}
        note={`前五只 ${weightText(payload.top5)}`}
      />
      <MetricCard
        label="标注过的行业"
        value={`${payload.industry_labeled_count} / ${payload.industry_buckets.length}`}
        note={
          payload.industry_unlabeled_count > 0
            ? `${payload.industry_unlabeled_count} 只没标行业，归「未标注」`
            : '都标了行业'
        }
      />
      <MetricCard
        label="走势最像的一对"
        value={pair ? pair.corr.toFixed(2) : '—'}
        note={pair ? `${nameOf(pair.pair[0])} 与 ${nameOf(pair.pair[1])} · ${pair.samples} 个交易日` : corr.reason ?? '没算成'}
      />
    </div>
  )
}

function Charts({
  payload,
  thresholds,
  palette,
}: {
  payload: PortfolioXrayPayload
  thresholds: XrayThresholds
  palette: ReturnType<typeof useChartPalette>
}) {
  const single = useMemo(
    () => singleWeightOption(payload, thresholds, palette),
    [payload, thresholds, palette],
  )
  const industry = useMemo(
    () => industryWeightOption(payload.industry_buckets, thresholds, palette),
    [payload.industry_buckets, thresholds, palette],
  )
  const bucketCount = payload.industry_buckets.length

  if (!single && !industry) {
    return (
      <p className="text-caption text-text-muted">
        没有取到现价的持仓，画不出占比图——缺价不算成 0，全部列在下面的缺口里。
      </p>
    )
  }

  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {single && (
        <div className="flex flex-col gap-1">
          <p className="text-caption font-medium text-text-secondary">单只占比</p>
          <EChart
            option={single.option}
            height={Math.max(160, 40 + single.shown * 28)}
            ariaLabel={`单只占比条形图，最大 ${weightText(payload.top1)}`}
          />
          {payload.items.filter((item) => item.weight !== null).length > single.shown && (
            <p className="text-caption text-text-muted">
              只画前 {single.shown} 只；其余 {payload.items.filter((item) => item.weight !== null).length - single.shown} 只不进这张图。
            </p>
          )}
        </div>
      )}
      {industry && (
        <div className="flex flex-col gap-1">
          <p className="text-caption font-medium text-text-secondary">
            行业暴露 <span className="font-normal text-text-muted">· 只用你标注的行业</span>
          </p>
          <EChart
            option={industry}
            height={Math.max(160, 40 + bucketCount * 28)}
            ariaLabel={`行业暴露条形图，共 ${bucketCount} 个行业桶`}
          />
        </div>
      )}
    </div>
  )
}

/**
 * 每只的权重表：`weight === null` 是「没算进分母」，画成 `—` 并把缺口原因挂在悬浮文案上，
 * 不当 0、也不拿成本价顶替。
 */
function Weights({ payload }: { payload: PortfolioXrayPayload }) {
  const gapReason = useMemo(() => {
    const map = new Map<string, string>()
    for (const gap of payload.gaps) {
      if (gap.affects === 'weight' && !map.has(gap.symbol)) map.set(gap.symbol, gap.reason)
    }
    return map
  }, [payload.gaps])

  const columns: Array<Column<XrayItem>> = [
    {
      key: 'name',
      header: '名称',
      truncate: true,
      render: (row) => (
        <span className={row.name ? 'truncate font-medium text-text' : 'text-text-muted'}>
          {row.name || MISSING}
        </span>
      ),
      cellTitle: (row) => row.name || '名称未填',
    },
    {
      key: 'symbol',
      header: '代码',
      width: '92px',
      render: (row) => <span className="ldst-num text-caption text-text-secondary">{row.symbol}</span>,
    },
    {
      key: 'market_value',
      header: '市值',
      width: '112px',
      numeric: true,
      render: (row) => (
        <PrivacyText
          className="ldst-num text-text"
          text={moneyText(row.market_value)}
          title={gapReason.get(row.symbol) ?? undefined}
        />
      ),
    },
    {
      key: 'weight',
      header: '权重',
      width: '88px',
      numeric: true,
      render: (row) =>
        row.weight === null ? (
          <span className="ldst-num text-text-muted" title={gapReason.get(row.symbol) ?? '没算进权重分母'}>
            {MISSING}
          </span>
        ) : (
          <span className="ldst-num text-text">{weightText(row.weight)}</span>
        ),
    },
  ]

  return (
    <div className="flex flex-col gap-1">
      <p className="text-caption font-medium text-text-secondary">
        逐只权重 <span className="font-normal text-text-muted">· 按市值降序；缺现价的不算权重</span>
      </p>
      <DataTable
        columns={columns}
        rows={payload.items}
        rowKey={(row) => row.symbol}
        density="compact"
        maxHeight="280px"
      />
    </div>
  )
}

function Buckets({ payload }: { payload: PortfolioXrayPayload }) {
  const nameOf = useMemo(() => {
    const map = new Map<string, string>()
    for (const item of payload.items) map.set(item.symbol, item.name || item.symbol)
    return map
  }, [payload.items])

  if (payload.industry_buckets.length === 0) return null

  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3">
      <p className="text-caption font-medium text-text-secondary">行业分布</p>
      <ul className="flex flex-col gap-1 text-caption leading-body">
        {payload.industry_buckets.map((bucket) => (
          <li key={bucket.industry} className="flex flex-wrap items-baseline gap-x-2">
            <span className={bucket.labeled ? 'font-medium text-text' : 'text-text-muted'}>
              {bucket.industry}
            </span>
            <span className="ldst-num text-text-secondary">{weightText(bucket.weight)}</span>
            <PrivacyText
              className="ldst-num text-text-muted"
              text={bucket.market_value === null ? MISSING : `市值 ${moneyText(bucket.market_value)}`}
            />
            <span className="text-text-muted">
              {bucket.symbols.map((symbol) => nameOf.get(symbol) ?? symbol).join('、')}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function Gaps({ payload }: { payload: PortfolioXrayPayload }) {
  if (payload.gaps.length === 0) return null
  return (
    <div className="flex flex-col gap-1 border-t border-border pt-3">
      <p className="text-caption font-medium text-text-secondary">
        缺口（{payload.gaps.length}）
        <span className="ml-1 font-normal text-text-muted">这些不是 0，是没算</span>
      </p>
      <ul className="flex flex-col gap-0.5 text-caption leading-body text-text-muted">
        {payload.gaps.map((gap, index) => (
          <li key={`${gap.symbol}-${gap.affects}-${index}`}>
            <span className="ldst-num">{gap.name ? `${gap.name} ${gap.symbol}` : gap.symbol}</span>
            <span className="ml-1 shrink-0 rounded border border-border px-1 text-caption">{GAP_LABEL[gap.affects]}</span>{' '}
            ：{gap.reason || MISSING}
          </li>
        ))}
      </ul>
    </div>
  )
}

interface ThresholdEditorProps {
  value: XrayThresholds
  onChange: (next: XrayThresholds) => void
}

/** 提醒线编辑器：填百分比（或 0–1 的系数），留空 = 这一项不提醒。 */
function ThresholdEditor({ value, onChange }: ThresholdEditorProps) {
  const show = (key: ThresholdKey, unit: '%' | 'r') => {
    const current = value[key]
    if (current === null) return ''
    return unit === '%' ? String(Math.round(current * 1000) / 10) : String(current)
  }
  const [draft, setDraft] = useState<Record<ThresholdKey, string>>(() => ({
    single: show('single', '%'),
    top3: show('top3', '%'),
    industry: show('industry', '%'),
    corr: show('corr', 'r'),
  }))
  const [errors, setErrors] = useState<Partial<Record<ThresholdKey, string>>>({})

  const commit = (key: ThresholdKey, unit: '%' | 'r', raw: string) => {
    const text = raw.trim()
    if (text === '') {
      setErrors((prev) => ({ ...prev, [key]: undefined }))
      onChange({ ...value, [key]: null })
      return
    }
    const parsed = Number(text)
    const next = unit === '%' ? parsed / 100 : parsed
    if (!Number.isFinite(parsed) || next <= 0 || next > 1) {
      setErrors((prev) => ({
        ...prev,
        [key]: unit === '%' ? '占比要填 0 到 100 之间的数' : '相关系数要填 0 到 1 之间的数',
      }))
      return
    }
    setErrors((prev) => ({ ...prev, [key]: undefined }))
    onChange({ ...value, [key]: next })
  }

  return (
    <div className="flex flex-col gap-2 rounded border border-border bg-sunken px-3 py-3">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {THRESHOLD_FIELDS.map((field) => (
          <Field
            key={field.key}
            label={`${field.label}${field.unit === '%' ? '（%）' : '（0–1）'}`}
            htmlFor={`xray-threshold-${field.key}`}
            hint={field.hint}
            error={errors[field.key] ?? null}
          >
            <Input
              id={`xray-threshold-${field.key}`}
              aria-label={`${field.label}提醒线`}
              inputMode="decimal"
              placeholder="不提醒"
              value={draft[field.key]}
              invalid={Boolean(errors[field.key])}
              onChange={(event) => setDraft({ ...draft, [field.key]: event.target.value })}
              onBlur={(event) => commit(field.key, field.unit, event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') commit(field.key, field.unit, event.currentTarget.value)
              }}
            />
          </Field>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <Button
          variant="ghost"
          onClick={() => {
            setDraft({
              single: String(Math.round(DEFAULT_THRESHOLDS.single! * 1000) / 10),
              top3: String(Math.round(DEFAULT_THRESHOLDS.top3! * 1000) / 10),
              industry: String(Math.round(DEFAULT_THRESHOLDS.industry! * 1000) / 10),
              corr: String(DEFAULT_THRESHOLDS.corr),
            })
            setErrors({})
            onChange({ ...DEFAULT_THRESHOLDS })
          }}
        >
          恢复起点值
        </Button>
        <p className="text-caption leading-body text-text-muted">
          起点值只是方便第一次用，不是我们的建议；留空就不提醒这一项，改完立刻按新的线重算。
        </p>
      </div>
    </div>
  )
}
