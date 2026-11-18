import { useEffect, useMemo, useState } from 'react'
import { CalendarClock, Loader2, Play, Plus, RefreshCw, Swords, Sparkles, Square, X } from 'lucide-react'

import { ApiError } from '../api'
import { SenateRunner } from '../screens/skills/SkillDetailScreen'
import {
  addPreference,
  deletePreference,
  fetchPreferences,
  type PreferencesPayload,
} from '../api/preferences'
import {
  fetchScheduleJobs,
  runScheduleJob,
  setPlanCreateFlag,
  setPlanStatus,
  toggleScheduleJob,
  type ScheduleBackgroundJob,
  type ScheduleJob,
  type ScheduleJobsPayload,
  type SchedulePlan,
} from '../api/schedule'

/**
 * 对话输入框底行的「技能」入口（豆包 / DeepSeek 电脑端那一排的对应物）。
 *
 * 在它之前，「定时研究 / 盯盘提醒 / 盘中实时盯盘 / 跨轮任务」只活在
 * `config/schedule.yaml` 与 `data/research_plans.json` 里：任务跑得再好，用户不看
 * 文件就不知道有这些东西。这个弹层把四类东西摆到页面上：
 *
 * 1. **我正在跟的任务**：跨轮任务（「帮我盯住浦发银行一周」）；熔断停下的也留在这，
 *    带失败次数、最后一次失败原因，以及逐轮复盘——用户能判断还值不值得继续跟；
 * 2. **我记住的偏好**：助手会按偏好回答，所以必须能看、能自己加、能删；
 * 3. **立即执行**：盯盘 / 盘中盯盘 / 定时研究 / 跨轮任务各一个「执行」按钮；
 * 4. **定时开关**：同样的开关、触发时间、阈值、最近一次产物。
 *
 * 两个接口各自独立失败：任务读不出来不该把偏好也藏掉，反之亦然。
 */

/** 底行快捷技能：按执行顺序排列，每类只取第一个匹配任务 */
const SKILLS: readonly { kind: string; label: string; hint: string }[] = [
  { kind: 'watch_alert', label: '盯盘', hint: '取一次本机报价快照，越过阈值就写收件箱' },
  { kind: 'watch_live', label: '盘中盯盘', hint: '盘中每 30 秒现取实时价，只在交易时段运行' },
  { kind: 'agent_research', label: '定时研究', hint: '交给 agent：模型自己选工具、自己定轮次' },
  { kind: 'plan_tick', label: '跨轮任务', hint: '把你说过的「盯它一周」按频率各推一轮' },
]

const FREQUENCY_LABEL: Record<string, string> = {
  daily: '每天盘后',
  intraday: '盘中按间隔',
  on_move: '有异动就推',
}

function planSummary(plan: SchedulePlan): string {
  if (!plan.last_summary) return plan.runs > 0 ? '上次没留结论' : '还没开始推进'
  return plan.last_summary.length > 60 ? `${plan.last_summary.slice(0, 60)}…` : plan.last_summary
}

function describeTrigger(job: ScheduleJob): string {
  const trigger = job.trigger?.trim()
  if (!trigger) return '未设置触发'
  if (/^\d+s$/.test(trigger)) {
    const seconds = Number(trigger.replace('s', ''))
    return seconds >= 60 ? `每 ${Math.round(seconds / 60)} 分钟` : `每 ${seconds} 秒`
  }
  return `每天 ${trigger}`
}

function thresholdText(job: ScheduleJob): string {
  const parts: string[] = []
  if (job.alert_pct !== null) parts.push(`较昨收 ±${job.alert_pct}%`)
  if (job.move_pct !== null) parts.push(`较上次快照 ±${job.move_pct}%`)
  if (job.min_gap_min !== null) parts.push(`${job.min_gap_min} 分钟内不重复`)
  return parts.join(' · ')
}

function lastRunText(job: ScheduleJob): string | null {
  const run = job.last_run
  if (!run) return null
  const label: Record<string, string> = {
    ok: '成功',
    failed: '失败',
    skipped: '跳过',
    running: '进行中',
    partial: '部分失败',
  }
  const when = (run.finished_at ?? run.started_at ?? '').replace('T', ' ').slice(0, 16)
  const status = label[run.status] ?? run.status
  return `上次执行：${when || '时间未知'} ${status}${run.detail ? `（${run.detail}）` : ''}`
}

export function ScheduleDock() {
  const [open, setOpen] = useState(false)
  // 「多空辩论」入口：跟「技能」并排。点开就地跑一次合议，不跳页。
  const [debateOpen, setDebateOpen] = useState(false)
  const [payload, setPayload] = useState<ScheduleJobsPayload | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  // 偏好：能看、能自己加、能删。
  const [prefs, setPrefs] = useState<PreferencesPayload | null>(null)
  const [prefsError, setPrefsError] = useState<string | null>(null)
  const [prefDraft, setPrefDraft] = useState('')
  const [prefBusy, setPrefBusy] = useState<string | null>(null)

  // 跨轮任务：正在改状态的那条 + 展开了复盘的那条
  const [planBusy, setPlanBusy] = useState<string | null>(null)
  const [reviewOpen, setReviewOpen] = useState<string | null>(null)

  // 「推进时允许新建任务」开关正在改的那条 plan_tick 任务；后台任务区展开状态
  const [flagBusy, setFlagBusy] = useState<string | null>(null)
  const [bgOpen, setBgOpen] = useState(false)

  async function load() {
    setError(null)
    try {
      setPayload(await fetchScheduleJobs())
    } catch (exc) {
      setPayload(null)
      setError(exc instanceof ApiError ? exc.message : `定时任务读不出来：${String(exc)}`)
    }
    try {
      setPrefs(await fetchPreferences())
      setPrefsError(null)
    } catch (exc) {
      setPrefs(null)
      setPrefsError(exc instanceof ApiError ? exc.message : `偏好读不出来：${String(exc)}`)
    }
  }

  useEffect(() => {
    if (!open) return
    void load()
    // 面板开着时再看一眼产物：跑完一次会新落研究卡 / 新写收件箱
    const timer = setInterval(() => void load(), 15000)
    return () => clearInterval(timer)
  }, [open])

  async function onRun(job: ScheduleJob, label = job.name) {
    setBusy(job.id)
    setNote(null)
    try {
      const result = await runScheduleJob(job.id)
      setNote(
        result.started
          ? `${label} 已在后台开始，跑完进「提醒」收件箱`
          : `${label} 未启动：${result.detail ?? '原因未知'}`,
      )
      setTimeout(() => void load(), 4000)
    } catch (exc) {
      setNote(exc instanceof ApiError ? exc.message : `触发失败：${String(exc)}`)
    } finally {
      setBusy(null)
    }
  }

  async function onToggle(job: ScheduleJob) {
    setBusy(job.id)
    setNote(null)
    try {
      await toggleScheduleJob(job.id, !job.enabled)
      await load()
      setNote(`${job.name} 已${job.enabled ? '关闭' : '开启'}`)
    } catch (exc) {
      setNote(exc instanceof ApiError ? exc.message : `开关失败：${String(exc)}`)
    } finally {
      setBusy(null)
    }
  }

  async function onDeletePreference(text: string) {
    setPrefBusy(text)
    setNote(null)
    try {
      setPrefs(await deletePreference(text))
      setNote(`已删掉「${text}」`)
    } catch (exc) {
      setNote(exc instanceof ApiError ? exc.message : `删除失败：${String(exc)}`)
    } finally {
      setPrefBusy(null)
    }
  }

  async function onAddPreference() {
    const text = prefDraft.trim()
    if (!text) return
    setPrefBusy('__add')
    setNote(null)
    try {
      setPrefs(await addPreference(text))
      setPrefDraft('')
      setNote(`记住了「${text}」`)
    } catch (exc) {
      setNote(exc instanceof ApiError ? exc.message : `添加失败：${String(exc)}`)
    } finally {
      setPrefBusy(null)
    }
  }

  /** 熔断停下的任务要能被用户放行（恢复 = 服务端把失败计数归零），也能主动结束。 */
  async function onPlanStatus(id: string, status: string) {
    setPlanBusy(id)
    setNote(null)
    try {
      const next = await setPlanStatus(id, status)
      setPayload((current) => (current ? { ...current, plans: next } : current))
      setNote(status === 'active' ? '已恢复跟踪，下一轮会重新推进' : '已结束这条任务')
    } catch (exc) {
      setNote(exc instanceof ApiError ? exc.message : `改状态失败：${String(exc)}`)
    } finally {
      setPlanBusy(null)
    }
  }

  /**
   * 「推进时不建新任务」的显式开关。
   *
   * 关（默认）：推进只产出一轮结论，不会每推一步就繁殖一条同标任务；
   * 开：推进里出现「持续跟踪…」时会再建一条跨轮任务（上限仍受 MAX_ACTIVE 约束）。
   */
  async function onPlanCreateFlag(job: ScheduleJob) {
    setFlagBusy(job.id)
    setNote(null)
    const next = !(job.allow_plan_create === true)
    try {
      await setPlanCreateFlag(job.id, next)
      setPayload((current) =>
        current
          ? {
              ...current,
              jobs: current.jobs.map((item) =>
                item.id === job.id ? { ...item, allow_plan_create: next } : item,
              ),
            }
          : current,
      )
      setNote(
        next
          ? '已允许：推进里可以新建任务（注意别让同一只票繁殖多条）'
          : '已关闭：推进只给结论，不会再建新任务',
      )
    } catch (exc) {
      setNote(exc instanceof ApiError ? exc.message : `改开关失败：${String(exc)}`)
    } finally {
      setFlagBusy(null)
    }
  }

  const jobs = payload?.jobs ?? []
  const plans = payload?.plans.items ?? []
  const plansPayload = payload?.plans ?? null
  const enabledCount = jobs.filter((job) => job.enabled).length
  /** 跨轮推进任务：它身上挂着「推进时允许新建任务」的开关 */
  const planTickJob = jobs.find((job) => job.kind === 'plan_tick') ?? null
  /** 后台高频任务（快照 / 同步 / 摘要…）：只读展示，不给开关 */
  const background: ScheduleBackgroundJob[] = payload?.background ?? []
  const backgroundOn = background.filter((job) => job.enabled).length
  // 首次打开时还没拉到数据：技能行仍显示（按钮置灰），不让入口凭空消失
  const skillRows = useMemo(
    () =>
      SKILLS.map((skill) => ({
        ...skill,
        job: jobs.find((job) => job.kind === skill.kind) ?? null,
      })),
    [jobs],
  )

  return (
    <div className="relative flex items-center">
      <button
        type="button"
        aria-label="技能与定时任务"
        aria-expanded={open}
        data-testid="schedule-dock-trigger"
        onClick={() => setOpen((value) => !value)}
        className="inline-flex items-center gap-1.5 rounded-pill border border-border bg-surface-2 px-2.5 py-1 text-12px leading-tight text-text-secondary transition-colors duration-fast hover:border-border-strong hover:text-text"
      >
        <Sparkles className="size-3.5" aria-hidden="true" />
        技能
        {jobs.length > 0 && <span className="text-text-muted">{enabledCount}/{jobs.length}</span>}
      </button>

      <button
        type="button"
        aria-label="多空辩论：就地发起一次 Senate 合议"
        aria-expanded={debateOpen}
        onClick={() => setDebateOpen((value) => !value)}
        className="ml-1.5 inline-flex items-center gap-1.5 rounded-pill border border-accent/40 bg-accent-soft px-2.5 py-1 text-12px leading-tight text-text transition-colors duration-fast hover:border-accent"
      >
        <Swords className="size-3.5" aria-hidden="true" />
        多空辩论
      </button>

      {debateOpen && (
        <>
          <div
            className="fixed inset-0 z-20 bg-black/15"
            aria-hidden="true"
            onClick={() => setDebateOpen(false)}
          />
          <div
            role="dialog"
            aria-label="多空辩论"
            className="fixed left-1/2 top-1/2 z-30 flex max-h-[calc(100vh-96px)] w-[min(720px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col gap-2 overflow-y-auto rounded-2xl border border-border-strong bg-surface p-3 shadow-lg"
          >
            <div className="flex shrink-0 items-center justify-between gap-2">
              <span className="text-13px font-medium text-text">多空辩论 · StockSenate</span>
              <button
                type="button"
                aria-label="关闭辩论面板"
                onClick={() => setDebateOpen(false)}
                className="inline-flex items-center rounded-pill border border-border px-2 py-0.5 text-11px text-text-muted hover:border-border-strong hover:text-text"
              >
                <X className="size-3" aria-hidden="true" />
                关闭
              </button>
            </div>
            <SenateRunner />
          </div>
        </>
      )}

      {open && (
        <>
          <div
            className="fixed inset-0 z-20 bg-black/15"
            aria-hidden="true"
            onClick={() => setOpen(false)}
          />
          <div
            role="dialog"
            aria-label="技能与定时任务"
            data-testid="schedule-dock-panel"
            className="fixed left-1/2 top-1/2 z-30 flex max-h-[calc(100vh-96px)] w-[min(560px,calc(100vw-32px))] -translate-x-1/2 -translate-y-1/2 flex-col overflow-y-auto rounded-2xl border border-border-strong bg-surface p-3 shadow-lg"
          >
            <div className="mb-2 flex shrink-0 items-center justify-between gap-2">
              <span className="text-13px font-medium text-text">技能 · 盯盘与研究</span>
              <button
                type="button"
                onClick={() => void load()}
                className="inline-flex items-center gap-1 rounded-pill border border-border px-2 py-0.5 text-11px text-text-muted hover:border-border-strong hover:text-text"
              >
                <RefreshCw className="size-3" aria-hidden="true" />
                刷新
              </button>
            </div>

            {error && (
              <p className="mb-2 shrink-0 rounded-lg border border-border bg-surface-2 p-2 text-12px leading-relaxed text-text-secondary">
                {error}
              </p>
            )}

            <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
              <section>
                <p className="mb-1 text-11px text-text-muted">我正在跟的任务</p>
                {plans.length === 0 ? (
                  <p className="px-1 text-11px leading-relaxed text-text-muted">
                    {plansPayload?.ok === false
                      ? plansPayload.detail ?? '任务状态读不出来'
                      : '还没有。跟它说一句「帮我盯住浦发银行一周」「半导体板块持续看」，就会自动建。'}
                  </p>
                ) : (
                  <div className="flex flex-col gap-1.5">
                    {plans.map((plan) => {
                      const paused = plan.status === 'paused'
                      return (
                        <div
                          key={plan.id}
                          data-testid={`schedule-plan-${plan.id}`}
                          className={`rounded-xl border px-2 py-1.5 ${
                            paused ? 'border-danger bg-surface-2' : 'border-border bg-surface-2'
                          }`}
                        >
                          <div className="flex items-baseline justify-between gap-2">
                            <p className="truncate text-12px font-medium text-text">{plan.label}</p>
                            <span
                              className={`shrink-0 text-11px ${
                                paused ? 'text-danger' : 'text-text-muted'
                              }`}
                            >
                              {paused
                                ? `已暂停 · 连续 ${plan.failures} 次失败`
                                : `已推进 ${plan.runs} 次`}
                            </span>
                          </div>
                          <p className="mt-0.5 text-11px text-text-muted">
                            {FREQUENCY_LABEL[plan.frequency] ?? plan.frequency}
                            {plan.expires_at ? ` · 至 ${plan.expires_at.slice(0, 10)}` : ''}
                          </p>
                          <p className="mt-1 line-clamp-2 text-11px leading-snug text-text-secondary">
                            {paused ? plan.last_error || planSummary(plan) : planSummary(plan)}
                          </p>
                          <div className="mt-1 flex items-center gap-1.5">
                            {plan.review.length > 0 && (
                              <button
                                type="button"
                                data-testid={`plan-review-${plan.id}`}
                                onClick={() => setReviewOpen(reviewOpen === plan.id ? null : plan.id)}
                                className="rounded-pill border border-border bg-surface px-2 py-0.5 text-11px text-text-secondary hover:border-border-strong hover:text-text"
                              >
                                {reviewOpen === plan.id ? '收起复盘' : `复盘 ${plan.review.length} 轮`}
                              </button>
                            )}
                            {paused && (
                              <button
                                type="button"
                                data-testid={`plan-resume-${plan.id}`}
                                disabled={planBusy === plan.id}
                                onClick={() => void onPlanStatus(plan.id, 'active')}
                                className="rounded-pill border border-border bg-surface px-2 py-0.5 text-11px text-text hover:border-border-strong disabled:opacity-40"
                              >
                                恢复
                              </button>
                            )}
                            <button
                              type="button"
                              disabled={planBusy === plan.id}
                              onClick={() => void onPlanStatus(plan.id, 'done')}
                              className="rounded-pill border border-border bg-surface px-2 py-0.5 text-11px text-text-muted hover:border-border-strong hover:text-text disabled:opacity-40"
                            >
                              {paused ? '结束' : '不再跟'}
                            </button>
                          </div>
                          {reviewOpen === plan.id && (
                            <div className="mt-1 flex flex-col gap-0.5 border-t border-border pt-1">
                              {plan.review.map((row, index) => (
                                <p
                                  key={`${row.at}-${index}`}
                                  className="text-11px leading-snug text-text-muted"
                                >
                                  <span className={row.ok ? 'text-text-secondary' : 'text-danger'}>
                                    {row.date || row.at.slice(0, 10)} {row.ok ? '成功' : '失败'}
                                  </span>
                                  {' · '}
                                  {row.summary || '没留结论'}
                                </p>
                              ))}
                            </div>
                          )}
                        </div>
                      )
                    })}
                  </div>
                )}
              </section>

              {planTickJob && (
                <section>
                  <p className="mb-1 text-11px text-text-muted">跨轮推进设置</p>
                  <div
                    data-testid="plan-create-flag"
                    className="rounded-xl border border-border bg-surface-2 px-2 py-1.5"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="text-12px font-medium text-text">推进时允许新建任务</p>
                        <p className="mt-0.5 text-11px leading-snug text-text-muted">
                          {planTickJob.allow_plan_create
                            ? '开：推进里出现「持续跟踪…」会再建一条跨轮任务，注意上限'
                            : '关（默认）：推进只给这一轮结论，不会再繁殖同标任务'}
                        </p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={planTickJob.allow_plan_create === true}
                        aria-label="推进时允许新建任务"
                        data-testid="plan-create-flag-toggle"
                        disabled={flagBusy === planTickJob.id}
                        onClick={() => void onPlanCreateFlag(planTickJob)}
                        className={`inline-flex shrink-0 items-center gap-1 rounded-pill border px-2 py-1 text-11px transition-colors disabled:opacity-40 ${
                          planTickJob.allow_plan_create
                            ? 'border-border-strong bg-surface text-text'
                            : 'border-border bg-surface text-text-muted hover:border-border-strong hover:text-text'
                        }`}
                      >
                        {flagBusy === planTickJob.id ? (
                          <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                        ) : null}
                        {planTickJob.allow_plan_create ? '已开 · 点此关闭' : '已关 · 点此开启'}
                      </button>
                    </div>
                  </div>
                </section>
              )}

              <section>
                <p className="mb-1 text-11px text-text-muted">
                  我记住的偏好
                  {prefs ? `（${prefs.count}/${prefs.max_items}）` : ''}
                </p>
                {prefsError ? (
                  <p className="px-1 text-11px leading-relaxed text-text-muted">{prefsError}</p>
                ) : prefs && prefs.items.length > 0 ? (
                  <div className="flex flex-col gap-1">
                    {prefs.items.map((item) => (
                      <div
                        key={`${item.kind}:${item.text}`}
                        data-testid={`preference-${item.kind}`}
                        className="flex items-start justify-between gap-2 rounded-xl border border-border bg-surface-2 px-2 py-1.5"
                      >
                        <div className="min-w-0">
                          <p className="text-11px text-text-muted">
                            {item.kind_label}
                            {item.hits > 1 ? ` · 提过 ${item.hits} 次` : ''}
                          </p>
                          <p className="mt-0.5 break-words text-12px leading-snug text-text">
                            {item.text}
                          </p>
                        </div>
                        <button
                          type="button"
                          aria-label={`删掉偏好：${item.text}`}
                          disabled={prefBusy === item.text}
                          onClick={() => void onDeletePreference(item.text)}
                          className="inline-flex shrink-0 items-center rounded-pill border border-border bg-surface p-1 text-text-muted hover:border-border-strong hover:text-text disabled:opacity-40"
                        >
                          {prefBusy === item.text ? (
                            <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                          ) : (
                            <X className="size-3" aria-hidden="true" />
                          )}
                        </button>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="px-1 text-11px leading-relaxed text-text-muted">
                    {prefs
                      ? '还没有。你随口说的「别太长」「我只看创业板」会自动记在这里。'
                      : '读取中…'}
                  </p>
                )}
                <div className="mt-1.5 flex items-center gap-1.5">
                  <input
                    value={prefDraft}
                    aria-label="加一条偏好"
                    placeholder="加一条：例如「结论先行，别太长」"
                    onChange={(event) => setPrefDraft(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter') void onAddPreference()
                    }}
                    className="min-w-0 flex-1 rounded-lg border border-border bg-surface px-2 py-1 text-11px text-text placeholder:text-text-muted focus:border-border-strong focus:outline-none"
                  />
                  <button
                    type="button"
                    disabled={!prefDraft.trim() || prefBusy === '__add'}
                    onClick={() => void onAddPreference()}
                    className="inline-flex shrink-0 items-center gap-1 rounded-pill border border-border bg-surface px-2 py-1 text-11px text-text hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {prefBusy === '__add' ? (
                      <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                    ) : (
                      <Plus className="size-3" aria-hidden="true" />
                    )}
                    加
                  </button>
                </div>
              </section>

              <section>
                <p className="mb-1 text-11px text-text-muted">立即执行</p>
                <div className="flex flex-col gap-1.5">
                  {skillRows.map(({ kind, label, hint, job }) => (
                    <div
                      key={kind}
                      data-testid={`schedule-skill-${kind}`}
                      className="flex items-center justify-between gap-2 rounded-xl border border-border bg-surface-2 px-2 py-1.5"
                    >
                      <div className="min-w-0">
                        <p className="text-12px font-medium text-text">{label}</p>
                        <p className="text-11px leading-snug text-text-muted">{hint}</p>
                      </div>
                      <button
                        type="button"
                        disabled={!job || busy === job?.id}
                        onClick={() => job && void onRun(job, label)}
                        title={job ? `执行 ${job.name}` : '这个任务还没配置'}
                        className="inline-flex shrink-0 items-center gap-1 rounded-pill border border-border bg-surface px-2 py-1 text-11px text-text hover:border-border-strong disabled:cursor-not-allowed disabled:opacity-40"
                      >
                        {job && busy === job.id ? (
                          <Loader2 className="size-3 animate-spin" aria-hidden="true" />
                        ) : (
                          <Play className="size-3" aria-hidden="true" />
                        )}
                        执行
                      </button>
                    </div>
                  ))}
                </div>
              </section>

              <section>
                <p className="mb-1 text-11px text-text-muted">定时开关</p>
                {!error && jobs.length === 0 && (
                  <p className="px-1 text-11px text-text-muted">
                    {payload ? '调度表里还没有这几类任务' : '读取中…'}
                  </p>
                )}
                <div className="flex flex-col gap-1.5">
                  {jobs.map((job) => (
                    <div
                      key={job.id}
                      data-testid={`schedule-job-${job.id}`}
                      className="rounded-xl border border-border bg-surface-2 px-2 py-1.5"
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <p className="truncate text-12px font-medium text-text">{job.name}</p>
                          <p className="mt-0.5 text-11px text-text-muted">
                            {describeTrigger(job)}
                            {job.enabled ? ' · 已开启' : ' · 已关闭'}
                          </p>
                        </div>
                        <button
                          type="button"
                          disabled={busy === job.id}
                          onClick={() => void onToggle(job)}
                          aria-label={`${job.enabled ? '关闭' : '开启'} ${job.name}`}
                          className="inline-flex shrink-0 items-center gap-1 rounded-pill border border-border bg-surface px-2 py-1 text-11px text-text hover:border-border-strong disabled:opacity-50"
                        >
                          {job.enabled ? (
                            <Square className="size-3" aria-hidden="true" />
                          ) : (
                            <CalendarClock className="size-3" aria-hidden="true" />
                          )}
                          {job.enabled ? '关闭' : '开启'}
                        </button>
                      </div>
                      {thresholdText(job) && (
                        <p className="mt-1 text-11px text-text-muted">阈值：{thresholdText(job)}</p>
                      )}
                      {job.question && (
                        <p className="mt-1 line-clamp-2 text-11px text-text-muted">问：{job.question}</p>
                      )}
                      <p className="mt-1 text-11px text-text-muted">
                        {job.latest_digest
                          ? `最近产物：${job.latest_digest.date}（${job.latest_digest.written_at ?? '时间未知'}）`
                          : '还没有产物'}
                      </p>
                      {lastRunText(job) && (
                        <p className="mt-1 text-11px text-text-muted">{lastRunText(job)}</p>
                      )}
                    </div>
                  ))}
                </div>
              </section>

              {background.length > 0 && (
                <section>
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-11px text-text-muted">
                      后台任务（{backgroundOn}/{background.length} 在跑）
                    </p>
                    <button
                      type="button"
                      data-testid="schedule-background-toggle"
                      onClick={() => setBgOpen((value) => !value)}
                      className="rounded-pill border border-border px-2 py-0.5 text-11px text-text-muted hover:border-border-strong hover:text-text"
                    >
                      {bgOpen ? '收起' : '展开'}
                    </button>
                  </div>
                  <p className="mt-0.5 px-1 text-11px leading-relaxed text-text-muted">
                    快照 / 同步这类高频任务由后台按表跑，页面只读、不给执行按钮——
                    点一下就会和行情同步抢上游。
                  </p>
                  {bgOpen && (
                    <div className="mt-1 flex flex-col gap-1">
                      {background.map((job) => (
                        <div
                          key={job.id}
                          data-testid={`schedule-background-${job.id}`}
                          className="rounded-xl border border-border bg-surface-2 px-2 py-1.5"
                        >
                          <div className="flex items-baseline justify-between gap-2">
                            <p className="truncate text-12px text-text">{job.name}</p>
                            <span
                              className={`shrink-0 text-11px ${
                                job.enabled ? 'text-text-secondary' : 'text-text-muted'
                              }`}
                            >
                              {job.enabled ? '在跑' : '已停'}
                              {job.session_bound ? ' · 仅交易时段' : ''}
                            </span>
                          </div>
                          <p className="mt-0.5 text-11px text-text-muted">
                            {job.kind_label} · {describeTrigger(job)}
                          </p>
                          <p className="mt-0.5 text-11px text-text-muted">
                            {job.latest_digest
                              ? `最近产物：${job.latest_digest.date}（${job.latest_digest.written_at ?? '时间未知'}）`
                              : '还没有产物'}
                          </p>
                          {lastRunText(job) && (
                            <p className="mt-0.5 text-11px text-text-muted">{lastRunText(job)}</p>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </section>
              )}
            </div>

            {note && (
              <p className="mt-2 shrink-0 text-11px leading-relaxed text-text-secondary">{note}</p>
            )}
            <p className="mt-2 shrink-0 text-11px text-text-muted">
              执行是后台异步，结果落在「提醒」页，不在这里同步显示。
            </p>
          </div>
        </>
      )}
    </div>
  )
}

export default ScheduleDock
