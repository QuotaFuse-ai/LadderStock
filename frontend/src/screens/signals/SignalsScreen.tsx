import { useMemo, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Radar, RefreshCw, ScrollText } from 'lucide-react'

import type { IndustrySignal, SignalEvidence, SignalRule } from '../../api/industry'
import { DemoStateBadge, OfflineBanner, PageHeader } from '../../components/PageShell'
import { LinkButton } from '../../components/research/bits'
import {
  Badge,
  Banner,
  Button,
  Card,
  DataTable,
  Drawer,
  EmptyState,
  ErrorBlock,
  Field,
  MetricCard,
  SectionCard,
  Segmented,
  Select,
  Skeleton,
  SkeletonRows,
  type Column,
  type Expandable,
} from '../../components/ui'
import { formatInt, MISSING } from '../../lib/format'
import { formatEvidenceValue } from '../industry/metrics'
import { withParams } from '../industry/links'
import { useSignalsView } from '../industry/useIndustryView'

/**
 * P-R9-03 产业信号（`/signals`）。
 *
 * 信号由后端基于 R5 指标判定（DEC-028），前端只做筛选与呈现；
 * 每条信号都能展开证据链：字段路径 + 当前值 + 比较符/阈值 + 时点 + 数据集。
 * 证据不完整（缺时点/数据集/阈值）的条目在解析层丢弃，页面用 partial 横幅说明条数。
 */

type DirectionFilter = 'all' | 'bullish' | 'bearish'

const DIRECTION_OPTIONS: ReadonlyArray<{ value: DirectionFilter; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'bullish', label: '偏多' },
  { value: 'bearish', label: '偏空' },
]

const SEVERITY_LABEL: Record<IndustrySignal['severity'], string> = {
  info: '提示',
  warn: '关注',
  // 三档级别以 UI-SPEC §3.2 的词表为准（提示 / 关注 / 强信号），本屏不自造「风险」这第三说
  danger: '强信号',
}

const SEVERITY_TONE: Record<IndustrySignal['severity'], 'info' | 'warn' | 'danger'> = {
  info: 'info',
  warn: 'warn',
  danger: 'danger',
}

function directionLabel(direction: IndustrySignal['direction']): string {
  return direction === 'bullish' ? '偏多' : '偏空'
}

function asDirection(value: string | null): DirectionFilter {
  return value === 'bullish' || value === 'bearish' ? value : 'all'
}

const EVIDENCE_COLUMNS: Array<Column<SignalEvidence>> = [
  {
    key: 'field',
    header: '字段',
    render: (row) => (
      <span className="flex min-w-0 flex-col leading-tight">
        <span className="text-text">{row.label}</span>
        <code className="ldst-num break-all text-caption text-text-muted">{row.path}</code>
      </span>
    ),
  },
  {
    key: 'value',
    header: '当前值 / 阈值',
    align: 'right',
    width: '152px',
    render: (row) => (
      <span className="ldst-num flex flex-col items-end leading-tight text-text">
        <span>{formatEvidenceValue(row.value, row.unit)}</span>
        <span className="text-caption text-text-muted">
          {row.comparator} {formatEvidenceValue(row.threshold, row.unit)}
        </span>
      </span>
    ),
  },
  {
    key: 'passed',
    header: '结论',
    align: 'center',
    width: '72px',
    render: (row) => (
      <Badge tone={row.passed ? 'ok' : 'danger'}>{row.passed ? '成立' : '未成立'}</Badge>
    ),
  },
  {
    key: 'asOf',
    header: '时点 / 数据集',
    align: 'right',
    width: '152px',
    render: (row) => (
      <span className="flex min-w-0 flex-col items-end leading-tight">
        <span className="ldst-num text-caption text-text-secondary">{row.as_of}</span>
        <code className="ldst-num break-all text-caption text-text-muted">{row.dataset}</code>
      </span>
    ),
  },
]

export default function SignalsScreen() {
  const [params, setParams] = useSearchParams()
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const direction = asDirection(params.get('direction'))
  const rule = params.get('rule')
  const industry = params.get('industry')

  const view = useSignalsView({ industry, rule })
  const { demo, forced, state, payload, error, reload } = view

  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next, { replace: true })
  }

  // 演示开关走真锚点：`href="/signals?demo=1"` 会把当前筛选整串丢掉并触发整页重载
  const demoHref = useMemo(() => {
    const next = new URLSearchParams(params)
    next.set('demo', '1')
    return `/signals?${next.toString()}`
  }, [params])

  const rules = payload?.rules ?? []
  const signals = payload?.signals ?? []

  const filtersActive = direction !== 'all' || rule !== null || industry !== null

  const clearFilters = () => {
    const next = new URLSearchParams(params)
    for (const key of ['direction', 'rule', 'industry']) next.delete(key)
    setParams(next, { replace: true })
  }

  const visible = useMemo(
    () => signals.filter((signal) => direction === 'all' || signal.direction === direction),
    [signals, direction],
  )

  const industries = useMemo(
    () => [...new Set(signals.map((signal) => signal.industry))].sort((a, b) => a.localeCompare(b, 'zh-CN')),
    [signals],
  )

  const selected = useMemo(
    () => signals.find((signal) => signal.id === selectedId) ?? null,
    [signals, selectedId],
  )

  const selectedRule = selected ? rules.find((item) => item.id === selected.rule_id) ?? null : null

  const columns = useMemo<Array<Column<IndustrySignal>>>(
    () => [
      {
        key: 'industry',
        header: '行业',
        width: '96px',
        sort: (a, b) => a.industry.localeCompare(b.industry, 'zh-CN'),
        render: (row) => (
          <Link
            to={withParams(`/industry/${encodeURIComponent(row.industry)}`, { demo: demo ? '1' : null })}
            onClick={(event) => event.stopPropagation()}
            title={row.industry}
            className="line-clamp-1 font-medium text-text underline-offset-2 transition-colors duration-fast ease-ldst hover:text-accent-hover hover:underline"
          >
            {row.industry}
          </Link>
        ),
      },
      {
        key: 'rule',
        header: '规则',
        width: '132px',
        sort: (a, b) => a.rule_title.localeCompare(b.rule_title, 'zh-CN'),
        render: (row) => <span className="line-clamp-1 text-text-secondary" title={row.rule_title}>{row.rule_title}</span>,
      },
      {
        key: 'direction',
        header: '方向',
        align: 'center',
        width: '56px',
        sort: (a, b) => a.direction.localeCompare(b.direction),
        render: (row) => (
          <Badge tone={row.direction === 'bullish' ? 'up' : 'down'}>{directionLabel(row.direction)}</Badge>
        ),
      },
      {
        key: 'severity',
        header: '级别',
        align: 'center',
        width: '56px',
        render: (row) => <Badge tone={SEVERITY_TONE[row.severity]}>{SEVERITY_LABEL[row.severity]}</Badge>,
      },
      {
        key: 'evidence',
        header: '证据',
        align: 'right',
        width: '56px',
        render: (row) => (
          <span className="ldst-num text-text-secondary">
            {row.evidence.filter((item) => item.passed).length}/{row.evidence.length}
          </span>
        ),
      },
      {
        key: 'asOf',
        header: '时点',
        align: 'right',
        width: '92px',
        sort: (a, b) => a.as_of.localeCompare(b.as_of),
        render: (row) => (
          <span className="ldst-num whitespace-nowrap text-text-secondary">{row.as_of}</span>
        ),
      },
      {
        key: 'action',
        header: '',
        align: 'right',
        width: '72px',
        render: (row) => (
          <button
            type="button"
            aria-haspopup="dialog"
            onClick={(event) => {
              event.stopPropagation()
              setSelectedId(row.id)
            }}
            className="whitespace-nowrap rounded-xs px-1 text-caption font-medium text-accent underline-offset-2 transition-colors duration-fast ease-ldst hover:underline"
          >
            查看证据
          </button>
        ),
      },
    ],
    [demo],
  )

  const header = (
    <PageHeader
      title="产业信号"
      subtitle={`规则判定基于 R5 指标 · 数据时点 ${payload?.as_of ?? MISSING} · 命中 ${formatInt(payload?.counts.total)} 条`}
      badge={<DemoStateBadge state={forced} />}
      actions={
        <Button
          variant="primary"
          onClick={reload}
          disabled={state === 'loading'}
          leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
        >
          刷新
        </Button>
      }
    />
  )

  if (state === 'loading') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <SignalsSkeleton />
      </div>
    )
  }

  if (state === 'offline') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <OfflineBanner onRetry={reload} />
        <Card>
          <EmptyState
            icon={<Radar aria-hidden="true" />}
            title="本地服务未连接"
            description="启动 ldst run 后自动接入；未连接时不展示任何信号。"
            action={<Button variant="secondary" onClick={reload}>重新检查</Button>}
          />
        </Card>
      </div>
    )
  }

  if (state === 'error') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        <ErrorBlock
          variant="banner"
          title="产业信号取数失败"
          detail={error instanceof Error ? error.message : '未知错误'}
          onRetry={reload}
        />
      </div>
    )
  }

  if (state === 'empty') {
    return (
      <div className="flex flex-col gap-5">
        {header}
        {/* 与正常态同一段 12 栏：空态卡占左 8 栏、规则卡仍占右 4 栏，切态时不跳版 */}
        <div className="grid grid-cols-1 gap-4 desk:grid-cols-12">
          <div className={rules.length > 0 ? 'desk:col-span-8' : 'desk:col-span-12'}>
            <Card>
              <EmptyState
                icon={<Radar aria-hidden="true" />}
                title={payload === null ? '接口未接入' : '没有命中的规则信号'}
                description={
                  payload === null ? (
                    <>
                      页面已按 <code className="ldst-num">GET /api/signals</code> 契约实现；规则判定在后端执行（DEC-028），
                      前端不自行计算信号。接口上线后自动接入。
                    </>
                  ) : (
                    <>
                      {payload.reason ?? '当前 R5 数据没有触发任何规则。'}
                      规则由后端基于五维指标判定，缺数据时不产生信号。
                    </>
                  )
                }
                action={
                  <div className="flex flex-wrap items-center justify-center gap-2">
                    <Button variant="secondary" onClick={reload}>刷新</Button>
                    {!demo && <LinkButton to={demoHref}>查看演示数据</LinkButton>}
                  </div>
                }
              />
            </Card>
          </div>
          {rules.length > 0 && (
            <aside className="flex flex-col gap-4 desk:col-span-4">
              <RulesCard rules={rules} signals={signals} />
            </aside>
          )}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      {header}

      {state === 'partial' && (
        <Banner tone="warn" title="部分信号证据链不完整">
          {payload?.dropped ?? 0} 条信号缺少字段、时点或数据集，已在解析层丢弃——不展示半条证据，也不猜缺失值。
        </Banner>
      )}

      <div className="grid grid-cols-3 gap-4">
        {/*
          三张卡都不带 `note`：`MetricCard` 固定 `h-metric`（96px）只装得下「标签 + 数值」两行，
          第三行 `note` 会压到卡片下沿之外（审计已记为 ui 侧 R-项）。规则条数改由右栏副标题承载。
        */}
        <MetricCard label="命中信号" hero value={payload?.counts.total ?? 0} unit="条" />
        <MetricCard label="偏多" value={payload?.counts.bullish ?? 0} unit="条" />
        <MetricCard label="偏空" value={payload?.counts.bearish ?? 0} unit="条" />
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Segmented
          ariaLabel="按方向筛选"
          options={DIRECTION_OPTIONS}
          value={direction}
          onChange={(next) => setParam('direction', next === 'all' ? null : next)}
        />
        {/* C-06 Field：标签与控件的绑定、说明位都在组件里，屏侧不再手写 <label> 外壳 */}
        <Field label="规则" htmlFor="signal-rule-filter" className="w-56">
          <Select
            id="signal-rule-filter"
            value={rule ?? ''}
            onChange={(event) => setParam('rule', event.target.value || null)}
          >
            <option value="">全部规则</option>
            {rules.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="行业" htmlFor="signal-industry-filter" className="w-40">
          <Select
            id="signal-industry-filter"
            value={industry ?? ''}
            onChange={(event) => setParam('industry', event.target.value || null)}
          >
            <option value="">全部行业</option>
            {industries.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grow" />
        <span className="ldst-num text-caption text-text-muted">
          {visible.length} 条可见 · 点击行或「查看证据」展开
        </span>
      </div>

      <div className="grid grid-cols-1 gap-4 desk:grid-cols-12">
        <div className="desk:col-span-8">
          <DataTable<IndustrySignal>
            columns={columns}
            rows={visible}
            rowKey={(row) => row.id}
            caption="信号由后端基于 R5 五维指标判定；每条都能回溯到字段与时点"
            density="compact"
            maxHeight="calc(100vh - 380px)"
            onRowClick={(row) => setSelectedId(row.id)}
            initialSort={{ key: 'asOf', dir: 'desc' }}
            empty={
              <EmptyState
                compact
                title="当前筛选没有信号"
                description={
                  filtersActive
                    ? '方向 / 规则 / 行业三个筛选叠加后没有命中；清空即可回到全部信号。'
                    : '后端本次没有返回任何命中记录；可刷新重取，或切到演示数据看完整交互。'
                }
                action={
                  filtersActive ? (
                    <Button variant="secondary" onClick={clearFilters}>清空筛选</Button>
                  ) : (
                    <Button variant="secondary" onClick={reload}>刷新</Button>
                  )
                }
              />
            }
          />
        </div>
        <aside className="flex flex-col gap-4 desk:col-span-4">
          <RulesCard rules={rules} signals={signals} />
        </aside>
      </div>

      <Drawer
        open={selected !== null}
        onClose={() => setSelectedId(null)}
        title={selected ? `证据链 · ${selected.industry}` : '证据链'}
        subtitle={selected ? `${selected.rule_title} · 判定时点 ${selected.as_of}` : undefined}
        width="lg"
        footer={
          selected ? (
            <div className="flex items-center gap-2">
              <LinkButton to={withParams(`/industry/${encodeURIComponent(selected.industry)}`, { demo: demo ? '1' : null })}>
                打开行业详情
              </LinkButton>
              <Button variant="primary" onClick={() => setSelectedId(null)}>
                关闭
              </Button>
            </div>
          ) : undefined
        }
      >
        {selected && (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2">
              <Badge tone={selected.direction === 'bullish' ? 'up' : 'down'}>
                {directionLabel(selected.direction)}
              </Badge>
              <Badge tone={SEVERITY_TONE[selected.severity]}>{SEVERITY_LABEL[selected.severity]}</Badge>
              <span className="ldst-num text-caption text-text-muted">
                {selected.evidence.filter((item) => item.passed).length}/{selected.evidence.length} 条证据成立
              </span>
            </div>

            {selectedRule && (
              <div className="rounded border border-border bg-surface-2 p-3">
                <p className="text-caption text-text-secondary">规则表达式</p>
                <code className="ldst-num mt-1 block break-all text-body text-text">{selectedRule.expression}</code>
              </div>
            )}

            <div>
              <p className="text-caption text-text-secondary">信号摘要</p>
              <p className="text-body leading-body text-text">{selected.summary || MISSING}</p>
            </div>

            <DataTable<SignalEvidence>
              columns={EVIDENCE_COLUMNS}
              rows={selected.evidence}
              rowKey={(row) => `${row.path}-${row.as_of}`}
              caption="证据链逐条回溯：字段路径 → 当前值 → 比较符/阈值 → 时点 → 数据集；判定由后端执行（DEC-028）"
              density="compact"
            />

            <p className="text-caption leading-body text-text-muted">
              时点来自各数据集自身的 as_of，不拿请求日期冒充；任一证据缺字段或时点，整条信号不会展示。
            </p>
          </div>
        )}
      </Drawer>
    </div>
  )
}

function RulesCard({
  rules,
  signals,
}: {
  rules: SignalRule[]
  signals: IndustrySignal[]
}) {
  const hits = useMemo(() => {
    const map = new Map<string, number>()
    for (const signal of signals) map.set(signal.rule_id, (map.get(signal.rule_id) ?? 0) + 1)
    return map
  }, [signals])

  const columns = useMemo<Array<Column<SignalRule>>>(
    () => [
      {
        key: 'title',
        header: '规则',
        truncate: true,
        sort: (a, b) => a.title.localeCompare(b.title, 'zh-CN'),
        render: (row) => row.title,
      },
      {
        key: 'direction',
        header: '方向',
        align: 'center',
        width: '56px',
        sort: (a, b) => a.direction.localeCompare(b.direction),
        render: (row) => <Badge tone={row.direction === 'bullish' ? 'up' : 'down'}>{directionLabel(row.direction)}</Badge>,
        cellTitle: (row) => directionLabel(row.direction),
      },
      {
        key: 'hits',
        header: '命中',
        numeric: true,
        width: '48px',
        sort: (a, b) => (hits.get(a.id) ?? 0) - (hits.get(b.id) ?? 0),
        render: (row) => hits.get(row.id) ?? 0,
      },
    ],
    [hits],
  )

  const expandable: Expandable<SignalRule> = {
    label: '展开规则表达式',
    render: (row) => (
      <code className="ldst-num block break-words text-caption leading-body text-text-secondary">
        {row.expression || '未给出表达式'}
      </code>
    ),
  }

  return (
    <SectionCard
      title="规则说明"
      subtitle={rules.length > 0 ? `后端判定，前端只读 · ${rules.length} 条参与判定` : '后端判定，前端只读'}
      actions={<ScrollText className="size-4 text-text-muted" aria-hidden="true" />}
      bodyClassName="p-0"
    >
      {rules.length === 0 ? (
        <EmptyState compact title="接口未返回规则清单" description="规则与阈值应在 /api/signals 的 rules 字段中给出。" />
      ) : (
        <DataTable<SignalRule>
          columns={columns}
          rows={rules}
          rowKey={(row) => row.id}
          caption="点行首展开规则表达式；命中数按本次 payload 统计"
          density="compact"
          stickyFirstColumn={false}
          expandable={expandable}
          initialSort={{ key: 'hits', dir: 'desc' }}
        />
      )}
    </SectionCard>
  )
}

/**
 * 整屏 loading 骨架：列数与栅格与正常态一一对应（§10「骨架与真实内容形状同构」）。
 * `PageShell.ScreenSkeleton` 是 5 列指标阵，与本屏的 3 列 + 右栏规则表不同形，所以在本屏内给。
 */
function SignalsSkeleton() {
  return (
    <div className="flex flex-col gap-5" aria-busy="true" aria-live="polite">
      <span className="sr-only">正在加载…</span>
      <div className="grid grid-cols-3 gap-4">
        {[0, 1, 2].map((index) => (
          <div key={index} className="flex h-metric flex-col justify-between rounded border border-border bg-surface p-4">
            <Skeleton className="w-1/2" />
            <Skeleton className="h-6 w-2/3" />
            <Skeleton className="w-1/3" />
          </div>
        ))}
      </div>
      <div className="flex items-end gap-3">
        <Skeleton className="h-control w-56" />
        <Skeleton className="h-control w-40" />
      </div>
      <div className="grid grid-cols-1 gap-4 desk:grid-cols-12">
        <div className="desk:col-span-8">
          <div className="rounded border border-border bg-surface">
            <SkeletonRows rows={6} slots={['16%', '34%', '12%', '12%', '10%']} />
          </div>
        </div>
        <aside className="desk:col-span-4">
          <div className="rounded border border-border bg-surface">
            <SkeletonRows rows={4} slots={['52%', '20%', '16%']} />
          </div>
        </aside>
      </div>
    </div>
  )
}
