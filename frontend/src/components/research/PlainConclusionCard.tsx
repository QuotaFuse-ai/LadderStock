import { useId, useState, type ReactNode } from 'react'
import { ChevronDown, FileQuestion } from 'lucide-react'

import type { PlainClaim, PlainConclusion } from '../../api/plainConclusion'
import { cn } from '../../lib/cn'
import { Badge, Banner, SectionCard } from '../ui'

/**
 * StockSenate 白话结论卡（QUOT-537，契约 plain-v1）。
 *
 * 只渲染载荷：段落文本、证据、置信度、风险标识、名词小抄、免责声明全部原样展示；
 * 页面不重算指标、不给评级、不补写结论。载荷缺 `plain` 时如实显示降级态。
 */

export interface PlainConclusionCardProps {
  plain: PlainConclusion | null
  disclaimer: string | null
  className?: string
}

/** 置信度按载荷数值展示，不做「高/中/低」评级；小数点后最多 4 位是格式化，不是重算。 */
function confidenceText(value: number): string {
  return value.toFixed(4).replace(/0+$/, '').replace(/\.$/, '')
}

function ClaimRow({ claim }: { claim: PlainClaim }) {
  const [open, setOpen] = useState(false)
  const evidenceId = useId()
  return (
    <li className="flex min-w-0 flex-col gap-1.5">
      <div className="flex flex-wrap items-start gap-2">
        <p className="min-w-0 flex-1 text-body leading-body text-text">{claim.text}</p>
        {claim.neutral && (
          <Badge tone="flat" className="shrink-0">
            中性表述
          </Badge>
        )}
      </div>
      {claim.evidence.length > 0 && (
        <>
          <button
            type="button"
            aria-expanded={open}
            aria-controls={evidenceId}
            onClick={() => setOpen((value) => !value)}
            className="inline-flex w-fit items-center gap-1 rounded-xs text-caption text-info transition-colors duration-fast ease-ldst hover:text-accent-hover"
          >
            <ChevronDown
              aria-hidden="true"
              className={cn('size-3.5 transition-transform duration-fast ease-ldst', open && 'rotate-180')}
            />
            证据 <span className="ldst-num">{claim.evidence.length}</span> 条
          </button>
          {open && (
            <ul id={evidenceId} className="flex flex-col gap-1">
              {claim.evidence.map((citation, index) => (
                <li
                  key={`${citation.domain}-${citation.source}-${citation.data_as_of}-${index}`}
                  className="rounded-xs bg-surface-2 px-2 py-1.5"
                >
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption">
                    <Badge tone="flat">{citation.domain}</Badge>
                    <span className="text-text-secondary">{citation.source}</span>
                    <span className="ldst-num text-text-muted">{citation.data_as_of}</span>
                  </div>
                  {citation.detail && (
                    <p className="mt-1 break-all text-caption text-text-muted">
                      <span className="sr-only">定位：</span>
                      {citation.detail}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </li>
  )
}

function ClaimSection({
  title,
  claims,
  emptyText,
  extra,
}: {
  title: string
  claims: PlainClaim[]
  emptyText: string
  extra?: ReactNode
}) {
  const headingId = useId()
  return (
    <section aria-labelledby={headingId} className="flex min-w-0 flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h3 id={headingId} className="text-body font-semibold text-text">
          {title}
        </h3>
        {extra}
      </div>
      {claims.length > 0 ? (
        <ul className="flex flex-col gap-3">
          {claims.map((claim, index) => (
            <ClaimRow key={`${title}-${index}`} claim={claim} />
          ))}
        </ul>
      ) : (
        <p className="text-body leading-body text-text-muted">{emptyText}</p>
      )}
    </section>
  )
}

export function PlainConclusionCard({ plain, disclaimer, className }: PlainConclusionCardProps) {
  if (!plain) {
    return (
      <SectionCard
        title="白话结论"
        subtitle="后端未透传"
        actions={<Badge tone="flat">降级</Badge>}
        className={className}
      >
        <div className="flex items-start gap-2">
          <FileQuestion aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-text-muted" />
          <div className="min-w-0">
            <p className="text-body font-medium text-text">后端尚未透传白话结论</p>
            <p className="mt-1 text-body leading-body text-text-secondary">
              研究状态载荷里没有 <code className="ldst-num">plain</code> 字段；页面不会自行生成或推测结论。
              后端透传后，这里会按「这是什么 / 为什么 / 风险点」三段与逐条证据展示。
            </p>
            {disclaimer && (
              <p className="mt-2 border-t border-border pt-2 text-caption leading-caption text-text-muted">
                <span className="font-medium text-text-secondary">免责声明：</span>
                {disclaimer}
              </p>
            )}
          </div>
        </div>
      </SectionCard>
    )
  }

  const degradedReasons: string[] = []
  if (plain.degraded) degradedReasons.push('载荷标记 degraded=true，部分表述已做合规降级。')
  if (plain.redline_blocked > 0) {
    degradedReasons.push(`有 ${plain.redline_blocked} 条表述因合规红线被拦截，未出现在下方内容中。`)
  }
  if (plain.omitted_claims > 0) {
    degradedReasons.push(`有 ${plain.omitted_claims} 条论断因缺少可引用证据未展示。`)
  }

  const confidence = confidenceText(plain.confidence)

  return (
    <SectionCard
      title="白话结论"
      subtitle={`研究结论白话版 · ${plain.version}`}
      tone={plain.degraded || degradedReasons.length > 0 ? 'warn' : 'info'}
      actions={<Badge tone={plain.degraded ? 'warn' : 'info'}>只读载荷</Badge>}
      className={className}
    >
      <div className="flex flex-col gap-4">
        {degradedReasons.length > 0 && (
          <Banner tone="warn" title="部分表述已做合规降级">
            <ul className="flex list-disc flex-col gap-1 pl-4">
              {degradedReasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          </Banner>
        )}

        <div className="flex flex-col gap-1.5 rounded border border-border bg-surface-2 p-3">
          <div className="flex flex-wrap items-baseline gap-2">
            <span className="text-caption text-text-secondary">置信度</span>
            <span className="ldst-num text-section font-semibold text-text">{confidence}</span>
            <span className="text-caption text-text-muted">载荷原值 · 取值 0~1</span>
          </div>
          <div
            role="img"
            aria-label={`置信度 ${confidence}（取值 0~1）`}
            className="h-1.5 w-full overflow-hidden rounded-pill bg-flat-soft"
          >
            <div className="h-full rounded-pill bg-accent" style={{ width: `${plain.confidence * 100}%` }} />
          </div>
          {plain.confidence_note && (
            <p className="text-caption leading-caption text-text-secondary">{plain.confidence_note}</p>
          )}
        </div>

        <ClaimSection title="这是什么" claims={[plain.what]} emptyText="载荷缺少「这是什么」论断。" />

        <ClaimSection
          title="为什么"
          claims={plain.why}
          emptyText="载荷中没有可挂证据的「为什么」论断。"
        />

        <ClaimSection
          title="风险点"
          claims={plain.risks}
          emptyText="载荷中没有可挂证据的风险论断。"
          extra={
            plain.risk_flags.length > 0 ? (
              <span className="flex flex-wrap items-center gap-1">
                {plain.risk_flags.map((flag) => (
                  <Badge key={flag} tone="warn">
                    {flag}
                  </Badge>
                ))}
              </span>
            ) : undefined
          }
        />

        {plain.glossary.length > 0 && (
          <details className="rounded border border-border bg-surface-2 px-3 py-2">
            <summary className="cursor-pointer text-caption font-medium text-text-secondary">
              名词小抄（<span className="ldst-num">{plain.glossary.length}</span>）
            </summary>
            <dl className="mt-2 flex flex-col gap-1.5">
              {plain.glossary.map((entry) => (
                <div key={entry.term} className="text-caption leading-caption">
                  <dt className="inline font-medium text-text">{entry.term}</dt>
                  <dd className="inline text-text-secondary">：{entry.note}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}

        <p className="border-t border-border pt-3 text-caption leading-caption text-text-muted">
          <span className="font-medium text-text-secondary">免责声明：</span>
          {disclaimer ?? '载荷未提供免责声明'}
        </p>
      </div>
    </SectionCard>
  )
}
