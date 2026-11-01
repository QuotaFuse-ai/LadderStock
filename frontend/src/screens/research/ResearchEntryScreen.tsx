import { useState, type FormEvent } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { Building2, Radio, Search } from 'lucide-react'

import { normalizeResearchCode } from '../../api/research'
import { PageHeader } from '../../components/PageShell'
import { LinkButton } from '../../components/research/bits'
import { Button, Card, CardBody, CardHeader, Field, Input } from '../../components/ui'

/**
 * 个股深挖落地页（`/research`，QUOT-300）。
 *
 * `/research/:code` 与它的报告页早已交付，但侧栏没有任何入口，用户只能手打 URL。
 * 本页补上「输入 6 位代码 → 进入深挖」这一步：不接受非法代码，不做假数据。
 * 演示与取证参数（`demo` / `variant` / `date` / `state`）原样带到研究页。
 */

/** 跨页保留的研究页参数；其余查询串（如页内取证参数）不外溢。 */
const CARRIED_PARAMS = ['demo', 'variant', 'date', 'state'] as const

export default function ResearchEntryScreen() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [raw, setRaw] = useState('')
  const [error, setError] = useState<string | null>(null)

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const code = normalizeResearchCode(raw)
    if (!code) {
      setError('代码不合法：请输入 6 位数字，可带 .SH / .SZ / .BJ 后缀。')
      return
    }
    const query = new URLSearchParams()
    for (const key of CARRIED_PARAMS) {
      const value = params.get(key)
      if (value !== null) query.set(key, value)
    }
    const search = query.toString()
    navigate(`/research/${encodeURIComponent(code)}${search ? `?${search}` : ''}`)
  }

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="个股深挖"
        subtitle="输入证券代码，进入六段研究产物与研究报告"
        badge={
          params.get('demo') === '1' ? (
            <span className="text-caption text-text-muted">演示源 · ?demo=1</span>
          ) : null
        }
      />

      <Card className="max-w-content">
        <CardHeader title="开始深挖" subtitle="代码只做格式校验；有没有产物由研究页判断，本页不猜测。" />
        <CardBody className="flex flex-col gap-4">
          <form onSubmit={submit} className="flex flex-col gap-3">
            <Field
              label="证券代码"
              htmlFor="research-code"
              required
              error={error}
              hint="6 位数字，例如 600000；可带 .SH / .SZ / .BJ 后缀。"
            >
              <Input
                id="research-code"
                name="code"
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
            <div className="flex items-center gap-2">
              <Button
                type="submit"
                variant="primary"
                leadingIcon={<Search className="size-3.5" aria-hidden="true" />}
              >
                进入深挖
              </Button>
            </div>
          </form>

          <p className="border-t border-border pt-3 text-caption leading-body text-text-muted">
            还没有研究产物时，先在本机运行 <code className="ldst-num">ldst research &lt;code&gt;</code> 生成六段产物；
            页面只读已落盘的产物，不会现场取数或补数据。
          </p>
        </CardBody>
      </Card>

      <div className="flex flex-wrap items-center gap-2">
        <LinkButton to="/industry" variant="secondary" leadingIcon={<Building2 className="size-3.5" aria-hidden="true" />}>
          产业研究矩阵
        </LinkButton>
        <LinkButton to="/signals" variant="secondary" leadingIcon={<Radio className="size-3.5" aria-hidden="true" />}>
          产业信号
        </LinkButton>
      </div>
    </div>
  )
}
