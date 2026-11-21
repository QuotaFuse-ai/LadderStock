/**
 * 规则（`/alert`）：DEC-087 提醒引擎的管理页——规则 / 渠道 / 手动跑 / 触发历史。
 *
 * 三个标签页对应后端的四组资源（`api/alert.ts` 有完整契约注释）：
 *
 * - **规则**：清单 + 新建 / 启停 / 删除。七类规则各有口径（均线类不用阈值、
 *   pnl 类必须有持仓成本），表单按类型动态切字段；非法输入由后端 400 的人话
 *   报错兜底，页面只做最小的前置校验（空值 / 代码格式），不重复实现后端规则；
 * - **渠道**：清单（URL 已由后端打码）+ 新建 / 删除 / 真发一条测试。
 *   SMTP 密码只存环境变量名（`password_env`），表单永远不出现密码框；
 * - **历史**：最近触发记录（含每条渠道投递结果）。
 *
 * 「手动跑一轮」放页头：与调度任务 `alert_check` 是同一个执行体（三面共享），
 * 真取数（现价 + 本地历史），可能要几十秒——按钮进待态。
 *
 * 空态 / 错误态都给下一步动作（QUOT 前端规约 §8），不静默空白；
 * 取不到的数字显示「—」（MISSING），不补 0。
 */

import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  BellRing,
  Loader2,
  Mail,
  MessageSquare,
  Plus,
  Puzzle,
  RefreshCw,
  Send,
  Trash2,
  Zap,
} from 'lucide-react'

import { describeApiError } from '../api'
import {
  CHANNEL_KIND_LABELS,
  RULE_KIND_LABELS,
  createChannel,
  createRule,
  deleteChannel,
  deleteRule,
  describeRule,
  fetchChannels,
  fetchHistory,
  fetchRules,
  runAlerts,
  testChannel,
  toggleRule,
  type AlertChannel,
  type AlertRule,
  type ChannelTestResult,
  type HistoryItem,
  type RunReport,
  type RuleDraft,
} from '../api/alert'
import { OfflineBanner, PageHeader } from '../components/PageShell'
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  EmptyState,
  ErrorBlock,
  Input,
  SectionCard,
  Segmented,
} from '../components/ui'
import { cn } from '../lib/cn'
import { MISSING } from '../lib/format'
import { useAsync } from '../lib/useAsync'

type Tab = 'rules' | 'channels' | 'history'

const TABS = [
  { value: 'rules', label: '规则' },
  { value: 'channels', label: '渠道' },
  { value: 'history', label: '历史' },
] as const

type RuleFormKind =
  | 'price_above'
  | 'price_below'
  | 'ma_break_down'
  | 'ma_break_up'
  | 'volume_spike'
  | 'pnl_drop'
  | 'pnl_gain'

const RULE_FORM_KINDS: ReadonlyArray<{ value: RuleFormKind; label: string; hint: string }> = [
  { value: 'price_below', label: '价格跌破', hint: '现价跌破指定价格（元）时提醒（常用：止损线）' },
  { value: 'price_above', label: '价格涨到', hint: '现价涨到指定价格（元）时提醒（常用：目标价）' },
  { value: 'ma_break_down', label: '跌破均线', hint: '现价跌破 N 日均线时提醒（不用填阈值）' },
  { value: 'ma_break_up', label: '站上均线', hint: '现价站上 N 日均线时提醒（不用填阈值）' },
  { value: 'volume_spike', label: '放量', hint: '当日成交量达到前 N 日均量的指定倍数时提醒' },
  { value: 'pnl_drop', label: '浮亏超过', hint: '浮亏超过指定百分比时提醒（需本机有持仓）' },
  { value: 'pnl_gain', label: '浮盈超过', hint: '浮盈超过指定百分比时提醒（需本机有持仓）' },
]

const CHANNEL_FORM_KINDS: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'dingtalk', label: '钉钉机器人' },
  { value: 'feishu', label: '飞书机器人' },
  { value: 'wecom', label: '企业微信机器人' },
  { value: 'webhook', label: '通用 Webhook' },
  { value: 'email', label: '邮件（SMTP）' },
]

const CHANNEL_ICONS: Record<string, typeof Mail> = {
  email: Mail,
  webhook: Puzzle,
  dingtalk: MessageSquare,
  feishu: MessageSquare,
  wecom: MessageSquare,
}

function StateLabel({ rule }: { rule: AlertRule }) {
  if (!rule.enabled) return <Badge tone="neutral">已停用</Badge>
  const status = rule.state?.status
  if (!status || status === 'armed') return <Badge tone="info">待命</Badge>
  if (status === 'cooldown') return <Badge tone="warn">冷却中</Badge>
  return <Badge tone="neutral">{status}</Badge>
}

function describeError(error: unknown): string {
  const info = describeApiError(error)
  return `${info.title}：${info.detail}`
}

export default function AlertRulesScreen() {
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>('rules')
  const [runPanel, setRunPanel] = useState<RunReport | null>(null)
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  // 规则表单
  const [ruleKind, setRuleKind] = useState<RuleFormKind>('price_below')
  const [ruleSymbol, setRuleSymbol] = useState('')
  const [ruleThreshold, setRuleThreshold] = useState('')
  const [ruleWindow, setRuleWindow] = useState('20')
  const [ruleCooldown, setRuleCooldown] = useState('60')
  const [ruleChannels, setRuleChannels] = useState<string[]>([])
  const [ruleNote, setRuleNote] = useState('')

  // 渠道表单
  const [channelKind, setChannelKind] = useState('dingtalk')
  const [channelId, setChannelId] = useState('')
  const [channelName, setChannelName] = useState('')
  const [channelUrl, setChannelUrl] = useState('')
  const [mailHost, setMailHost] = useState('')
  const [mailPort, setMailPort] = useState('465')
  const [mailFrom, setMailFrom] = useState('')
  const [mailTo, setMailTo] = useState('')
  const [mailUser, setMailUser] = useState('')
  const [testingChannelId, setTestingChannelId] = useState<string | null>(null)
  const [testResults, setTestResults] = useState<Record<string, ChannelTestResult>>({})

  const data = useAsync(
    async () => {
      const [rules, channels, history] = await Promise.all([
        fetchRules(),
        fetchChannels(),
        fetchHistory(30),
      ])
      return {
        rules: rules.rules,
        channels: channels.channels,
        history: history.items,
      }
    },
    [],
  )
  const rules = data.data?.rules ?? []
  const channels = data.data?.channels ?? []
  const history = data.data?.history ?? []
  const loadError = data.status === 'error' ? describeError(data.error) : null

  const kindHint = RULE_FORM_KINDS.find((item) => item.value === ruleKind)?.hint ?? ''
  const needsThreshold = ruleKind !== 'ma_break_down' && ruleKind !== 'ma_break_up'
  const needsWindow =
    ruleKind === 'ma_break_down' || ruleKind === 'ma_break_up' || ruleKind === 'volume_spike'
  const thresholdLabel =
    ruleKind === 'volume_spike' ? '倍数' : ruleKind.startsWith('pnl') ? '百分比（%）' : '价格（元）'

  const reload = () => data.reload()

  const submitRule = async () => {
    setFormError(null)
    const symbol = ruleSymbol.trim()
    if (!/^\d{6}$/.test(symbol)) {
      setFormError('标的需要 6 位数字代码（如 600000）')
      return
    }
    if (needsThreshold && !ruleThreshold.trim()) {
      setFormError(`请填${RULE_KIND_LABELS[ruleKind] ?? ''}的${thresholdLabel.replace(/（.*）/, '')}`)
      return
    }
    const draft: RuleDraft = {
      kind: ruleKind,
      symbol,
      threshold: needsThreshold ? Number(ruleThreshold) : 0,
      channels: ruleChannels,
      cooldown_min: Number(ruleCooldown) || 60,
      note: ruleNote.trim(),
    }
    if (needsWindow && ruleWindow.trim()) draft.window = Number(ruleWindow)
    setBusy(true)
    try {
      await createRule(draft)
      setRuleSymbol('')
      setRuleThreshold('')
      setRuleNote('')
      reload()
    } catch (error) {
      setFormError(describeError(error))
    } finally {
      setBusy(false)
    }
  }

  const submitChannel = async () => {
    setFormError(null)
    const id = channelId.trim().toLowerCase()
    if (!/^[a-z0-9][a-z0-9_-]{1,31}$/.test(id)) {
      setFormError('渠道 id 需要 2-32 位小写字母 / 数字 / - / _')
      return
    }
    let config: Record<string, unknown>
    if (channelKind === 'email') {
      if (!mailHost.trim() || !mailFrom.trim() || !mailTo.trim()) {
        setFormError('邮件渠道需要 SMTP 服务器、发件人与收件人')
        return
      }
      config = {
        smtp_host: mailHost.trim(),
        smtp_port: Number(mailPort) || 465,
        from_addr: mailFrom.trim(),
        to_addrs: mailTo.split(/[,，;；\s]+/).filter(Boolean),
        user: mailUser.trim(),
      }
    } else {
      if (!channelUrl.trim().startsWith('http')) {
        setFormError('机器人 / Webhook 渠道需要以 http(s):// 开头的 URL')
        return
      }
      config = { url: channelUrl.trim() }
    }
    setBusy(true)
    try {
      await createChannel({ id, kind: channelKind, name: channelName.trim(), config })
      setChannelId('')
      setChannelName('')
      setChannelUrl('')
      setMailHost('')
      setMailFrom('')
      setMailTo('')
      setMailUser('')
      reload()
    } catch (error) {
      setFormError(describeError(error))
    } finally {
      setBusy(false)
    }
  }

  const onRun = async () => {
    setRunError(null)
    setRunPanel(null)
    setRunning(true)
    try {
      const report = await runAlerts()
      setRunPanel(report)
      reload()
    } catch (error) {
      setRunError(describeError(error))
    } finally {
      setRunning(false)
    }
  }

  const onToggleRule = async (rule: AlertRule) => {
    setBusy(true)
    try {
      await toggleRule(rule.id, !rule.enabled)
      reload()
    } finally {
      setBusy(false)
    }
  }

  const onDeleteRule = async (rule: AlertRule) => {
    setBusy(true)
    try {
      await deleteRule(rule.id)
      reload()
    } finally {
      setBusy(false)
    }
  }

  const onDeleteChannel = async (channel: AlertChannel) => {
    setBusy(true)
    try {
      await deleteChannel(channel.id)
      reload()
    } finally {
      setBusy(false)
    }
  }

  const onTestChannel = async (channel: AlertChannel) => {
    setTestingChannelId(channel.id)
    try {
      const result = await testChannel(channel.id)
      setTestResults((prev) => ({ ...prev, [channel.id]: result }))
    } catch (error) {
      setTestResults((prev) => ({
        ...prev,
        [channel.id]: { ok: false, detail: describeError(error) },
      }))
    } finally {
      setTestingChannelId(null)
    }
  }

  const noDataSymbols =
    runPanel?.results?.filter((row) => row.status === 'no_data').map((row) => row.symbol) ?? []

  return (
    <div className="mx-auto max-w-table px-page pb-16">
      <PageHeader
        title="提醒规则"
        subtitle="自定义规则 × 出站渠道：越线报一次，缺数不硬算（DEC-087）"
        actions={
          <div className="flex items-center gap-2">
            <Button size="md" variant="secondary" onClick={() => navigate('/inbox')}>
              查看收件箱
            </Button>
            <Button size="md" onClick={onRun} disabled={running || busy}>
              {running ? <Loader2 size={14} className="animate-spin" /> : <Zap size={14} />}
              {running ? '取数中…' : '手动跑一轮'}
            </Button>
          </div>
        }
      />

      {data.status === 'error' ? <OfflineBanner onRetry={reload} /> : null}

      {runError ? <ErrorBlock title="手动运行失败" detail={runError} onRetry={onRun} /> : null}
      {runPanel ? (
        <Card className="mb-4 mt-4">
          <CardHeader title={`最近一轮（${runPanel.ran_at}）`} />
          <CardBody>
            <p className="text-body text-text">
              <Badge
                tone={
                  runPanel.status === 'ok'
                    ? 'ok'
                    : runPanel.status === 'partial'
                      ? 'warn'
                      : 'neutral'
                }
              >
                {runPanel.status}
              </Badge>{' '}
              {runPanel.detail}
            </p>
            {runPanel.fired.length > 0 ? (
              <div className="mt-3 flex flex-col gap-2">
                {runPanel.fired.map((fired) => (
                  <div key={fired.rule_id} className="text-body text-text">
                    <BellRing size={14} className="mr-1 inline text-warn" />
                    {fired.title}
                    <span className="ml-2 text-caption text-text-muted">{fired.detail}</span>
                    {fired.deliveries.length > 0 ? (
                      <span className="ml-2 text-caption text-text-muted">
                        （投递：
                        {fired.deliveries
                          .map((delivery) => `${delivery.channel}${delivery.ok ? '✓' : '✗'}`)
                          .join('、')}
                        ）
                      </span>
                    ) : null}
                  </div>
                ))}
              </div>
            ) : null}
            {noDataSymbols.length > 0 ? (
              <p className="mt-2 text-caption text-text-muted">
                有规则缺数据未判定：{noDataSymbols.join('、')}（缺数不硬算，等下一轮或先补数据）
              </p>
            ) : null}
          </CardBody>
        </Card>
      ) : null}

      <div className="mt-4">
        <Segmented
          ariaLabel="提醒管理视图"
          value={tab}
          onChange={(next) => setTab(next)}
          options={TABS.map(({ value, label }) => ({ value, label }))}
        />
      </div>

      {formError ? <p className="mt-2 text-caption text-danger">{formError}</p> : null}

      {tab === 'rules' ? (
        <div className="mt-4 flex flex-col gap-4">
          <SectionCard title={`规则（${rules.length} 条）`}>
            {data.status === 'error' ? (
              <ErrorBlock title="规则读取失败" detail={loadError} onRetry={reload} />
            ) : rules.length === 0 ? (
              <EmptyState
                icon={<BellRing size={20} />}
                title="还没有提醒规则"
                description="用下面的表单加一条：比如「600000 价格跌破 10.20 时提醒我」。"
              />
            ) : (
              rules.map((rule) => {
                const channelNames = rule.channels.map(
                  (id) => channels.find((channel) => channel.id === id)?.name || id,
                )
                return (
                  <div
                    key={rule.id}
                    className="flex flex-col gap-2 border-b border-line py-3 last:border-b-0 md:flex-row md:items-center md:gap-4"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="text-body font-medium text-text">
                          {describeRule(rule)}
                        </span>
                        <StateLabel rule={rule} />
                      </div>
                      <div className="mt-1 text-caption text-text-muted">
                        冷却 {rule.cooldown_min} 分钟
                        {rule.channels.length > 0
                          ? ` · 投递：${channelNames.join('、')}`
                          : ' · 只落收件箱'}
                        {rule.note ? ` · ${rule.note}` : ''}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <Button
                        size="md"
                        variant="ghost"
                        onClick={() => onToggleRule(rule)}
                        disabled={busy}
                      >
                        {rule.enabled ? '停用' : '启用'}
                      </Button>
                      <Button
                        size="md"
                        variant="danger"
                        onClick={() => onDeleteRule(rule)}
                        disabled={busy}
                      >
                        <Trash2 size={14} />
                        删除
                      </Button>
                    </div>
                  </div>
                )
              })
            )}
          </SectionCard>

          <SectionCard title="新建规则">
            <div className="flex flex-col gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-caption text-text-muted">类型</span>
                <select
                  className="input"
                  value={ruleKind}
                  onChange={(event) => setRuleKind(event.target.value as RuleFormKind)}
                >
                  {RULE_FORM_KINDS.map(({ value, label }) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <span className="text-caption-2 text-text-muted">{kindHint}</span>
              </label>
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <label className="flex flex-col gap-1">
                  <span className="text-caption text-text-muted">标的（6 位代码）</span>
                  <Input
                    value={ruleSymbol}
                    onChange={(event) => setRuleSymbol(event.target.value)}
                    placeholder="600000"
                  />
                </label>
                {needsThreshold ? (
                  <label className="flex flex-col gap-1">
                    <span className="text-caption text-text-muted">{thresholdLabel}</span>
                    <Input
                      value={ruleThreshold}
                      onChange={(event) => setRuleThreshold(event.target.value)}
                      placeholder={ruleKind.startsWith('pnl') ? '5' : '10.2'}
                    />
                  </label>
                ) : null}
                {needsWindow ? (
                  <label className="flex flex-col gap-1">
                    <span className="text-caption text-text-muted">
                      {ruleKind === 'volume_spike' ? '均量窗口（日）' : '均线天数'}
                    </span>
                    <Input
                      value={ruleWindow}
                      onChange={(event) => setRuleWindow(event.target.value)}
                      placeholder="20"
                    />
                  </label>
                ) : null}
                <label className="flex flex-col gap-1">
                  <span className="text-caption text-text-muted">冷却（分钟）</span>
                  <Input
                    value={ruleCooldown}
                    onChange={(event) => setRuleCooldown(event.target.value)}
                    placeholder="60"
                  />
                </label>
              </div>
              <label className="flex flex-col gap-1">
                <span className="text-caption text-text-muted">备注（随提醒正文带出，可空）</span>
                <Input
                  value={ruleNote}
                  onChange={(event) => setRuleNote(event.target.value)}
                  placeholder="止损线"
                />
              </label>
              {channels.length > 0 ? (
                <div className="flex flex-col gap-1">
                  <span className="text-caption text-text-muted">
                    命中后投递（不选 = 只落收件箱）
                  </span>
                  <div className="flex flex-wrap gap-2">
                    {channels
                      .filter((channel) => channel.enabled)
                      .map((channel) => {
                        const checked = ruleChannels.includes(channel.id)
                        return (
                          <label
                            key={channel.id}
                            className={cn(
                              'cursor-pointer rounded-full border px-3 py-1 text-caption',
                              checked
                                ? 'border-action bg-action-soft text-action'
                                : 'border-line text-text-muted',
                            )}
                          >
                            <input
                              type="checkbox"
                              className="mr-1"
                              checked={checked}
                              onChange={(event) => {
                                setRuleChannels((prev) =>
                                  event.target.checked
                                    ? [...prev, channel.id]
                                    : prev.filter((id) => id !== channel.id),
                                )
                              }}
                            />
                            {channel.name || channel.id}
                          </label>
                        )
                      })}
                  </div>
                </div>
              ) : (
                <p className="text-caption text-text-muted">
                  还没有外部渠道（到「渠道」标签页可配钉钉 / 飞书 / 企微 / 邮件 / Webhook）。
                </p>
              )}
              <div>
                <Button onClick={submitRule} disabled={busy}>
                  <Plus size={14} />
                  添加规则
                </Button>
              </div>
            </div>
          </SectionCard>
        </div>
      ) : null}

      {tab === 'channels' ? (
        <div className="mt-4 flex flex-col gap-4">
          <SectionCard title={`渠道（${channels.length} 个）`}>
            {data.status === 'error' ? (
              <ErrorBlock title="渠道读取失败" detail={loadError} onRetry={reload} />
            ) : channels.length === 0 ? (
              <EmptyState
                icon={<Send size={20} />}
                title="还没有出站渠道"
                description="收件箱始终可用；配好渠道后提醒可顺手推到钉钉 / 飞书 / 企微 / 邮箱 / Webhook。"
              />
            ) : (
              channels.map((channel) => {
                const Icon = CHANNEL_ICONS[channel.kind] ?? Puzzle
                const url = typeof channel.config.url === 'string' ? channel.config.url : null
                const mailbox = Array.isArray(channel.config.to_addrs)
                  ? (channel.config.to_addrs as unknown[]).join('、')
                  : null
                const result = testResults[channel.id] ?? null
                return (
                  <div key={channel.id} className="border-b border-line py-3 last:border-b-0">
                    <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-4">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <Icon size={15} className="text-text-muted" />
                          <span className="text-body font-medium text-text">
                            {channel.name || channel.id}
                          </span>
                          <Badge tone={channel.enabled ? 'ok' : 'neutral'}>
                            {CHANNEL_KIND_LABELS[channel.kind] ?? channel.kind}
                          </Badge>
                        </div>
                        <div className="mt-1 truncate text-caption text-text-muted">
                          {url ?? mailbox ?? channel.id}
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <Button
                          size="md"
                          variant="secondary"
                          onClick={() => onTestChannel(channel)}
                          disabled={testingChannelId !== null}
                        >
                          {testingChannelId === channel.id ? (
                            <Loader2 size={14} className="animate-spin" />
                          ) : (
                            <Send size={14} />
                          )}
                          测试
                        </Button>
                        <Button
                          size="md"
                          variant="danger"
                          onClick={() => onDeleteChannel(channel)}
                          disabled={testingChannelId !== null}
                        >
                          <Trash2 size={14} />
                          删除
                        </Button>
                      </div>
                    </div>
                    {result ? (
                      <p
                        className={cn(
                          'mt-1 text-caption',
                          result.ok ? 'text-ok' : 'text-danger',
                        )}
                      >
                        {result.ok ? '已投递：' : '投递失败：'}
                        {result.detail}
                      </p>
                    ) : null}
                  </div>
                )
              })
            )}
          </SectionCard>

          <SectionCard title="新建渠道">
            <div className="flex flex-col gap-3">
              <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <label className="flex flex-col gap-1">
                  <span className="text-caption text-text-muted">类型</span>
                  <select
                    className="input"
                    value={channelKind}
                    onChange={(event) => setChannelKind(event.target.value)}
                  >
                    {CHANNEL_FORM_KINDS.map(({ value, label }) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-caption text-text-muted">id（小写字母/数字/-/_）</span>
                  <Input
                    value={channelId}
                    onChange={(event) => setChannelId(event.target.value)}
                    placeholder="ding-main"
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className="text-caption text-text-muted">显示名（可空）</span>
                  <Input
                    value={channelName}
                    onChange={(event) => setChannelName(event.target.value)}
                    placeholder="钉钉主群"
                  />
                </label>
              </div>
              {channelKind === 'email' ? (
                <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
                  <label className="flex flex-col gap-1">
                    <span className="text-caption text-text-muted">SMTP 服务器</span>
                    <Input
                      value={mailHost}
                      onChange={(event) => setMailHost(event.target.value)}
                      placeholder="smtp.example.com"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-caption text-text-muted">端口（465 SSL / 587 STARTTLS）</span>
                    <Input
                      value={mailPort}
                      onChange={(event) => setMailPort(event.target.value)}
                      placeholder="465"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-caption text-text-muted">发件人</span>
                    <Input
                      value={mailFrom}
                      onChange={(event) => setMailFrom(event.target.value)}
                      placeholder="board@example.com"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-caption text-text-muted">收件人（可多个）</span>
                    <Input
                      value={mailTo}
                      onChange={(event) => setMailTo(event.target.value)}
                      placeholder="me@example.com"
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="text-caption text-text-muted">SMTP 用户名（可空）</span>
                    <Input
                      value={mailUser}
                      onChange={(event) => setMailUser(event.target.value)}
                      placeholder="board@example.com"
                    />
                  </label>
                  <p className="text-caption text-text-muted md:col-span-2">
                    SMTP 密码不进表单：由环境变量提供（变量名可在后端渠道配置里指定），值只进环境。
                  </p>
                </div>
              ) : (
                <label className="flex flex-col gap-1">
                  <span className="text-caption text-text-muted">
                    {channelKind === 'webhook' ? 'Webhook URL' : '机器人 Webhook URL'}
                  </span>
                  <Input
                    value={channelUrl}
                    onChange={(event) => setChannelUrl(event.target.value)}
                    placeholder={
                      channelKind === 'dingtalk'
                        ? 'https://oapi.dingtalk.com/robot/send?access_token=…'
                        : channelKind === 'feishu'
                          ? 'https://open.feishu.cn/open-apis/bot/v2/hook/…'
                          : 'https://example.com/hook'
                    }
                  />
                  <span className="text-caption-2 text-text-muted">
                    URL 只落本机库、页面回显永远打码。
                  </span>
                </label>
              )}
              <div>
                <Button onClick={submitChannel} disabled={busy}>
                  <Plus size={14} />
                  添加渠道
                </Button>
              </div>
            </div>
          </SectionCard>
        </div>
      ) : null}

      {tab === 'history' ? (
        <div className="mt-4">
          <SectionCard title={`最近触发（${history.length} 条）`}>
            {history.length === 0 ? (
              <EmptyState
                icon={<RefreshCw size={20} />}
                title="还没有触发记录"
                description="规则命中（或手动跑一轮触发）后，这里会留下带投递结果的记录。"
              />
            ) : (
              (history as HistoryItem[]).map((item) => (
                <div key={item.id} className="border-b border-line py-3 last:border-b-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-body font-medium text-text">{item.title}</span>
                    <span className="text-caption text-text-muted">
                      {item.fired_at || MISSING}
                    </span>
                  </div>
                  <p className="mt-1 whitespace-pre-line text-caption text-text-muted">
                    {item.body}
                  </p>
                  {item.deliveries.length > 0 ? (
                    <p className="mt-1 text-caption text-text-muted">
                      投递：
                      {item.deliveries
                        .map(
                          (delivery) =>
                            `${delivery.channel}${delivery.ok ? ' ✓' : ` ✗ ${delivery.detail ?? ''}`}`,
                        )
                        .join('；')}
                    </p>
                  ) : null}
                </div>
              ))
            )}
          </SectionCard>
        </div>
      ) : null}
    </div>
  )
}
