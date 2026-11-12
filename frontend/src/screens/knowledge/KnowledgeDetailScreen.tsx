import { useMemo } from 'react'
import { Link, useParams } from 'react-router-dom'
import { BookOpen, MessageCircleQuestion } from 'lucide-react'

import {
  MARKET_LABEL,
  STATUS_LABEL,
  describeKnowledgeError,
  isKnowledgeMissing,
  loadKnowledge,
  normalizeKnowledgeMarket,
  normalizeKnowledgeSymbol,
  type KnowledgeDossier,
  type KnowledgeMarket,
  type KnowledgeStatus,
} from '../../api/knowledge'
import { MarkdownView } from '../../components/research/MarkdownView'
import { LinkButton } from '../../components/research/bits'
import { DemoStateBadge, PageHeader } from '../../components/PageShell'
import { Badge, Banner, Card, CardBody, CardHeader, EmptyState, ErrorBlock, SkeletonText, type BadgeTone } from '../../components/ui'
import { buildCitationIndex } from '../../lib/citation'
import { useAsync } from '../../lib/useAsync'
import { useAssistantShell, useRegisterAssistantObject } from '../../lib/assistant/shell'

/**
 * 档案页（`/knowledge/:market/:symbol`，QUOT-567）。
 *
 * 数据只有一条来源：`GET /api/knowledge/:market/:symbol`（QUOT-488）。页面不计算状态、
 * 不补写日期：`status` 是后端按 `as_of + valid_days` 现算的，`stale` / `refuted` 时
 * 顶部横幅必须出现（「数据可能过期」），不能只靠徽标颜色。
 *
 * 正文复用研究面的 `MarkdownView`：同一份解析器、同一套标题 / 列表 / 引用 / 表格、同一套角标，
 * 档案与研究报告读起来是同一类文档。档案没有 provenance 槽位，角标索引给空——正文里
 * 真出现 `[n]` 时渲染成「无来源编号」，不伪造抽屉。
 *
 * 404「档案不存在」是业务空态（还没攒到这只），给出口：去首页研究面板问 AI；其余错误走
 * `describeKnowledgeError` 的同款「结论 / 原因 / 代码 / 重试」。
 */

const STATUS_TONE: Record<KnowledgeStatus, BadgeTone> = {
  fresh: 'ok',
  stale: 'warn',
  refuted: 'danger',
}

/** 过期 / 被推翻时的横幅文案：一句话说清楚「能不能当依据」。 */
const STALE_BANNER: Record<Exclude<KnowledgeStatus, 'fresh'>, { title: string; body: string }> = {
  stale: {
    title: '数据可能过期',
    body: '这份档案已经超过有效期，里面的价格和财务数字未必是现在的样子；当参考可以，别当今天的依据。',
  },
  refuted: {
    title: '结论已被新数据推翻',
    body: '档案里的部分结论后来被更新的数据否定了；看的时候以新数据为准，这份内容只作历史记录。',
  },
}

function validDaysText(dossier: KnowledgeDossier): string {
  return dossier.valid_days === null ? '有效期未登记' : `有效期 ${dossier.valid_days} 天`
}

function asOfText(dossier: KnowledgeDossier): string {
  return dossier.as_of ? `更新于 ${dossier.as_of}` : '更新时间未登记'
}

function DossierCard({ dossier }: { dossier: KnowledgeDossier }) {
  const index = useMemo(() => buildCitationIndex(null), [])
  const stale = dossier.status !== 'fresh' ? STALE_BANNER[dossier.status] : null

  return (
    <div className="flex flex-col gap-4">
      {stale && (
        <Banner tone="warn" title={stale.title}>
          <p className="max-w-measure">{stale.body}</p>
        </Banner>
      )}

      <Card>
        <CardHeader
          title={dossier.name}
          subtitle={`${MARKET_LABEL[dossier.market]} · ${dossier.symbol}`}
          actions={
            <span className="flex flex-wrap items-center gap-1">
              <Badge tone="neutral" title="档案里的数字截至哪一天">
                {asOfText(dossier)}
              </Badge>
              <Badge tone={STATUS_TONE[dossier.status]} title="后端按更新日 + 有效期现算">
                {STATUS_LABEL[dossier.status]}
              </Badge>
              <Badge tone="neutral">{validDaysText(dossier)}</Badge>
            </span>
          }
        />
        <CardBody>
          {dossier.body.trim() ? (
            <MarkdownView markdown={dossier.body} index={index} onCitation={() => {}} />
          ) : (
            <p className="text-body text-text-secondary">这份档案还没有正文，只有上面的时间和状态。</p>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="这些数字从哪来" subtitle="每项抓取的时间与追溯号，出问题时按这个对账" />
        <CardBody>
          {dossier.sources.length === 0 ? (
            <p className="text-body text-text-secondary">档案没有登记来源；这类数字不要当依据。</p>
          ) : (
            <ul className="flex flex-col gap-1.5">
              {dossier.sources.map((source, index) => (
                <li key={index} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-caption text-text-secondary">
                  <Badge tone={source.confidence === 'A' ? 'ok' : 'neutral'}>
                    {source.confidence ? `可信度 ${source.confidence}` : '可信度未标'}
                  </Badge>
                  <span>{source.tool ?? source.source ?? '来源未登记'}</span>
                  {source.fetched_at && <span className="ldst-num text-text-muted">{source.fetched_at}</span>}
                  {source.trace_id && (
                    <code className="ldst-num rounded-xs bg-surface-3 px-1 text-text-muted">{source.trace_id}</code>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

export default function KnowledgeDetailScreen() {
  const params = useParams()
  const market = normalizeKnowledgeMarket(params.market)
  const symbol = normalizeKnowledgeSymbol(params.symbol)
  const shell = useAssistantShell()

  const enabled = market !== null && symbol !== null
  const view = useAsync<KnowledgeDossier>(
    (signal) => loadKnowledge(market as KnowledgeMarket, symbol as string, signal),
    [market, symbol],
    { enabled },
  )

  const dossier = view.status === 'ready' ? view.data : null
  const missing = isKnowledgeMissing(view.error)
  const errorInfo = missing ? null : describeKnowledgeError(view.error)

  useRegisterAssistantObject(
    dossier ? { code: dossier.symbol, name: dossier.name } : symbol ? { code: symbol } : null,
  )

  if (!enabled) {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader title="档案" badge={<DemoStateBadge state={null} />} />
        <Card>
          <CardBody>
            <EmptyState
              icon={<BookOpen aria-hidden="true" />}
              title="地址里的市场或代码不对"
              description="市场只能是 SH / SZ / BJ，代码是 6 位数字。"
              action={
                <LinkButton to="/knowledge" variant="secondary">
                  去查另一只
                </LinkButton>
              }
            />
          </CardBody>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title={dossier ? `${dossier.name}的档案` : '档案'}
        subtitle={`${MARKET_LABEL[market]} · ${symbol}`}
        badge={<DemoStateBadge state={null} />}
        actions={
          <LinkButton to="/knowledge" variant="secondary" leadingIcon={<BookOpen className="size-3.5" aria-hidden="true" />}>
            查另一只
          </LinkButton>
        }
      />

      {view.status === 'loading' && (
        <Card>
          <CardHeader title="正在读取档案" />
          <CardBody>
            <SkeletonText lines={6} />
          </CardBody>
        </Card>
      )}

      {view.status === 'error' && missing && (
        <Card>
          <CardBody>
            <EmptyState
              icon={<BookOpen aria-hidden="true" />}
              title="还没有这只票的档案"
              description={
                <>
                  平台还没攒过 {MARKET_LABEL[market]} {symbol} 的档案。想现在就知道点什么，
                  可以去首页的「研究股票」页签直接问 AI；档案会在研究跑过之后自动出现。
                </>
              }
              action={
                <span className="flex flex-wrap items-center justify-center gap-2">
                  {shell ? (
                    <button
                      type="button"
                      onClick={() =>
                        shell.open({
                          object: { code: symbol },
                          initialDraft: `帮我分析一下 ${symbol} 最近的行情和基本面，有哪些值得注意的风险？`,
                        })
                      }
                      className="inline-flex h-control items-center gap-1 rounded bg-accent-solid px-3 text-body font-medium text-accent-contrast hover:bg-accent-solid-hover"
                    >
                      <MessageCircleQuestion className="size-3.5" aria-hidden="true" />
                      去问 AI
                    </button>
                  ) : (
                    <LinkButton to="/" variant="primary" leadingIcon={<MessageCircleQuestion className="size-3.5" aria-hidden="true" />}>
                      去首页问 AI
                    </LinkButton>
                  )}
                  <LinkButton to="/knowledge" variant="secondary">
                    换一只查
                  </LinkButton>
                </span>
              }
            />
          </CardBody>
        </Card>
      )}

      {view.status === 'error' && errorInfo && (
        <ErrorBlock
          title={errorInfo.title}
          detail={errorInfo.detail}
          code={errorInfo.code}
          onRetry={errorInfo.retryable ? view.reload : undefined}
        />
      )}

      {dossier && <DossierCard dossier={dossier} />}

      <p className="text-caption leading-body text-text-muted">
        档案由平台自动收集整理，只作信息参考，不构成投资建议。<Link to="/knowledge" className="ml-1 underline underline-offset-2">换一只</Link>
      </p>
    </div>
  )
}
