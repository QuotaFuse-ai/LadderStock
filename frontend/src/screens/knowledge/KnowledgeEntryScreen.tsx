import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { BookOpen, Search } from 'lucide-react'

import {
  KNOWLEDGE_MARKETS,
  MARKET_LABEL,
  inferKnowledgeMarket,
  normalizeKnowledgeMarket,
  normalizeKnowledgeSymbol,
  type KnowledgeMarket,
} from '../../api/knowledge'
import { DemoStateBadge, PageHeader } from '../../components/PageShell'
import { Button, Card, CardBody, CardHeader, Field, Input, Select } from '../../components/ui'

/**
 * 知识库落地页（`/knowledge`，QUOT-567）。
 *
 * 只做一件事：把「我们了解这只票什么」取回来——输入 6 位代码，市场按前缀
 * 先替用户选好（可手改），提交跳 `/knowledge/:market/:symbol`。
 *
 * 文案面向普通用户：不出现 manifest / frontmatter / schema 这类词，
 * 说的是「平台自动攒的、多久更新一次、过期了会怎样」。
 */

const INFER_HINT: Record<KnowledgeMarket, string> = {
  SH: '已按代码推断为沪市',
  SZ: '已按代码推断为深市',
  BJ: '已按代码推断为北交所',
}

export default function KnowledgeEntryScreen() {
  const navigate = useNavigate()
  const [raw, setRaw] = useState('')
  const [market, setMarket] = useState<KnowledgeMarket | 'auto'>('auto')
  const [error, setError] = useState<string | null>(null)

  const code = normalizeKnowledgeSymbol(raw)
  const inferred = inferKnowledgeMarket(code)
  // 用户没手选时跟着代码走；选过就一直是用户的选择，换代码也不改
  const effective = market === 'auto' ? inferred : normalizeKnowledgeMarket(market)

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
        subtitle="看看平台已经了解这只有多少，以及是什么时候了解到的"
        badge={<DemoStateBadge state={null} />}
      />

      <Card className="max-w-content">
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
    </div>
  )
}
