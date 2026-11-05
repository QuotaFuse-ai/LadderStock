import { useEffect, useId, useMemo, useState, type FormEvent } from 'react'
import { CalendarClock, History, NotebookPen, Pencil, Plus, Target, Trash2 } from 'lucide-react'

import { cn } from '../../../lib/cn'
import { localDateIso } from '../../../lib/format'
import {
  VERDICT_LABEL,
  daysPastDue,
  overdueCount,
  overduePoints,
  useThesisNotes,
  type DecisionPoint,
  type ThesisRecord,
  type Verdict,
} from '../../../../stores'
import {
  Badge,
  Button,
  ConfirmDialog,
  EmptyState,
  Field,
  Input,
  SectionCard,
  Select,
  Textarea,
} from '../../ui'

/**
 * 区块 H：我的论点与判断（QUOT-452）。
 *
 * props 契约（QUOT-448 定死）：`code` 规范代码、`name` 展示名、`className` 页面栅格。
 *
 * 三件事必须成立，否则这一块没有意义：
 * - **每条论点至少一个裁决点**：到期日 + 判定条件缺一不可，保存时校验并提示；
 * - **到期未判要看得见**：面板顶部给「已到期未判 N」，条目高亮并标逾期天数；
 * - **判定留痕**：结论与判定时间只追加，`history` 回看得到改判过程。
 *
 * 存储：`thesis_notes` 分片（IndexedDB，本机私有）。本组件不发请求、不把正文写进日志
 * 或 AI 上下文，也不提供分享 / 导出入口。
 */

export interface ThesisSectionProps {
  code: string | null
  name: string | null
  className?: string
}

type FormMode = null | 'new' | string

interface ThesisFormDraft {
  title: string
  body: string
  reason: string
  condition: string
  due: string
}

interface FieldErrors {
  title?: string
  condition?: string
  due?: string
}

const EMPTY_DRAFT: ThesisFormDraft = { title: '', body: '', reason: '', condition: '', due: '' }

const VERDICT_TONE: Record<Verdict, 'ok' | 'danger' | 'neutral'> = {
  pending: 'neutral',
  met: 'ok',
  broken: 'danger',
}

function pad(value: number): string {
  return value < 10 ? `0${value}` : String(value)
}

/** 判定时间按本地时区展示到分钟：留痕是给人回看的，不是给机器排序的。 */
function formatJudgedAt(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function verdictSummary(points: readonly DecisionPoint[]): string {
  const judged = points.filter((point) => point.verdict !== 'pending').length
  return `${judged}/${points.length} 已判`
}

export function ThesisSection({ code, name, className }: ThesisSectionProps) {
  const records = useThesisNotes((state) => state.records)
  const addThesis = useThesisNotes((state) => state.addThesis)
  const updateThesis = useThesisNotes((state) => state.updateThesis)
  const removeThesis = useThesisNotes((state) => state.removeThesis)
  const addPoint = useThesisNotes((state) => state.addPoint)
  const judgePoint = useThesisNotes((state) => state.judgePoint)

  const baseId = useId()
  const [formMode, setFormMode] = useState<FormMode>(null)
  const [draft, setDraft] = useState<ThesisFormDraft>(EMPTY_DRAFT)
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({})
  const [formError, setFormError] = useState('')
  const [pointFor, setPointFor] = useState<string | null>(null)
  const [pointDraft, setPointDraft] = useState({ condition: '', due: '' })
  const [pointError, setPointError] = useState('')
  const [judgeFor, setJudgeFor] = useState<{ thesisId: string; pointId: string } | null>(null)
  const [judgeDraft, setJudgeDraft] = useState<{ verdict: '' | 'met' | 'broken'; note: string }>({
    verdict: '',
    note: '',
  })
  const [judgeError, setJudgeError] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<ThesisRecord | null>(null)
  const [savedNote, setSavedNote] = useState('')

  // 换标的时收起所有编辑态：草稿是「这一只」的，不能跟着标题漂到下一只
  useEffect(() => {
    setFormMode(null)
    setDraft(EMPTY_DRAFT)
    setFieldErrors({})
    setFormError('')
    setPointFor(null)
    setPointDraft({ condition: '', due: '' })
    setPointError('')
    setJudgeFor(null)
    setJudgeDraft({ verdict: '', note: '' })
    setJudgeError('')
    setDeleteTarget(null)
    setSavedNote('')
  }, [code])

  const mine = useMemo(
    () => (code ? records.filter((record) => record.code === code) : []),
    [records, code],
  )
  const today = localDateIso()
  const overdue = useMemo(() => overdueCount(mine, today), [mine, today])
  const label = name ?? code ?? '该标的'

  const titleId = `${baseId}-title`
  const bodyId = `${baseId}-body`
  const reasonId = `${baseId}-reason`
  const conditionId = `${baseId}-condition`
  const dueId = `${baseId}-due`
  const symbolId = `${baseId}-symbol`

  const resetForm = () => {
    setFormMode(null)
    setDraft(EMPTY_DRAFT)
    setFieldErrors({})
    setFormError('')
  }

  const startNew = () => {
    setFormMode('new')
    setDraft(EMPTY_DRAFT)
    setFieldErrors({})
    setFormError('')
    setSavedNote('')
    setPointFor(null)
    setJudgeFor(null)
  }

  const startEdit = (record: ThesisRecord) => {
    setFormMode(record.id)
    setDraft({
      title: record.title,
      body: record.body,
      reason: record.reason,
      condition: '',
      due: '',
    })
    setFieldErrors({})
    setFormError('')
    setSavedNote('')
    setPointFor(null)
    setJudgeFor(null)
  }

  const submitForm = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!code) return
    const errors: FieldErrors = {}
    if (!draft.title.trim()) errors.title = '论点需要一个标题'
    if (formMode === 'new') {
      if (!draft.condition.trim()) {
        errors.condition = '裁决点必须写清判定条件：什么算成立、什么算不成立'
      }
      if (!draft.due.trim()) {
        errors.due = '裁决点必须填到期日'
      }
    }
    setFieldErrors(errors)
    if (Object.keys(errors).length > 0) {
      setFormError('还有必填项没写完，先补齐再保存。')
      return
    }

    const result =
      formMode === 'new'
        ? addThesis({
            code,
            title: draft.title,
            body: draft.body,
            reason: draft.reason,
            condition: draft.condition,
            due: draft.due,
          })
        : formMode
          ? updateThesis(formMode, {
              title: draft.title,
              body: draft.body,
              reason: draft.reason,
            })
          : { ok: false as const, error: '没有正在编辑的论点' }

    if (!result.ok) {
      setFormError(result.error)
      return
    }
    resetForm()
    setSavedNote(formMode === 'new' ? '论点与裁决点已保存到本机' : '论点已更新')
  }

  const submitPoint = (thesisId: string) => {
    const result = addPoint(thesisId, pointDraft)
    if (!result.ok) {
      setPointError(result.error)
      return
    }
    setPointFor(null)
    setPointDraft({ condition: '', due: '' })
    setPointError('')
    setSavedNote('裁决点已添加')
  }

  const submitJudge = () => {
    if (!judgeFor) return
    if (!judgeDraft.verdict) {
      setJudgeError('先选判定结论：成立或不成立')
      return
    }
    const result = judgePoint(judgeFor.thesisId, judgeFor.pointId, {
      verdict: judgeDraft.verdict,
      note: judgeDraft.note,
    })
    if (!result.ok) {
      setJudgeError(result.error)
      return
    }
    setJudgeFor(null)
    setJudgeDraft({ verdict: '', note: '' })
    setJudgeError('')
    setSavedNote('判定已记录，留痕可回看')
  }

  const sections = (
    <>
      {!code ? (
        <EmptyState
          compact
          icon={<Target aria-hidden="true" />}
          title="先选择一只标的"
          description="论点按标的保存在本机；从上方搜索选中标的后，这里会显示它自己的论点与裁决点。"
        />
      ) : (
        <>
          {savedNote && (
            <p role="status" className="mb-3 text-caption text-ok">
              {savedNote}
            </p>
          )}

          {formMode && (
            <form
              onSubmit={submitForm}
              aria-label={formMode === 'new' ? '新建论点' : '编辑论点'}
              className="mb-4 flex flex-col gap-3 rounded border border-border-strong bg-surface-2 p-4"
            >
              <div className="grid gap-3 desk:grid-cols-2">
                <Field label="标的" htmlFor={symbolId} hint="论点按当前页面标的保存">
                  <Input
                    id={symbolId}
                    value={name ? `${name}（${code}）` : code}
                    readOnly
                    aria-readonly="true"
                  />
                </Field>
                <Field
                  label="标题"
                  htmlFor={titleId}
                  required
                  error={fieldErrors.title}
                  hint={fieldErrors.title ? undefined : '一句话说清你的判断'}
                >
                  <Input
                    id={titleId}
                    value={draft.title}
                    invalid={Boolean(fieldErrors.title)}
                    onChange={(event) => setDraft({ ...draft, title: event.target.value })}
                    placeholder="比如「产能护城河，绕不开」"
                    autoFocus
                  />
                </Field>
              </div>

              <Field label="正文" htmlFor={bodyId} hint="把支撑判断的事实写下来，以后回看才认得出当时的自己">
                <Textarea
                  id={bodyId}
                  rows={3}
                  value={draft.body}
                  onChange={(event) => setDraft({ ...draft, body: event.target.value })}
                  placeholder="需求、竞争格局、估值……"
                />
              </Field>

              <Field
                label="建仓或观察理由"
                htmlFor={reasonId}
                hint="为什么现在建仓 / 为什么先观察"
              >
                <Textarea
                  id={reasonId}
                  rows={2}
                  value={draft.reason}
                  onChange={(event) => setDraft({ ...draft, reason: event.target.value })}
                  placeholder="比如「只观察，等下一份财报确认毛利率」"
                />
              </Field>

              {formMode === 'new' && (
                <fieldset className="flex flex-col gap-3 rounded border border-border p-3">
                  <legend className="px-1 text-caption text-text-secondary">
                    裁决点（到期必判，至少一个）
                  </legend>
                  <Field
                    label="判定条件"
                    htmlFor={conditionId}
                    required
                    error={fieldErrors.condition}
                    hint={
                      fieldErrors.condition
                        ? undefined
                        : '写清什么算成立、什么算不成立；到期必须二选一'
                    }
                  >
                    <Textarea
                      id={conditionId}
                      rows={2}
                      value={draft.condition}
                      onChange={(event) => setDraft({ ...draft, condition: event.target.value })}
                      placeholder="比如「下一季毛利率 ≥ 30% 算成立，否则不成立」"
                    />
                  </Field>
                  <Field
                    label="到期日"
                    htmlFor={dueId}
                    required
                    error={fieldErrors.due}
                    hint={fieldErrors.due ? undefined : '可手填 YYYY-MM-DD，也可用日历选择'}
                  >
                    <Input
                      id={dueId}
                      type="date"
                      value={draft.due}
                      invalid={Boolean(fieldErrors.due)}
                      onChange={(event) => setDraft({ ...draft, due: event.target.value })}
                    />
                  </Field>
                </fieldset>
              )}

              {formError && (
                <p role="alert" className="text-caption text-danger">
                  {formError}
                </p>
              )}

              <div className="flex flex-wrap items-center gap-2">
                <Button type="submit" variant="secondary">
                  {formMode === 'new' ? '保存论点' : '保存修改'}
                </Button>
                <Button variant="ghost" onClick={resetForm}>
                  取消
                </Button>
              </div>
            </form>
          )}

          {mine.length === 0 ? (
            <EmptyState
              icon={<NotebookPen aria-hidden="true" />}
              title={`还没为${label}写下论点`}
              description={
                <>
                  写一条论点，再配一个到期必判的裁决点 —— 以后每次打开就知道该回来核什么。
                  <br />
                  裁决点必须写明判定条件与到期日：到点只能判「成立」或「不成立」，判断过程才留得下来。
                </>
              }
              action={
                <Button variant="secondary" leadingIcon={<Plus className="size-4" aria-hidden="true" />} onClick={startNew}>
                  写第一条论点
                </Button>
              }
            />
          ) : (
            <ul className="flex flex-col gap-3">
              {mine.map((record) => {
                const recordOverdue = overduePoints(record.points, today)
                return (
                  <li
                    key={record.id}
                    aria-labelledby={`thesis-${record.id}`}
                    className="rounded border border-border bg-surface-2 p-4"
                  >
                    <div className="flex flex-wrap items-baseline gap-2">
                      <h3
                        id={`thesis-${record.id}`}
                        className="min-w-0 text-body font-semibold text-text"
                      >
                        {record.title}
                      </h3>
                      <span className="ldst-num text-caption text-text-muted">
                        {record.code} · 记于 {formatJudgedAt(record.createdAt)}
                      </span>
                      <span className="text-caption text-text-muted">
                        {verdictSummary(record.points)}
                      </span>
                      {recordOverdue.length > 0 && (
                        <Badge tone="danger">已到期未判 {recordOverdue.length}</Badge>
                      )}
                      <span className="ml-auto flex shrink-0 items-center gap-1">
                        <Button
                          variant="ghost"
                          onClick={() => startEdit(record)}
                          leadingIcon={<Pencil className="size-3.5" aria-hidden="true" />}
                        >
                          编辑
                        </Button>
                        <Button
                          variant="ghost"
                          className="text-danger"
                          onClick={() => setDeleteTarget(record)}
                          leadingIcon={<Trash2 className="size-3.5" aria-hidden="true" />}
                        >
                          删除
                        </Button>
                      </span>
                    </div>

                    {record.body && (
                      <p className="mt-2 whitespace-pre-wrap text-body leading-body text-text-secondary">
                        {record.body}
                      </p>
                    )}
                    {record.reason && (
                      <p className="mt-2 text-caption leading-body text-text-secondary">
                        <span className="text-text-muted">建仓 / 观察理由：</span>
                        {record.reason}
                      </p>
                    )}

                    <div className="mt-3 rounded border border-border">
                      <p className="border-b border-border px-3 py-1 text-caption text-text-secondary">
                        裁决点（到期必判）
                      </p>
                      <ul>
                        {record.points.map((point) => {
                          const isOverdue = point.verdict === 'pending' && point.due < today
                          const isJudging =
                            judgeFor?.thesisId === record.id && judgeFor?.pointId === point.id
                          return (
                            <li
                              key={point.id}
                              className={cn(
                                'border-t border-border px-3 py-2 first:border-t-0',
                                isOverdue && 'border-l-2 border-l-warn bg-warn-soft',
                              )}
                            >
                              <div className="flex flex-wrap items-center gap-2">
                                <CalendarClock
                                  className={cn(
                                    'size-3.5 shrink-0',
                                    isOverdue ? 'text-warn' : 'text-text-muted',
                                  )}
                                  aria-hidden="true"
                                />
                                <span className="min-w-0 flex-1 text-body text-text">
                                  {point.condition}
                                </span>
                                <span
                                  className={cn(
                                    'ldst-num text-caption',
                                    isOverdue ? 'text-warn' : 'text-text-secondary',
                                  )}
                                >
                                  到期 {point.due}
                                </span>
                                {isOverdue && (
                                  <Badge tone="danger">
                                    已到期未判 · 逾期 {daysPastDue(point.due, today)} 天
                                  </Badge>
                                )}
                                <Badge tone={VERDICT_TONE[point.verdict]}>
                                  {VERDICT_LABEL[point.verdict]}
                                </Badge>
                                {!isJudging && (
                                  <Button
                                    variant="ghost"
                                    onClick={() => {
                                      setJudgeFor({ thesisId: record.id, pointId: point.id })
                                      setJudgeDraft({ verdict: '', note: '' })
                                      setJudgeError('')
                                    }}
                                  >
                                    判定
                                  </Button>
                                )}
                              </div>

                              {point.verdict !== 'pending' && point.judgedAt && (
                                <p className="mt-1 text-caption text-text-muted">
                                  最近判定于 {formatJudgedAt(point.judgedAt)}
                                  {point.verdictNote ? ` · ${point.verdictNote}` : ''}
                                </p>
                              )}

                              {point.history.length > 0 && (
                                <details className="mt-1">
                                  <summary className="cursor-pointer text-caption text-text-secondary">
                                    <History
                                      className="mr-1 inline size-3.5 align-text-bottom"
                                      aria-hidden="true"
                                    />
                                    判定留痕（{point.history.length} 次）
                                  </summary>
                                  <ul className="mt-1 flex flex-col gap-1 border-l border-border-strong pl-3">
                                    {point.history.map((entry) => (
                                      <li key={entry.id} className="text-caption text-text-secondary">
                                        <span className="ldst-num">
                                          {formatJudgedAt(entry.judgedAt)}
                                        </span>
                                        {' · '}
                                        {VERDICT_LABEL[entry.verdict]}
                                        {entry.note ? ` · ${entry.note}` : ''}
                                      </li>
                                    ))}
                                  </ul>
                                </details>
                              )}

                              {isJudging && (
                                <div className="mt-2 flex flex-col gap-2 rounded border border-border-strong bg-surface p-3">
                                  <div className="grid gap-2 desk:grid-cols-2">
                                    <Field
                                      label="判定结论"
                                      htmlFor={`${baseId}-verdict-${point.id}`}
                                      required
                                      hint="到点只能判成立或不成立"
                                    >
                                      <Select
                                        id={`${baseId}-verdict-${point.id}`}
                                        value={judgeDraft.verdict}
                                        invalid={Boolean(judgeError)}
                                        onChange={(event) =>
                                          setJudgeDraft({
                                            ...judgeDraft,
                                            verdict: event.target.value as '' | 'met' | 'broken',
                                          })
                                        }
                                      >
                                        <option value="">请选择</option>
                                        <option value="met">成立</option>
                                        <option value="broken">不成立</option>
                                      </Select>
                                    </Field>
                                    <Field
                                      label="判定说明（可留空）"
                                      htmlFor={`${baseId}-judge-note-${point.id}`}
                                      hint="写清依据，改判时也看得懂"
                                    >
                                      <Input
                                        id={`${baseId}-judge-note-${point.id}`}
                                        value={judgeDraft.note}
                                        onChange={(event) =>
                                          setJudgeDraft({ ...judgeDraft, note: event.target.value })
                                        }
                                        placeholder="比如「毛利率 31.2%，达标」"
                                      />
                                    </Field>
                                  </div>
                                  {judgeError && (
                                    <p role="alert" className="text-caption text-danger">
                                      {judgeError}
                                    </p>
                                  )}
                                  <div className="flex flex-wrap items-center gap-2">
                                    <Button variant="secondary" onClick={submitJudge}>
                                      记录判定
                                    </Button>
                                    <Button
                                      variant="ghost"
                                      onClick={() => {
                                        setJudgeFor(null)
                                        setJudgeDraft({ verdict: '', note: '' })
                                        setJudgeError('')
                                      }}
                                    >
                                      取消
                                    </Button>
                                  </div>
                                </div>
                              )}
                            </li>
                          )
                        })}
                      </ul>
                    </div>

                    {pointFor === record.id ? (
                      <div className="mt-2 flex flex-col gap-2 rounded border border-border-strong bg-surface p-3">
                        <Field
                          label="判定条件"
                          htmlFor={`${baseId}-new-condition-${record.id}`}
                          required
                          hint="写清什么算成立、什么算不成立"
                        >
                          <Textarea
                            id={`${baseId}-new-condition-${record.id}`}
                            rows={2}
                            value={pointDraft.condition}
                            onChange={(event) =>
                              setPointDraft({ ...pointDraft, condition: event.target.value })
                            }
                          />
                        </Field>
                        <Field
                          label="到期日"
                          htmlFor={`${baseId}-new-due-${record.id}`}
                          required
                          hint="可手填 YYYY-MM-DD，也可用日历选择"
                        >
                          <Input
                            id={`${baseId}-new-due-${record.id}`}
                            type="date"
                            value={pointDraft.due}
                            invalid={Boolean(pointError)}
                            onChange={(event) =>
                              setPointDraft({ ...pointDraft, due: event.target.value })
                            }
                          />
                        </Field>
                        {pointError && (
                          <p role="alert" className="text-caption text-danger">
                            {pointError}
                          </p>
                        )}
                        <div className="flex flex-wrap items-center gap-2">
                          <Button variant="secondary" onClick={() => submitPoint(record.id)}>
                            添加裁决点
                          </Button>
                          <Button
                            variant="ghost"
                            onClick={() => {
                              setPointFor(null)
                              setPointDraft({ condition: '', due: '' })
                              setPointError('')
                            }}
                          >
                            取消
                          </Button>
                        </div>
                      </div>
                    ) : (
                      <Button
                        variant="ghost"
                        className="mt-2"
                        leadingIcon={<Plus className="size-3.5" aria-hidden="true" />}
                        onClick={() => {
                          setPointFor(record.id)
                          setPointDraft({ condition: '', due: '' })
                          setPointError('')
                          setSavedNote('')
                        }}
                      >
                        加裁决点
                      </Button>
                    )}
                  </li>
                )
              })}
            </ul>
          )}

          <p className="mt-3 border-t border-border pt-2 text-caption leading-body text-text-muted">
            论点、裁决点与判定留痕只保存在本机 IndexedDB（thesis_notes），不上传、不进研究产物、
            不进日志或 AI 上下文；清空浏览器站点数据会一并删除。
          </p>
        </>
      )}
    </>
  )

  return (
    <SectionCard
      className={className}
      tone={overdue > 0 ? 'warn' : 'neutral'}
      title="我的论点与判断"
      subtitle={`写下判断，到期必判 · 只存本机（thesis_notes）`}
      actions={
        <>
          {overdue > 0 && <Badge tone="danger">已到期未判 {overdue}</Badge>}
          {code && !formMode && (
            <Button
              variant="secondary"
              leadingIcon={<Plus className="size-4" aria-hidden="true" />}
              onClick={startNew}
            >
              写论点
            </Button>
          )}
        </>
      }
    >
      {sections}

      <ConfirmDialog
        open={deleteTarget !== null}
        title="删除这条论点？"
        description={
          deleteTarget
            ? `「${deleteTarget.title}」及其 ${deleteTarget.points.length} 个裁决点、全部判定留痕会从本机删除，无法恢复。`
            : undefined
        }
        tone="danger"
        confirmLabel="删除"
        cancelLabel="保留"
        onConfirm={() => {
          if (deleteTarget) {
            removeThesis(deleteTarget.id)
            setSavedNote('论点已删除')
          }
          setDeleteTarget(null)
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </SectionCard>
  )
}
