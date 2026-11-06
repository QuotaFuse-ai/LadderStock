import { useState } from 'react'
import { Plus } from 'lucide-react'

import { isApiError } from '../../api'
import { addPortfolioTrade } from '../../api/radar'
import { Banner, Button, Field, Input, Segmented, Textarea } from '../../components/ui'
import {
  FIELD_LABEL,
  FIELDS_OF,
  TRADE_TYPES,
  TRADE_TYPE_LABELS,
  TYPE_HINT,
  emptyTradeForm,
  tradeRecord,
  type NumericField,
  type TradeForm,
} from './portfolioForm'
import { PrivacyInput } from './PrivacyText'

/**
 * 记一笔（QUOT-434）：类型切换只显示该类型要填的字段，校验与后端同口径。
 *
 * 成功后才清表单并重取视图；失败把后端原因原样显示，已填内容不丢。
 */

export interface PortfolioEntryProps {
  /** 写库成功后调用：由页面重取视图（静默刷新，不闪空表）。 */
  onSaved: (message: string) => void
}

export function PortfolioEntry({ onSaved }: PortfolioEntryProps) {
  const [form, setForm] = useState<TradeForm>(() => emptyTradeForm())
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const set = (patch: Partial<TradeForm>) => setForm((current) => ({ ...current, ...patch }))

  const submit = async () => {
    const parsed = tradeRecord(form)
    if ('error' in parsed) {
      setError(parsed.error)
      return
    }
    setSaving(true)
    setError(null)
    try {
      await addPortfolioTrade(parsed.record)
      setForm(emptyTradeForm(form.type))
      onSaved(`已记一笔${TRADE_TYPE_LABELS[form.type]}（本机 SQLite）`)
    } catch (cause) {
      setError(isApiError(cause) ? (cause.detail ?? cause.message) : String(cause))
    } finally {
      setSaving(false)
    }
  }

  const fields = FIELDS_OF[form.type]

  return (
    <div className="flex flex-col gap-3" data-testid="portfolio-entry">
      <Segmented
        ariaLabel="流水类型"
        options={TRADE_TYPES.map((type) => ({ value: type, label: TRADE_TYPE_LABELS[type] }))}
        value={form.type}
        onChange={(type) => {
          setError(null)
          set({ type })
        }}
      />

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Field label="代码" htmlFor="portfolio-symbol" required hint="6 位 A 股代码，可带 sh / sz 前缀">
          <Input
            id="portfolio-symbol"
            value={form.symbol}
            placeholder="600519"
            autoComplete="off"
            maxLength={12}
            onChange={(event) => set({ symbol: event.target.value })}
          />
        </Field>
        <Field label="日期" htmlFor="portfolio-date" required hint="写建仓 / 成交当天，YYYY-MM-DD">
          <Input
            id="portfolio-date"
            value={form.date}
            placeholder="2026-11-05"
            inputMode="numeric"
            maxLength={10}
            autoComplete="off"
            onChange={(event) => set({ date: event.target.value })}
          />
        </Field>
        {fields.map((key) => (
          <NumericFieldInput
            key={key}
            type={form.type}
            field={key}
            value={form[key]}
            onChange={(value) => set({ [key]: value } as Partial<TradeForm>)}
          />
        ))}
        <Field label="名称（选填）" htmlFor="portfolio-name" hint="便于查找，不参与计算">
          <Input
            id="portfolio-name"
            value={form.name}
            maxLength={40}
            autoComplete="off"
            placeholder="贵州茅台"
            onChange={(event) => set({ name: event.target.value })}
          />
        </Field>
      </div>

      <Field label="备注（选填）" htmlFor="portfolio-note" hint="当时的判断、要留意什么；纯文本，只存本机">
        <Textarea
          id="portfolio-note"
          value={form.note}
          rows={2}
          maxLength={1000}
          placeholder="如：回踩 20 日线加仓"
          onChange={(event) => set({ note: event.target.value })}
        />
      </Field>

      {error && <Banner tone="danger" title={error} />}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          variant="primary"
          loading={saving}
          leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
          onClick={() => void submit()}
        >
          记一笔
        </Button>
        <span className="text-caption leading-body text-text-muted">{TYPE_HINT[form.type]}</span>
      </div>
    </div>
  )
}

interface NumericFieldInputProps {
  type: TradeForm['type']
  field: NumericField
  value: string
  onChange: (value: string) => void
}

function NumericFieldInput({ type, field, value, onChange }: NumericFieldInputProps) {
  const label = FIELD_LABEL[type][field]
  if (!label) return null
  const [text, placeholder] = label
  const id = `portfolio-${field}`
  return (
    <Field label={text} htmlFor={id} required={field !== 'fee'}>
      <PrivacyInput
        id={id}
        value={value}
        inputMode="decimal"
        autoComplete="off"
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
    </Field>
  )
}
