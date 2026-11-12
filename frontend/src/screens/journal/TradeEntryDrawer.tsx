import { useEffect, useState, type FormEvent } from 'react'

import { api, describeApiError, isEndpointMissing } from '../../api'
import { Banner, Button, Drawer, Field, Input, Segmented, Textarea } from '../../components/ui'

/**
 * 「记一笔」（QUOT-561 / DEC-083）：交易日志的本机写入口。
 *
 * 只写本机 SQLite（与本地记录共用同一个写入函数），不写任何指标——写完的回显
 * 是服务端返回的那一行，页面不自己算、不补默认值。失败一律按真实错误展示
 * （400 的字段原因原样给出），不回落演示、不假装成功。
 */

export const NOTE_LIMIT = 200

type Side = 'buy' | 'sell'

const SIDE_OPTIONS = [
  { value: 'buy' as const, label: '买入' },
  { value: 'sell' as const, label: '卖出' },
]

interface FormState {
  code: string
  side: Side
  price: string
  qty: string
  note: string
  planPrice: string
  tradeDate: string
}

function today(): string {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

function emptyForm(): FormState {
  return { code: '', side: 'buy', price: '', qty: '', note: '', planPrice: '', tradeDate: today() }
}

/** 只做格式检查（空 / 非数字 / 非正数）；业务口径由服务端同一份校验给出。 */
function parsePositive(raw: string): number | null {
  const text = raw.trim()
  if (!text) return null
  const value = Number(text)
  if (!Number.isFinite(value) || value <= 0) return null
  return value
}

function validate(form: FormState): Record<string, string> {
  const errors: Record<string, string> = {}
  if (!form.code.trim()) errors.code = '填上证券代码'
  if (parsePositive(form.price) === null) errors.price = '成交价要是大于 0 的数字'
  if (parsePositive(form.qty) === null) errors.qty = '数量要是大于 0 的数字'
  if (form.planPrice.trim() && parsePositive(form.planPrice) === null) {
    errors.planPrice = '计划价要是大于 0 的数字；不想记就留空'
  }
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.tradeDate)) errors.tradeDate = '日期按「年-月-日」填写'
  if (form.note.trim().length > NOTE_LIMIT) errors.note = `备注最多 ${NOTE_LIMIT} 个字`
  return errors
}

export interface TradeEntryDrawerProps {
  open: boolean
  onClose: () => void
  /** 写成功后由页面刷新统计（本组件不自己算演示数字）。 */
  onSaved: () => void
  onNotify: (message: string, tone: 'ok' | 'danger') => void
}

export function TradeEntryDrawer({ open, onClose, onSaved, onNotify }: TradeEntryDrawerProps) {
  const [form, setForm] = useState<FormState>(emptyForm)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [busy, setBusy] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [saved, setSaved] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setForm(emptyForm())
    setErrors({})
    setFailure(null)
    setSaved(null)
    setBusy(false)
  }, [open])

  const patch = (key: keyof FormState) => (value: string) => {
    setForm((current) => ({ ...current, [key]: value }))
    setErrors((current) => {
      if (!(key in current)) return current
      const next = { ...current }
      delete next[key]
      return next
    })
  }

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    const found = validate(form)
    setErrors(found)
    if (Object.keys(found).length > 0) return
    setBusy(true)
    setFailure(null)
    try {
      const written = await api.journalAddTrade({
        code: form.code.trim(),
        side: form.side,
        price: parsePositive(form.price) as number,
        qty: parsePositive(form.qty) as number,
        trade_date: form.tradeDate,
        ...(form.planPrice.trim() ? { plan_price: parsePositive(form.planPrice) as number } : {}),
        ...(form.note.trim() ? { note: form.note.trim() } : {}),
      })
      const sideLabel = written.side === 'sell' ? '卖出' : '买入'
      setSaved(
        `${written.trade_date} ${written.code} ${sideLabel} ${written.qty} 股 @ ${written.price}，已记到本机`,
      )
      setForm(emptyForm())
      onSaved()
      onNotify('已记一笔，统计会在刷新后更新', 'ok')
    } catch (error) {
      if (isEndpointMissing(error)) {
        setFailure('本机服务还是旧版本，没有「记一笔」写入口：更新到当前版本后再试。已经填的内容不会丢。')
      } else {
        setFailure(describeApiError(error).detail)
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title="记一笔"
      subtitle="只写本机日志 · 不上传 · 不进 AI 上下文"
      footer={
        <>
          <Button variant="ghost" onClick={onClose} disabled={busy}>
            关闭
          </Button>
          <Button variant="primary" type="submit" form="ldst-trade-form" loading={busy}>
            记到本机
          </Button>
        </>
      }
    >
      <form id="ldst-trade-form" className="flex flex-col gap-3" onSubmit={(event) => void submit(event)}>
        {saved && (
          <Banner tone="ok" title="已记到本机">
            {saved}
          </Banner>
        )}
        {failure && (
          <Banner tone="danger" title="没有记上">
            {failure}
          </Banner>
        )}

        <Field label="方向" required>
          <Segmented
            options={SIDE_OPTIONS}
            value={form.side}
            onChange={(next) => patch('side')(next)}
            ariaLabel="买入还是卖出"
          />
        </Field>

        <div className="grid grid-cols-1 gap-3 rail:grid-cols-2">
          <Field label="证券代码" htmlFor="ldst-trade-code" required error={errors.code}>
            <Input
              id="ldst-trade-code"
              value={form.code}
              onChange={(event) => patch('code')(event.target.value)}
              placeholder="如 600000"
              autoComplete="off"
              inputMode="numeric"
              invalid={Boolean(errors.code)}
            />
          </Field>
          <Field label="成交日期" htmlFor="ldst-trade-date" required error={errors.tradeDate}>
            <Input
              id="ldst-trade-date"
              type="date"
              value={form.tradeDate}
              onChange={(event) => patch('tradeDate')(event.target.value)}
              invalid={Boolean(errors.tradeDate)}
            />
          </Field>
        </div>

        <div className="grid grid-cols-1 gap-3 rail:grid-cols-2">
          <Field label="成交价" htmlFor="ldst-trade-price" required error={errors.price}>
            <Input
              id="ldst-trade-price"
              value={form.price}
              onChange={(event) => patch('price')(event.target.value)}
              placeholder="如 10.32"
              inputMode="decimal"
              autoComplete="off"
              invalid={Boolean(errors.price)}
            />
          </Field>
          <Field label="数量（股）" htmlFor="ldst-trade-qty" required error={errors.qty}>
            <Input
              id="ldst-trade-qty"
              value={form.qty}
              onChange={(event) => patch('qty')(event.target.value)}
              placeholder="如 1000"
              inputMode="decimal"
              autoComplete="off"
              invalid={Boolean(errors.qty)}
            />
          </Field>
        </div>

        <Field
          label="计划价（可不填）"
          htmlFor="ldst-trade-plan"
          error={errors.planPrice}
          hint="填了才能统计「偏离计划」的成交"
        >
          <Input
            id="ldst-trade-plan"
            value={form.planPrice}
            onChange={(event) => patch('planPrice')(event.target.value)}
            placeholder="下单前想成交的价格"
            inputMode="decimal"
            autoComplete="off"
            invalid={Boolean(errors.planPrice)}
          />
        </Field>

        <Field
          label={`备注（可不填，最多 ${NOTE_LIMIT} 字）`}
          htmlFor="ldst-trade-note"
          error={errors.note}
          hint={`${form.note.trim().length} / ${NOTE_LIMIT}`}
        >
          <Textarea
            id="ldst-trade-note"
            value={form.note}
            onChange={(event) => patch('note')(event.target.value)}
            placeholder="一句话记下当时的理由"
            maxLength={NOTE_LIMIT}
            rows={3}
          />
        </Field>

        <p className="text-caption text-text-muted">
          只接受上面这几项：券商账号、资金密码这类信息没有入口，也不会落库。
        </p>
      </form>
    </Drawer>
  )
}
