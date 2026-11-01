import { ExternalLink } from 'lucide-react'

import type { ProvenanceSource } from '../../api/research'
import { sourceLocator, unverifiedReason, type CitationIndex } from '../../lib/citation'
import { cn } from '../../lib/cn'
import { MISSING, formatTime } from '../../lib/format'
import { Badge, Drawer, EmptyState } from '../ui'
import { NUMBER_CHIP } from './bits'

/**
 * 来源抽屉：`来源 · endpoint · as_of · url` 四件套（验收原文）。
 *
 * 角标编号 = `sources[]` 的列表序号；`verified=false` 的条目不进入角标索引，
 * 但仍列出来并明确标注「未核实」，让读者知道它为什么不支撑正文数字。
 */

export interface ProvenanceDrawerProps {
  open: boolean
  onClose: () => void
  stageTitle: string
  /** 从角标点进来的编号；从「来源」按钮打开时为 null */
  citation: number | null
  index: CitationIndex
}

function safeHref(value: string | null | undefined): string | null {
  return value && /^https?:\/\//i.test(value) ? value : null
}

/**
 * 一行一条元数据。原来是 `grid-cols-[64px_minmax(0,1fr)]` 的任意值列宽，
 * 改成标签列走间距刻度（`w-16` = 64px，正好是原值），值列 `min-w-0` 吃剩余宽度。
 */
function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 text-caption">
      <dt className="w-16 shrink-0 text-text-muted">{label}</dt>
      <dd className="min-w-0 break-all text-text-secondary">{children}</dd>
    </div>
  )
}

function SourceItem({
  source,
  number,
  selected,
}: {
  source: ProvenanceSource
  number: number
  selected: boolean
}) {
  const href = safeHref(source.url)
  return (
    <li
      className={cn(
        'flex flex-col gap-2 rounded px-2 py-3',
        selected && 'bg-info-soft',
      )}
      aria-current={selected ? 'true' : undefined}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn(NUMBER_CHIP, 'ldst-num bg-surface-3 text-text')}>[{number}]</span>
        <span className="text-body font-medium text-text">{source.dataset}</span>
        {source.verified ? (
          <Badge tone="ok">已核实</Badge>
        ) : (
          <Badge tone="warn" title={unverifiedReason(source)}>
            未核实
          </Badge>
        )}
      </div>
      <dl className="flex flex-col gap-1">
        <Row label="endpoint">
          <span className="ldst-num">{sourceLocator(source) ?? MISSING}</span>
        </Row>
        <Row label="as_of">{source.as_of ?? MISSING}</Row>
        <Row label="取数">
          {source.retrieved_at ? formatTime(source.retrieved_at) : MISSING}
        </Row>
        <Row label="取值">
          {source.value === null || source.value === undefined ? MISSING : String(source.value)}
          {source.unit ? ` ${source.unit}` : ''}
        </Row>
        <Row label="url">
          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noreferrer noopener"
              className="inline-flex items-center gap-1 text-info underline underline-offset-2"
            >
              打开原始地址
              <ExternalLink className="size-3" aria-hidden="true" />
            </a>
          ) : (
            MISSING
          )}
        </Row>
      </dl>
    </li>
  )
}

export function ProvenanceDrawer({
  open,
  onClose,
  stageTitle,
  citation,
  index,
}: ProvenanceDrawerProps) {
  // 角标编号对应 `sources[]` 的位置；未核实条目也占编号，避免编号与列表错位。
  const selected = citation === null ? null : index.sources[citation - 1] ?? null
  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={`来源 · ${stageTitle}`}
      subtitle={`共 ${index.sources.length} 条 · 已核实 ${index.byNumber.size} 条 · 未核实 ${index.unverifiedSources.length} 条`}
      width="lg"
    >
      {selected && citation !== null && (
        <div className="mb-3 rounded border border-info bg-info-soft px-3 py-2 text-body">
          <p className="font-medium text-info">
            编号 [{citation}] · {selected.dataset}
          </p>
          <p className="mt-1 text-caption text-text-secondary">
            角标只引用 `verified=true` 的来源；这条是正文 `[{citation}]` 的依据。
          </p>
        </div>
      )}
      {!selected && citation !== null && (
        <div className="mb-3 rounded border border-warn bg-warn-soft px-3 py-2 text-body text-warn">
          编号 [{citation}] 没有对应的已核实来源，正文该处不应作为数字依据。
        </div>
      )}

      {index.sources.length === 0 ? (
        <EmptyState
          title="本段没有引用来源"
          description="正文中的数字一律标〔未核实〕。可以把这份报告当作阅读材料，但不能作为审计依据。"
        />
      ) : (
        <ul className="flex flex-col divide-y divide-border">
          {index.sources.map((source, position) => (
            <SourceItem
              key={`${source.id}-${position}`}
              source={source}
              number={position + 1}
              selected={citation === position + 1}
            />
          ))}
        </ul>
      )}

      <p className="mt-4 border-t border-border pt-3 text-caption leading-body text-text-muted">
        编号与 `sources[]` 的列表顺序一致；`verified=false` 的条目不进入角标索引，
        正文对应位置显示〔未核实〕。页面不重算、不补齐、不排序来源。
      </p>
    </Drawer>
  )
}
