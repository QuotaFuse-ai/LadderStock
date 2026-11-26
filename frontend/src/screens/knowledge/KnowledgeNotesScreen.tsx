/**
 * 笔记与检索（`/knowledge/notes`，E 包 / DEC-091）。
 *
 * 「知识库」组第三页：平台攒的是档案，这里放的是**你自己记的东西**——
 *
 * - **笔记**：markdown 随手记（可带标的 / 标签），存本地库；列表 + 新建 / 编辑 /
 *   删除，正文在页面上直接渲染成可读的样式（`lib/markdown-lite.ts`）；
 * - **跨票检索**：一句话搜所有档案——向量层优先（语义），后端不可用时降级
 *   本地字面扫描；两种来源**分开标注**，不把降级伪装成语义命中；
 * - **估值换算**：PE / PB / PS / 股息率 / 盈利收益率 / 简化 DCF。输入全部
 *   由表单显式给出，输出恒带「这是换算器，不是估值判断」；换算结果不进
 *   研究管线（DEC-025 红线原样保留）。
 *
 * 空态 / 错误态都给下一步动作（QUOT 前端规约 §8），不静默空白；
 * 取不到的数字显示「—」（MISSING），不补 0。
 */

import { useMemo, useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import {
  BookOpen,
  Calculator,
  Loader2,
  NotebookPen,
  Pencil,
  Plus,
  Search,
  Trash2,
} from 'lucide-react'

import { describeApiError } from '../../api'
import {
  createNote,
  estimateValuation,
  fetchNotes,
  fetchTags,
  removeNote,
  searchKnowledge,
  updateNote,
  type EstimateInput,
  type EstimateResult,
  type KnowledgeNote,
  type NoteDraft,
  type SearchPayload,
  type TagCount,
} from '../../api/knowledgeNotes'
import { DemoStateBadge, OfflineBanner, PageHeader } from '../../components/PageShell'
import {
  Banner,
  Badge,
  Button,
  EmptyState,
  ErrorBlock,
  Field,
  Input,
  SectionCard,
  Segmented,
  Select,
  Textarea,
} from '../../components/ui'
import { renderMarkdownLite } from '../../lib/markdown-lite'
import { MISSING } from '../../lib/format'
import { useAsync } from '../../lib/useAsync'

type Tab = 'notes' | 'search' | 'calculator'

const TABS = [
  { value: 'notes', label: '笔记' },
  { value: 'search', label: '跨票检索' },
  { value: 'calculator', label: '估值换算' },
] as const

type CalcKind = 'pe' | 'pb' | 'ps' | 'dividend' | 'earnings-yield' | 'dcf'

const CALC_KINDS: ReadonlyArray<{ value: CalcKind; label: string; hint: string }> = [
  { value: 'pe', label: '市盈率 PE', hint: '价格 ÷ 每股收益' },
  { value: 'pb', label: '市净率 PB', hint: '价格 ÷ 每股净资产' },
  { value: 'ps', label: '市销率 PS', hint: '价格 ÷ 每股营收' },
  { value: 'dividend', label: '股息率', hint: '每股分红 ÷ 价格' },
  { value: 'earnings-yield', label: '盈利收益率', hint: '1 ÷ PE，与债券收益率同口径比较' },
  { value: 'dcf', label: '简化 DCF', hint: '两段折现：显式期 + 永续终值' },
]

/** 每种换算要哪些数字框：[字段名, 标签, 是否百分比输入] */
const CALC_FIELDS: Record<CalcKind, ReadonlyArray<readonly [string, string, boolean]>> = {
  pe: [['price', '价格（元）', false], ['eps', '每股收益（元）', false]],
  pb: [['price', '价格（元）', false], ['bps', '每股净资产（元）', false]],
  ps: [['price', '价格（元）', false], ['sps', '每股营收（元）', false]],
  dividend: [['price', '价格（元）', false], ['dps', '每股分红（元）', false]],
  'earnings-yield': [['pe', 'PE 倍数', false]],
  dcf: [
    ['fcf', '基期自由现金流（元）', false],
    ['growth', '增长段增速', true],
    ['terminal_growth', '终值增速', true],
    ['discount', '折现率', true],
    ['periods', '显式预测年数（1–20）', false],
  ],
}

function describeError(error: unknown): string {
  const info = describeApiError(error)
  return `${info.title}：${info.detail}`
}

function splitTags(raw: string): string[] {
  return raw
    .split(/[,，;；\s]+/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0)
}

/** 列表行副标题：正文首行（剥掉 # 记号），没有就给占位。 */
function firstLine(body: string): string {
  const line = body
    .split(/\r?\n/)
    .map((item) => item.replace(/^#+\s*/, '').trim())
    .find((item) => item.length > 0)
  return line ?? ''
}

function sourceBadge(source: string) {
  if (source === 'vector') return <Badge tone="info">语义</Badge>
  return <Badge tone="neutral">字面</Badge>
}

export default function KnowledgeNotesScreen() {
  const [tab, setTab] = useState<Tab>('notes')

  // ---- 笔记面
  const data = useAsync(
    async () => {
      const [notes, tags] = await Promise.all([fetchNotes({ limit: 200 }), fetchTags()])
      return { notes, tags }
    },
    [],
  )
  const notes = data.data?.notes ?? []
  const tags = data.data?.tags ?? []
  const [targetFilter, setTargetFilter] = useState('')
  const [tagFilter, setTagFilter] = useState('')
  const [selectedId, setSelectedId] = useState<number | null>(null)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [formTitle, setFormTitle] = useState('')
  const [formTarget, setFormTarget] = useState('')
  const [formTags, setFormTags] = useState('')
  const [formBody, setFormBody] = useState('')
  const [formError, setFormError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  // ---- 检索面
  const [query, setQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResult, setSearchResult] = useState<SearchPayload | null>(null)
  const [searchError, setSearchError] = useState<string | null>(null)

  // ---- 换算面
  const [calcKind, setCalcKind] = useState<CalcKind>('pe')
  const [calcInputs, setCalcInputs] = useState<Record<string, string>>({})
  const [computing, setComputing] = useState(false)
  const [calcResult, setCalcResult] = useState<EstimateResult | null>(null)
  const [calcError, setCalcError] = useState<string | null>(null)

  const filtered = useMemo(() => {
    const needle = targetFilter.trim().toUpperCase()
    return notes.filter((note) => {
      if (tagFilter && !note.tags.some((item) => item.toLowerCase() === tagFilter.toLowerCase())) {
        return false
      }
      if (needle && !`${note.market}:${note.symbol}`.toUpperCase().includes(needle)) {
        return false
      }
      return true
    })
  }, [notes, targetFilter, tagFilter])

  const reload = () => data.reload()

  const startEdit = (note: KnowledgeNote) => {
    setEditingId(note.id)
    setSelectedId(note.id)
    setFormTitle(note.title)
    setFormTarget(note.target)
    setFormTags(note.tags.join('、'))
    setFormBody(note.body)
    setFormError(null)
  }

  const resetForm = () => {
    setEditingId(null)
    setFormTitle('')
    setFormTarget('')
    setFormTags('')
    setFormBody('')
    setFormError(null)
  }

  const submitNote = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    setFormError(null)
    const body = formBody.trim()
    if (!body) {
      setFormError('笔记正文不能为空。')
      return
    }
    const draft: NoteDraft = {
      body,
      title: formTitle.trim(),
      target: formTarget.trim(),
      tags: splitTags(formTags),
    }
    setBusy(true)
    try {
      if (editingId !== null) {
        await updateNote(editingId, draft)
      } else {
        await createNote(draft)
      }
      resetForm()
      reload()
    } catch (error) {
      setFormError(describeError(error))
    } finally {
      setBusy(false)
    }
  }

  const onDelete = async (note: KnowledgeNote) => {
    setBusy(true)
    try {
      await removeNote(note.id)
      if (selectedId === note.id) setSelectedId(null)
      if (editingId === note.id) resetForm()
      reload()
    } catch (error) {
      setFormError(describeError(error))
    } finally {
      setBusy(false)
    }
  }

  const runSearch = async () => {
    const text = query.trim()
    setSearchError(null)
    if (!text) {
      setSearchError('先输入要搜的词，比如某只票的名字、一个观点或一个事件。')
      return
    }
    setSearching(true)
    try {
      setSearchResult(await searchKnowledge(text))
    } catch (error) {
      setSearchResult(null)
      setSearchError(describeError(error))
    } finally {
      setSearching(false)
    }
  }

  const onCalculate = async () => {
    setCalcError(null)
    setCalcResult(null)
    const fields = CALC_FIELDS[calcKind]
    const values: Record<string, number> = {}
    for (const [name, label, isPercent] of fields) {
      const raw = (calcInputs[name] ?? '').trim()
      if (!raw) {
        setCalcError(`请填写「${label.replace(/（.*）/, '')}」。`)
        return
      }
      const value = Number(raw)
      if (!Number.isFinite(value)) {
        setCalcError(`「${label.replace(/（.*）/, '')}」需要是数字。`)
        return
      }
      values[name] = isPercent ? value / 100 : value
    }
    const payload: EstimateInput = { kind: calcKind, ...values }
    setComputing(true)
    try {
      setCalcResult(await estimateValuation(payload))
    } catch (error) {
      setCalcError(describeError(error))
    } finally {
      setComputing(false)
    }
  }

  const calcHint = CALC_KINDS.find((item) => item.value === calcKind)?.hint ?? ''

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        title="笔记与检索"
        subtitle="自己记的看法 + 跨票搜档案 + 数字换算（换算器不是估值判断）"
        badge={<DemoStateBadge state={null} />}
      />

      {data.status === 'error' ? <OfflineBanner onRetry={reload} /> : null}

      <Segmented
        ariaLabel="知识库笔记视图"
        value={tab}
        onChange={(next) => setTab(next)}
        options={TABS.map(({ value, label }) => ({ value, label }))}
      />

      {tab === 'notes' ? (
        <div className="flex flex-col gap-4">
          <SectionCard
            title={`我的笔记（${filtered.length} 条${filtered.length !== notes.length ? ` / 共 ${notes.length} 条` : ''}）`}
            actions={
              <Button
                size="md"
                variant="ghost"
                leadingIcon={<Plus size={14} />}
                onClick={() => {
                  resetForm()
                  setTab('notes')
                }}
              >
                新建
              </Button>
            }
          >
            {data.status === 'error' ? (
              <ErrorBlock title="笔记读取失败" detail={describeError(data.error)} onRetry={reload} />
            ) : notes.length === 0 ? (
              <EmptyState
                icon={<NotebookPen size={20} />}
                title="还没有笔记"
                description="用下面的表单记第一条：比如「600000 等三季报再决定加仓」。笔记存本机，可带标的与标签，支持简单 markdown。"
              />
            ) : filtered.length === 0 ? (
              <EmptyState
                compact
                title="当前筛选没有命中的笔记"
                description="换个标的或标签再试；清掉筛选条件可以看到全部。"
              />
            ) : (
              <ul className="flex flex-col divide-y divide-border">
                {filtered.map((note) => (
                  <li key={note.id}>
                    <div className="flex items-start justify-between gap-3 py-3">
                      <button
                        type="button"
                        data-testid={`note-row-${note.id}`}
                        onClick={() => setSelectedId(selectedId === note.id ? null : note.id)}
                        className="min-w-0 flex-1 text-left transition-colors duration-fast hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
                      >
                        <span className="flex flex-wrap items-center gap-2">
                          {note.target ? <Badge tone="info">{note.target}</Badge> : null}
                          <span className="truncate text-body font-medium text-text">
                            {note.title || firstLine(note.body) || '（无标题）'}
                          </span>
                        </span>
                        <span className="mt-1 flex flex-wrap items-center gap-2 text-caption text-text-muted">
                          {note.tags.map((tag) => (
                            <span key={tag} className="rounded border border-border px-1">
                              {tag}
                            </span>
                          ))}
                          <span className="ldst-num">更新于 {note.updated_at || MISSING}</span>
                        </span>
                      </button>
                      <span className="flex shrink-0 items-center gap-1">
                        <Button
                          size="md"
                          variant="ghost"
                          leadingIcon={<Pencil size={14} />}
                          onClick={() => startEdit(note)}
                          disabled={busy}
                        >
                          编辑
                        </Button>
                        <Button
                          size="md"
                          variant="danger"
                          leadingIcon={<Trash2 size={14} />}
                          onClick={() => onDelete(note)}
                          disabled={busy}
                        >
                          删除
                        </Button>
                      </span>
                    </div>
                    {selectedId === note.id ? (
                      <div
                        data-testid={`note-body-${note.id}`}
                        className="mb-3 rounded border border-border bg-surface-2 p-3 text-body leading-body text-text [&_blockquote]:border-l-2 [&_blockquote]:border-border-strong [&_blockquote]:pl-2 [&_code]:rounded [&_code]:bg-surface-3 [&_code]:px-1 [&_h1]:mt-3 [&_h1]:text-section [&_h1]:font-semibold [&_h2]:mt-3 [&_h2]:text-subtitle [&_h2]:font-semibold [&_h3]:mt-2 [&_h3]:text-body [&_h3]:font-semibold [&_li]:ml-4 [&_ol]:list-decimal [&_p]:mt-1 [&_pre]:overflow-auto [&_pre]:rounded [&_pre]:bg-surface-3 [&_pre]:p-2 [&_ul]:list-disc"
                        dangerouslySetInnerHTML={{ __html: renderMarkdownLite(note.body) }}
                      />
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </SectionCard>

          <SectionCard title={editingId !== null ? '编辑这条笔记' : '新建笔记'}>
            <form onSubmit={submitNote} className="flex flex-col gap-3">
              <div className="flex flex-wrap items-end gap-3">
                <Field label="标题（可空）" htmlFor="note-title" className="w-64">
                  <Input
                    id="note-title"
                    value={formTitle}
                    placeholder="比如：浦发的观察要点"
                    onChange={(event) => setFormTitle(event.target.value)}
                  />
                </Field>
                <Field
                  label="标的（可空）"
                  htmlFor="note-target"
                  className="w-48"
                  hint="形态 SH:600000；纯代码也认"
                >
                  <Input
                    id="note-target"
                    value={formTarget}
                    placeholder="SH:600000"
                    autoComplete="off"
                    spellCheck={false}
                    onChange={(event) => setFormTarget(event.target.value)}
                  />
                </Field>
                <Field label="标签（可空）" htmlFor="note-tags" className="w-64" hint="顿号 / 逗号分隔">
                  <Input
                    id="note-tags"
                    value={formTags}
                    placeholder="银行、观察"
                    onChange={(event) => setFormTags(event.target.value)}
                  />
                </Field>
              </div>
              <Field label="正文（支持简单 markdown）" htmlFor="note-body" required>
                <Textarea
                  id="note-body"
                  rows={6}
                  value={formBody}
                  placeholder={'# 观点\n- 等三季报再决定加仓\n- 关注 `pb` 是否回到 0.6 以下'}
                  onChange={(event) => setFormBody(event.target.value)}
                />
              </Field>
              {formError ? <p className="text-caption text-danger">{formError}</p> : null}
              <div className="flex items-center gap-2">
                <Button type="submit" variant="primary" disabled={busy}>
                  {busy ? <Loader2 size={14} className="animate-spin" /> : null}
                  {editingId !== null ? '保存修改' : '保存笔记'}
                </Button>
                {editingId !== null ? (
                  <Button type="button" variant="ghost" onClick={resetForm} disabled={busy}>
                    取消编辑
                  </Button>
                ) : null}
              </div>
            </form>
          </SectionCard>

          {tags.length > 0 ? (
            <SectionCard title="按标签筛选">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setTagFilter('')}
                  className={`rounded border px-2 py-1 text-caption transition-colors duration-fast ${
                    tagFilter === ''
                      ? 'border-border-strong bg-surface-3 text-text'
                      : 'border-border text-text-secondary hover:bg-surface-2'
                  }`}
                >
                  全部
                </button>
                {tags.map((tag: TagCount) => (
                  <button
                    key={tag.tag}
                    type="button"
                    data-testid={`tag-chip-${tag.tag}`}
                    onClick={() => setTagFilter(tagFilter === tag.tag ? '' : tag.tag)}
                    className={`rounded border px-2 py-1 text-caption transition-colors duration-fast ${
                      tagFilter === tag.tag
                        ? 'border-border-strong bg-surface-3 text-text'
                        : 'border-border text-text-secondary hover:bg-surface-2'
                    }`}
                  >
                    {tag.tag} <span className="ldst-num">{tag.notes}</span>
                  </button>
                ))}
                <span className="ml-2">
                  <Field label="按标的过滤" htmlFor="note-filter" className="w-48">
                    <Input
                      id="note-filter"
                      value={targetFilter}
                      placeholder="600000"
                      autoComplete="off"
                      spellCheck={false}
                      onChange={(event) => setTargetFilter(event.target.value)}
                    />
                  </Field>
                </span>
              </div>
            </SectionCard>
          ) : null}
        </div>
      ) : null}

      {tab === 'search' ? (
        <SectionCard title="跨票检索" subtitle="一句话搜所有档案：向量语义优先，不通就降级字面，来源分开标注">
          <form
            onSubmit={(event) => {
              event.preventDefault()
              void runSearch()
            }}
            className="flex flex-col gap-3"
          >
            <div className="flex items-end gap-3">
              <Field label="搜索词" htmlFor="knowledge-search" className="flex-1">
                <Input
                  id="knowledge-search"
                  value={query}
                  placeholder="比如：银行 股息、算力 扩产、600000 三季报"
                  autoComplete="off"
                  leadingIcon={<Search className="size-4" aria-hidden="true" />}
                  onChange={(event) => setQuery(event.target.value)}
                />
              </Field>
              <Button type="submit" variant="primary" disabled={searching}>
                {searching ? <Loader2 size={14} className="animate-spin" /> : null}
                搜索
              </Button>
            </div>
            {searchError ? <p className="text-caption text-danger">{searchError}</p> : null}
          </form>

          {searchResult ? (
            <div className="mt-3 flex flex-col gap-3">
              <p className="text-caption text-text-muted">
                {searchResult.total > 0 ? (
                  <>
                    命中 <span className="ldst-num">{searchResult.total}</span> 条 · 本次来源：
                    {searchResult.engine === 'vector' ? '向量语义' : '本地字面（降级）'}
                    {searchResult.vector_error ? '（向量库不可用）' : null}
                  </>
                ) : (
                  '没有命中的档案。'
                )}
              </p>
              {searchResult.vector_error && searchResult.total > 0 ? (
                <Banner tone="warn" title="本次结果是字面降级，不是语义命中">
                  向量层不可用（{searchResult.vector_error}），以下来自本地字面扫描。要恢复语义搜索，
                  先把向量库拉起来。
                </Banner>
              ) : null}
              {searchResult.hits.length === 0 ? (
                <EmptyState
                  compact
                  title="没有命中的档案"
                  description="换个更具体的词，或先去「个股档案」让平台把这只票的档案攒出来。"
                />
              ) : (
                <ul className="flex flex-col divide-y divide-border">
                  {searchResult.hits.map((hit, index) => (
                    <li key={`${hit.market}:${hit.symbol}:${index}`} className="py-3">
                      <div className="flex flex-wrap items-center gap-2">
                        {sourceBadge(hit.source)}
                        {hit.market && hit.symbol ? (
                          <Link
                            to={`/knowledge/${hit.market}/${hit.symbol}`}
                            className="font-mono text-body font-medium text-text underline decoration-border underline-offset-2 hover:decoration-text"
                          >
                            {hit.market}:{hit.symbol}
                          </Link>
                        ) : (
                          <span className="text-body text-text-muted">{MISSING}</span>
                        )}
                        {hit.section ? <Badge tone="neutral">{hit.section}</Badge> : null}
                        <span className="ldst-num text-caption text-text-muted">
                          相关度 {hit.score === null ? MISSING : hit.score.toFixed(3)}
                        </span>
                      </div>
                      <p className="mt-1 line-clamp-3 text-caption leading-body text-text-secondary">
                        {hit.snippet || MISSING}
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          ) : null}
        </SectionCard>
      ) : null}

      {tab === 'calculator' ? (
        <div className="flex flex-col gap-4">
          <Banner tone="info" title="这是换算器，不是估值判断">
            数字全部来自你在下面填的输入，结果不构成投资建议，也不会进入研究档案——研究管线的
            估值段只认平台自己取到的数据（DEC-025 红线）。
          </Banner>
          <SectionCard title="估值换算" subtitle="选一种换算，填数字，点「计算」">
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-end gap-3">
                <Field label="换算类型" htmlFor="calc-kind" className="w-56" hint={calcHint}>
                  <Select
                    id="calc-kind"
                    value={calcKind}
                    onChange={(event) => {
                      setCalcKind(event.target.value as CalcKind)
                      setCalcInputs({})
                      setCalcResult(null)
                      setCalcError(null)
                    }}
                  >
                    {CALC_KINDS.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </Select>
                </Field>
                {CALC_FIELDS[calcKind].map(([name, label, isPercent]) => (
                  <Field key={name} label={label} htmlFor={`calc-${name}`} className="w-44">
                    <Input
                      id={`calc-${name}`}
                      inputMode="decimal"
                      value={calcInputs[name] ?? ''}
                      placeholder={isPercent ? '如 8 = 8%' : undefined}
                      autoComplete="off"
                      onChange={(event) =>
                        setCalcInputs((prev) => ({ ...prev, [name]: event.target.value }))
                      }
                    />
                  </Field>
                ))}
              </div>
              {calcError ? <p className="text-caption text-danger">{calcError}</p> : null}
              <div>
                <Button
                  variant="primary"
                  leadingIcon={<Calculator size={14} />}
                  onClick={() => void onCalculate()}
                  disabled={computing}
                >
                  {computing ? <Loader2 size={14} className="animate-spin" /> : null}
                  {computing ? '计算中…' : '计算'}
                </Button>
              </div>

              {calcResult ? (
                <div
                  data-testid="calc-result"
                  className="rounded border border-border bg-surface-2 p-3"
                >
                  <div className="flex flex-wrap items-baseline gap-2">
                    <span className="text-body font-medium text-text">{calcResult.kind}</span>
                    <span className="ldst-num text-section font-semibold text-text">
                      {calcResult.value}
                    </span>
                    {calcResult.value_pct ? (
                      <span className="ldst-num text-body text-text-secondary">
                        = {calcResult.value_pct}
                      </span>
                    ) : null}
                  </div>
                  {calcResult.pv_explicit !== undefined ? (
                    <p className="ldst-num mt-1 text-caption text-text-muted">
                      显式期现值 {calcResult.pv_explicit} · 终值现值 {calcResult.pv_terminal ?? MISSING}
                      {' · '}终值占比{' '}
                      {calcResult.terminal_share === null || calcResult.terminal_share === undefined
                        ? MISSING
                        : `${(calcResult.terminal_share * 100).toFixed(1)}%`}
                    </p>
                  ) : null}
                  {(calcResult.warnings ?? []).length > 0 ? (
                    <div className="mt-2">
                      <Banner tone="warn" title="注意">
                        <ul className="list-disc pl-4">
                          {calcResult.warnings!.map((warning) => (
                            <li key={warning}>{warning}</li>
                          ))}
                        </ul>
                      </Banner>
                    </div>
                  ) : null}
                  <p className="mt-2 text-caption text-text-muted">{calcResult.disclaimer}</p>
                </div>
              ) : null}
            </div>
          </SectionCard>
          <p className="text-caption leading-body text-text-muted">
            想对一只票做完整研究（含估值的官方口径），去
            <Link to="/research" className="mx-1 underline decoration-border underline-offset-2">
              个股深挖
            </Link>
            跑一轮；想在档案上继续补看法，回
            <Link to="/knowledge" className="mx-1 underline decoration-border underline-offset-2">
              <BookOpen size={12} className="mr-1 inline" aria-hidden="true" />
              个股档案
            </Link>
            。
          </p>
        </div>
      ) : null}
    </div>
  )
}
