import { useEffect, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { BookOpen, Search } from 'lucide-react'

import {
  KNOWLEDGE_MARKETS,
  MARKET_LABEL,
  STATUS_LABEL,
  inferKnowledgeMarket,
  loadKnowledgeIndex,
  normalizeKnowledgeMarket,
  normalizeKnowledgeSymbol,
  type KnowledgeIndex,
  type KnowledgeMarket,
  type KnowledgeStatus,
} from '../../api/knowledge'
import { DemoStateBadge, PageHeader } from '../../components/PageShell'
import { Badge, Button, Card, CardBody, CardHeader, EmptyState, Field, Input, Select } from '../../components/ui'

/**
 * 知识库落地页（`/knowledge`，QUOT-567）。
 *
 * 三块，缺一块用户就看不懂这页是干什么的：
 * 1. 查一只票的档案（输入代码 → 跳详情）；
 * 2. 平台已经攒下的档案（`GET /api/knowledge` 清单，点一行直达）——
 *    没有这块时，用户只知道有个搜索框，不知道库里到底有没有东西；
 * 3. 「档案里有什么、怎么读」的分类说明——状态词（有效 / 可能过期 / 已被推翻）
 *    和四类内容都在这页讲清，详情页就不必再教学。
 *
 * 文案面向普通用户：不出现 manifest / frontmatter / schema 这类词，
 * 说的是「平台自动攒的、多久更新一次、过期了会怎样」。
 */

const INFER_HINT: Record<KnowledgeMarket, string> = {
  SH: '已按代码推断为沪市',
  SZ: '已按代码推断为深市',
  BJ: '已按代码推断为北交所',
}

const STATUS_TONE: Record<KnowledgeStatus, 'ok' | 'warn' | 'danger'> = {
  fresh: 'ok',
  stale: 'warn',
  refuted: 'danger',
}

/** 档案的四类内容：详情页正文就是按这个顺序组织的。 */
const DOSSIER_SECTIONS: readonly { title: string; body: string }[] = [
  { title: '涨跌与价格', body: '最近收盘、日线走势、成交量。判断“这两天发生了什么”看这一段。' },
  { title: '财务与经营', body: '营收利润、毛利率、负债与现金流。判断“生意本身好不好”看这一段。' },
  { title: '结论与判断', body: '平台之前得出的看法，以及这个看法的有效期——过期就明确标出来。' },
  { title: '出处与时间', body: '每个数字来自哪个数据源、哪天取的、可信度几档。没有出处的数字不写。' },
]

export default function KnowledgeEntryScreen() {
  const navigate = useNavigate()
  const [raw, setRaw] = useState('')
  const [market, setMarket] = useState<KnowledgeMarket | 'auto'>('auto')
  const [error, setError] = useState<string | null>(null)
  // 清单：读不到不影响主表单，只在这一块显示读取失败
  const [index, setIndex] = useState<KnowledgeIndex | null>(null)
  const [indexError, setIndexError] = useState<string | null>(null)

  const code = normalizeKnowledgeSymbol(raw)
  const inferred = inferKnowledgeMarket(code)
  // 用户没手选时跟着代码走；选过就一直是用户的选择，换代码也不改
  const effective = market === 'auto' ? inferred : normalizeKnowledgeMarket(market)

  useEffect(() => {
    const controller = new AbortController()
    loadKnowledgeIndex(controller.signal)
      .then((result) => {
        if (!controller.signal.aborted) setIndex(result)
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return
        setIndexError(err instanceof Error ? err.message : String(err))
      })
    return () => controller.abort()
  }, [])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!code) {
      setError('请输入 6 位数字代码，例如 600000。')
      return
    }
    if (!effective) {
      setError('认不出这只票属于哪个市场，请在上面手动选择。')
      return
    }
    navigate(`/knowledge/${effective}/${code}`)
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="知识库"
        subtitle="平台攒下的标的档案：攒了什么、什么时候攒的、现在还能不能当依据"
        badge={<DemoStateBadge state={null} />}
      />

      <Card>
        <CardHeader
          title="查一只票的档案"
          subtitle="输入代码就能看；没攒过的会告诉你下一步去哪问"
        />
        <CardBody className="flex flex-col gap-4">
          <p className="max-w-measure text-body leading-body text-text-secondary">
            档案是平台平时自动攒下来的：这只票最近的价格、财务数字、以前记下来的结论，
            以及每项数字的出处。打开后先看顶上的「更新于哪天」——太久没更新，页面会明确提醒你先别当依据。
          </p>

          <form onSubmit={submit} className="flex flex-col gap-3">
            <div className="flex flex-wrap items-end gap-3">
              <Field
                label="证券代码"
                htmlFor="knowledge-code"
                required
                className="w-40"
                error={error}
                hint="6 位数字，例如 600000。"
              >
                <Input
                  id="knowledge-code"
                  name="code"
                  inputMode="numeric"
                  value={raw}
                  invalid={error !== null}
                  placeholder="600000"
                  autoComplete="off"
                  spellCheck={false}
                  leadingIcon={<Search className="size-4" aria-hidden="true" />}
                  onChange={(event) => {
                    setRaw(event.target.value)
                    if (error) setError(null)
                  }}
                />
              </Field>
              <Field
                label="市场"
                htmlFor="knowledge-market"
                className="w-48"
                hint={effective ? INFER_HINT[effective] : '先输代码，或直接选市场'}
              >
                <Select
                  id="knowledge-market"
                  value={market}
                  onChange={(event) => {
                    setMarket(event.target.value as KnowledgeMarket | 'auto')
                    if (error) setError(null)
                  }}
                >
                  <option value="auto">自动判断</option>
                  {KNOWLEDGE_MARKETS.map((item) => (
                    <option key={item} value={item}>
                      {MARKET_LABEL[item]}（{item}）
                    </option>
                  ))}
                </Select>
              </Field>
            </div>
            <div className="flex items-center gap-2">
              <Button
                type="submit"
                variant="primary"
                leadingIcon={<BookOpen className="size-3.5" aria-hidden="true" />}
              >
                看档案
              </Button>
            </div>
          </form>

          <ul className="flex flex-col gap-1 border-t border-border pt-3 text-caption leading-body text-text-muted">
            <li>· 沪市 60 / 68 开头，深市 00 / 30 开头，北交所 8 / 4 开头；认不出来时手动选一下即可。</li>
            <li>· 档案由平台自动生成和更新，不含你输入的意图，也不含任何持仓。</li>
          </ul>
        </CardBody>
      </Card>

      <Card>
        <CardHeader
          title="平台已经攒下的档案"
          subtitle={
            index
              ? `共 ${index.count} 只；点一行直接打开`
              : indexError
                ? '清单读取失败，下面的搜索框仍然可用'
                : '正在读清单…'
          }
        />
        <CardBody className="flex flex-col gap-3">
          {indexError ? (
            <p className="text-body leading-body text-text-secondary">
              清单读不到（{indexError}）。这不影响你按代码查：上面输入代码仍然能打开已知的档案。
            </p>
          ) : !index ? (
            <p className="text-body leading-body text-text-muted">正在读清单…</p>
          ) : index.items.length === 0 ? (
            <EmptyState
              compact
              title="还没有攒下任何档案"
              description="档案由「知识库刷新」任务按需生成：先在上面按代码查一次，或等后台任务跑完再回来。没攒到的时候平台不会编一份假的给你。"
            />
          ) : (
            <ul className="flex flex-col divide-y divide-border">
              {index.items.map((item) => (
                <li key={`${item.market}:${item.symbol}`}>
                  <button
                    type="button"
                    data-testid={`knowledge-row-${item.market}-${item.symbol}`}
                    onClick={() => navigate(`/knowledge/${item.market}/${item.symbol}`)}
                    className="flex w-full items-center justify-between gap-3 py-2 text-left transition-colors duration-fast hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="text-caption text-text-muted">{MARKET_LABEL[item.market]}</span>
                      <span className="font-mono text-body font-medium text-text">{item.symbol}</span>
                      <span className="truncate text-caption text-text-secondary">
                        {item.name || '名称待补'}
                      </span>
                    </span>
                    <span className="flex shrink-0 items-center gap-2">
                      {item.status ? (
                        <Badge tone={STATUS_TONE[item.status]}>{STATUS_LABEL[item.status]}</Badge>
                      ) : null}
                      {!item.has_profile ? <Badge tone="warn">还没成档案</Badge> : null}
                      <span className="text-caption text-text-muted">更新于 {item.as_of || '—'}</span>
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </CardBody>
      </Card>

      <Card>
        <CardHeader title="档案里有什么，怎么读" subtitle="四类内容 + 三种状态，看懂这两样就够用" />
        <CardBody className="flex flex-col gap-4">
          <ul className="grid gap-3 sm:grid-cols-2">
            {DOSSIER_SECTIONS.map((section) => (
              <li key={section.title} className="rounded border border-border bg-surface-2 p-3">
                <p className="text-body font-medium text-text">{section.title}</p>
                <p className="mt-1 text-caption leading-body text-text-secondary">{section.body}</p>
              </li>
            ))}
          </ul>

          <div className="flex flex-col gap-2 border-t border-border pt-3">
            <p className="text-body font-medium text-text">顶上的状态怎么读</p>
            <ul className="flex flex-col gap-1 text-caption leading-body text-text-secondary">
              <li className="flex items-center gap-2">
                <Badge tone="ok">{STATUS_LABEL.fresh}</Badge>
                还在有效期内，可以直接当依据。
              </li>
              <li className="flex items-center gap-2">
                <Badge tone="warn">{STATUS_LABEL.stale}</Badge>
                已经超过有效期，先别当依据；页面会提示你重新生成。
              </li>
              <li className="flex items-center gap-2">
                <Badge tone="danger">{STATUS_LABEL.refuted}</Badge>
                平台后来发现了相反的事实，这份结论已经不成立。
              </li>
            </ul>
          </div>

          <p className="text-caption leading-body text-text-muted">
            没攒到的票，档案页会说「档案不存在」，你可以改用首页的「个股深挖」直接问，或在研究台里让它跑一轮；
            跑完之后这里会出现它的档案。
          </p>
        </CardBody>
      </Card>
    </div>
  )
}
