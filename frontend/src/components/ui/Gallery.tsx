import { useState, type ReactNode } from 'react'

import { Badge, SignedNumber, StatusDot } from './Badge'
import { Button, IconButton } from './Button'
import { Card, CardHeader, MetricCard, SectionCard } from './Card'
import { Checkbox, Field, Input, Select, Switch, Textarea } from './Field'
import { Banner, StatePanel } from './Feedback'
import { HBarList, Sparkline } from './MiniChart'
import { Drawer } from './Overlay'
import { Segmented } from './Segmented'
import {
  FreshnessBadge,
  SourceBadges,
  SourceStatusBadge,
  type SourceBadgeMeta,
  worstSourceTone,
} from './SourceBadges'
import { Tabs } from './Tabs'
import { DataTable, type Column, type Expandable } from './Table'

/**
 * 组件层能力自证页（DESIGN-SPEC §6「组装示例」）。
 *
 * 入口：`npm run dev` 后访问 `/ui-kit.html`（`?theme=light` 换浅色，`?drawer=1` 以打开态渲染抽屉）。
 * 这一页不属于任何屏幕，也不从 `index.ts` 导出（不进 App 的依赖图、不进生产包），它只干一件事——
 * 把 QUOT-310 收口的几件事（列宽与截断 / 数字基线 / sticky / `summary` 与展开行 / `StatePanel`
 * 四态 / 过渡档位 / 溯源徽标同源）在一张页面上演示到位，屏幕子单照着抄即可。
 */

/** 溯源徽标只用到 envelope 的公共字段，这里给一份「缓存 + 部分缺失」的演示值。 */
const ENVELOPE_META: SourceBadgeMeta = {
  freshness: 'cached',
  source_status: 'partial',
  source_detail: 'TiData · 主工具未回传，走 peer 缓存',
  data_as_of: '2026-10-30T15:00:00+08:00',
}

interface PoolRow {
  code: string
  name: string
  pct: number
  ladder: number
  note: string
}

const ROWS: PoolRow[] = [
  { code: '600519', name: '贵州茅台', pct: 2.31, ladder: 3, note: '批价企稳 + 中秋备货启动，渠道库存回到健康水位' },
  { code: '300750', name: '宁德时代', pct: -1.08, ladder: 2, note: '锂价反弹，海外产能爬坡' },
  { code: '002594', name: '比亚迪', pct: 4.92, ladder: 5, note: '月销新高' },
  { code: '601899', name: '紫金矿业', pct: 0, ladder: 1, note: '铜金双升，但估值已到年内高位，需要看下一份财报确认' },
  { code: '000001', name: '平安银行', pct: -0.44, ladder: 1, note: '净息差仍在收窄' },
  { code: '688981', name: '中芯国际', pct: 6.15, ladder: 7, note: '先进制程放量 + 大基金三期' },
]

const rowKey = (row: PoolRow) => row.code

const COLUMNS: Array<Column<PoolRow>> = [
  {
    key: 'code',
    header: '代码',
    width: '88px',
    numeric: true,
    render: (row) => row.code,
  },
  { key: 'name', header: '名称', truncate: true, render: (row) => row.name, summary: (rows) => `共 ${rows.length} 只` },
  {
    key: 'pct',
    header: '涨幅',
    width: '104px',
    numeric: true,
    sort: (a, b) => a.pct - b.pct,
    render: (row) => (
      <SignedNumber
        text={`${row.pct > 0 ? '+' : ''}${row.pct.toFixed(2)}%`}
        tone={row.pct > 0 ? 'up' : row.pct < 0 ? 'down' : 'flat'}
        arrow={row.pct > 0 ? '▲' : row.pct < 0 ? '▼' : '—'}
      />
    ),
    summary: (rows) => (rows.reduce((total, row) => total + row.pct, 0) / rows.length).toFixed(2),
  },
  {
    key: 'ladder',
    header: '梯队',
    width: '88px',
    numeric: true,
    render: (row) => <Badge tone={row.ladder >= 3 ? 'warn' : 'neutral'}>{row.ladder} 板</Badge>,
    summary: (rows) => `${rows.filter((row) => row.ladder >= 3).length} 只`,
  },
  {
    key: 'note',
    header: '涨停理由',
    truncate: true,
    cellTitle: (row) => row.note,
    render: (row) => <span className="text-text-secondary">{row.note}</span>,
  },
]

const EXPANDABLE: Expandable<PoolRow> = {
  label: '展开对账明细',
  defaultExpanded: ['688981'],
  render: (row) => (
    <div className="flex flex-wrap items-center gap-4">
      <StatusDot tone={row.pct > 0 ? 'ok' : row.pct < 0 ? 'danger' : 'flat'}>
        对账：{row.pct > 0 ? '符合预期' : row.pct < 0 ? '低于预期' : '持平'}
      </StatusDot>
      <span className="text-caption text-text-secondary">
        触发条件：<span className="ldst-num">{row.code}</span> 收盘 {row.pct.toFixed(2)}%，梯队 {row.ladder}
      </span>
      <Sparkline values={[3, 4, 3, 5, 6, 5, 7, 6]} tone={row.pct >= 0 ? 'up' : 'down'} ariaLabel={`${row.name} 近八日走势`} />
    </div>
  ),
}

function Section({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline gap-2">
        <h2 className="text-section font-semibold text-text">{title}</h2>
        <p className="text-caption text-text-muted">{note}</p>
      </div>
      {children}
    </section>
  )
}

function StateBox({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-2 rounded border border-border bg-surface p-4">
      <p className="text-caption font-medium text-text-secondary">{label}</p>
      {children}
    </div>
  )
}

export function Gallery({ initialDrawerOpen = false }: { initialDrawerOpen?: boolean }) {
  const [density, setDensity] = useState<'compact' | 'default' | 'comfortable'>('default')
  const [tab, setTab] = useState<'pool' | 'review'>('pool')
  const [privacy, setPrivacy] = useState(true)
  const [ack, setAck] = useState(false)
  const [drawer, setDrawer] = useState(initialDrawerOpen)

  return (
    <div className="mx-auto flex w-content max-w-full flex-col gap-5 p-5 text-text">
      <header className="flex flex-wrap items-end justify-between gap-3 border-b border-border pb-4">
        <div className="flex flex-col gap-1">
          <h1 className="text-page font-semibold">组件层能力自证 · QUOT-310</h1>
          <p className="max-w-measure text-body leading-body text-text-secondary">
            本页只使用 <code className="ldst-num">components/ui</code> 的组件与 token 类名：没有硬编码
            hex、没有 arbitrary-value、过渡一律挂 <code className="ldst-num">duration-fast</code>（抽屉与
            Toast 是 §7.3 唯一的 <code className="ldst-num">duration-base</code> 例外）。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusDot tone="ok" pulse>
            本机 API 已连
          </StatusDot>
          <Badge tone="info">1440 × 900 起</Badge>
        </div>
      </header>

      <Section title="一 · DataTable" note="列宽策略 / 数字基线 / sticky 表头与首列 / 合计行 / 展开行，全在组件里定死">
        <Card>
          <CardHeader
            title="涨停池"
            subtitle="横向滚动时首列吸附，纵向滚动时表头吸附，交叉格层级更高"
            actions={
              <Segmented
                ariaLabel="表格密度"
                value={density}
                onChange={setDensity}
                options={[
                  { value: 'compact', label: '紧凑' },
                  { value: 'default', label: '标准' },
                  { value: 'comfortable', label: '宽松' },
                ]}
              />
            }
          />
          <DataTable
            columns={COLUMNS}
            rows={ROWS}
            rowKey={rowKey}
            caption="演示数据（非真实行情）：涨幅列与梯队列右对齐 + 等宽数字；理由列吃剩余宽度并截断，悬浮看全文"
            density={density}
            maxHeight="440px"
            expandable={EXPANDABLE}
            initialSort={{ key: 'pct', dir: 'desc' }}
          />
        </Card>
      </Section>

      <Section title="二 · StatePanel 四态同源" note="屏幕只填文案与出口动作；判定口径与 ?state= 演示态同一个 PageState">
        <div className="grid grid-cols-2 items-start gap-4 desk:grid-cols-4">
          <StateBox label="loading（骨架，区域不消失）">
            <StatePanel state="loading" skeletonRows={4}>
              <p>不会渲染</p>
            </StatePanel>
          </StateBox>
          <StateBox label="empty（必须给出口）">
            <StatePanel
              state="empty"
              empty={{
                compact: true,
                title: '还没有明日条件',
                description: '写下明天要盯的代码，收盘后自动对账。',
                action: (
                  <Button variant="primary" size="md">
                    新增条件
                  </Button>
                ),
              }}
            >
              <p>不会渲染</p>
            </StatePanel>
          </StateBox>
          <StateBox label="error（结论 / 原因 / 代码 / 重试）">
            <StatePanel
              state="error"
              error={{
                title: '涨停池取数失败',
                detail: 'tickhub-cn 未返回当日数据，本机没有可用快照。',
                code: 'HTTP 503',
                onRetry: () => undefined,
              }}
            >
              <p>不会渲染</p>
            </StatePanel>
          </StateBox>
          <StateBox label="partial / offline（降级不等于没有）">
            <StatePanel
              state="partial"
              notice={
                <Banner tone="warn" title="部分数据缺失">
                  涨停池缺 3 只个股的理由，其余照常显示。
                </Banner>
              }
            >
              <MetricCard
                label="池内总数"
                value="42"
                unit="只"
                change={<SignedNumber text="+6" tone="up" arrow="▲" />}
              />
            </StatePanel>
          </StateBox>
        </div>
      </Section>

      <Section title="三 · 控件与过渡" note="hover / focus / press 全在 duration-fast；主按钮一屏只有一个">
        <div className="grid grid-cols-2 gap-4 desk:grid-cols-3">
          <div className="flex flex-col gap-2 rounded border border-border bg-surface p-4">
            <p className="text-caption font-medium text-text-secondary">按钮（primary / secondary / ghost / danger）</p>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="primary">保存条件</Button>
              <Button variant="secondary">取消</Button>
              <Button variant="ghost">高级</Button>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Button variant="danger">删除草稿</Button>
              <Button loading>提交中</Button>
              <IconButton label="刷新数据" variant="secondary" icon={<span aria-hidden="true">↻</span>} />
            </div>
            <Button variant="secondary" disabled disabledReason="需要先选一个交易日">
              生成周报
            </Button>
            <Button variant="secondary" onClick={() => setDrawer(true)}>
              打开抽屉（§7.3 例外：180ms）
            </Button>
          </div>

          <div className="flex flex-col gap-2 rounded border border-border bg-surface p-4">
            <p className="text-caption font-medium text-text-secondary">表单（焦点环走全局 :focus-visible）</p>
            <Field label="证券代码" htmlFor="demo-code" hint="支持 6 位代码或名称模糊匹配">
              <Input id="demo-code" defaultValue="600519" />
            </Field>
            <Field label="对账口径" htmlFor="demo-scope">
              <Select id="demo-scope" defaultValue="close">
                <option value="close">收盘价</option>
                <option value="vwap">均价</option>
              </Select>
            </Field>
            <Field label="备注" htmlFor="demo-note" error="长度超出 200 字">
              <Textarea id="demo-note" defaultValue="批价企稳，但需要下一份财报确认。" />
            </Field>
            <Switch checked={privacy} onChange={setPrivacy} label="隐藏持仓金额" description="仅本机生效，不会写进 URL" />
            <Checkbox checked={ack} onChange={setAck}>本次不真的写入磁盘</Checkbox>
          </div>

          <SectionCard title="分段与页签" subtitle="Segmented 是 radiogroup，Tabs 是同一实体的不同视角">
            <Tabs
              ariaLabel="演示视角"
              value={tab}
              onChange={setTab}
              tabs={[
                { value: 'pool', label: '涨停池' },
                { value: 'review', label: '复盘' },
              ]}
            />
            <div className="flex flex-col gap-3 pt-3">
              <HBarList
                items={[
                  { label: '首板', value: 18, hint: '较昨日 +4' },
                  { label: '二板', value: 9 },
                  { label: '三板及以上', value: 5, hint: '高度 7' },
                ]}
                unit=" 只"
              />
              <Sparkline values={[2, 3, 5, 4, 6, 8, 7, 9]} tone="up" ariaLabel="连板高度近八日" />
            </div>
          </SectionCard>

          <SectionCard
            title="溯源徽标：一份 label map"
            subtitle="§5.1 文字+●/◐/○、§5.2 三态均有文字、§5.3 两枚并排不合并"
          >
            <div className="flex flex-wrap items-center gap-1">
              <FreshnessBadge freshness="realtime" asOf="2026-10-30 15:00" />
              <FreshnessBadge freshness="cached" asOf="2026-10-30 10:24" />
              <FreshnessBadge freshness="archive" asOf="2026-09-30 15:00" />
              <FreshnessBadge freshness="daily" />
            </div>
            <div className="flex flex-wrap items-center gap-1 pt-2">
              <SourceStatusBadge status="ok" />
              <SourceStatusBadge status="degraded" detail="同 peer 备工具回放" />
              <SourceStatusBadge status="partial" detail="缺 3 只封单" />
            </div>
            <div className="pt-3">
              <SourceBadges envelope={ENVELOPE_META} />
            </div>
            <div className="pt-3">
              <Banner
                tone={worstSourceTone(ENVELOPE_META.freshness, ENVELOPE_META.source_status)}
                title="提示条取二者最严重者"
              >
                同一份排序（partial &gt; degraded &gt; cached &gt; realtime）既驱动徽标，也驱动提示条色档。
              </Banner>
            </div>
            <p className="text-caption text-text-muted">
              第 4 枚是未登记的 freshness（radar 的 <code className="ldst-num">daily</code>）：后端 key 不直显，退到「未知」，原值只进悬浮说明。
            </p>
          </SectionCard>
        </div>
      </Section>

      <Drawer
        open={drawer}
        onClose={() => setDrawer(false)}
        title="抽屉：进出场用 duration-base"
        subtitle="480px 宽、Esc 关闭、焦点锁在环内、关闭后回到触发按钮"
        footer={
          <>
            <Button variant="ghost" onClick={() => setDrawer(false)}>
              取消
            </Button>
            <Button variant="primary" onClick={() => setDrawer(false)}>
              确认
            </Button>
          </>
        }
      >
        <DataTable
          columns={[
            { key: 'name', header: '名称', truncate: true, render: (row: PoolRow) => row.name },
            { key: 'pct', header: '涨幅', numeric: true, render: (row: PoolRow) => row.pct.toFixed(2) },
          ]}
          rows={ROWS.slice(0, 3)}
          rowKey={rowKey}
        />
        <p className="pt-3 text-caption text-text-muted">
          抽屉里同样用 DataTable：合计行 / 截断 / 数字基线在 480px 宽度下不需要任何额外补丁。
        </p>
      </Drawer>
    </div>
  )
}
