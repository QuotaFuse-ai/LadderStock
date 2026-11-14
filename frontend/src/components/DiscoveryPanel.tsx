import { useState } from 'react'
import { CheckCircle2, RefreshCw, Wrench } from 'lucide-react'

import {
  api,
  describeApiError,
  type DiscoveryCandidate,
  type DiscoveryPayload,
} from '../api'
import { Badge, Banner, Button, SectionCard, SkeletonText, Switch } from './ui'

/**
 * 零配置 AI 自动发现链（QUOT-601）：按 P1 网关 → P2 环境变量 → P3 登录态
 * 逐级探测，命中即一键落地；用户全程不填 key。设置页展示「当前通道来源」
 * 与探测延迟；P3 读别家登录态默认关闭，开启需显式开关。
 */

const SOURCE_BADGE: Record<string, { tone: 'ok' | 'warn' | 'neutral'; text: string }> = {
  workbuddy_gateway: { tone: 'ok', text: 'workbuddy网关' },
  env: { tone: 'ok', text: '环境变量' },
  codex: { tone: 'ok', text: 'Codex' },
  claude: { tone: 'ok', text: 'Claude' },
  manual: { tone: 'neutral', text: '手填' },
  none: { tone: 'warn', text: '未配置' },
}

export default function DiscoveryPanel({ onApplied }: { onApplied?: () => void }) {
  const [payload, setPayload] = useState<DiscoveryPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [busySource, setBusySource] = useState<string | null>(null)
  const [repairing, setRepairing] = useState(false)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'danger'; text: string } | null>(null)

  const load = async (): Promise<void> => {
    setLoading(true)
    setNotice(null)
    try {
      setPayload(await api.control.selfAiDiscover())
    } catch (error) {
      setNotice({ tone: 'danger', text: `探测失败：${describeApiError(error).detail}` })
    } finally {
      setLoading(false)
    }
  }

  const apply = async (source: string): Promise<void> => {
    setBusySource(source)
    setNotice(null)
    try {
      const result = await api.control.applyDiscovery(source)
      if (result.applied) {
        setNotice({ tone: 'ok', text: `已接入「${source}」通道，下次复盘即生效` })
        onApplied?.()
      } else {
        setNotice({ tone: 'danger', text: result.reason || '该来源暂不支持自动接入' })
      }
    } catch (error) {
      setNotice({ tone: 'danger', text: `接入失败：${describeApiError(error).detail}` })
    } finally {
      setBusySource(null)
    }
  }

  const repair = async (): Promise<void> => {
    setRepairing(true)
    setNotice(null)
    try {
      const result = await api.control.repairDiscovery()
      setNotice({
        tone: result.ok ? 'ok' : 'danger',
        text: result.ok ? `本地 AI 服务已恢复（${result.latency_ms}ms）` : result.detail,
      })
      await load()
    } catch (error) {
      setNotice({ tone: 'danger', text: `修复失败：${describeApiError(error).detail}` })
    } finally {
      setRepairing(false)
    }
  }

  const toggleCliLogins = async (next: boolean): Promise<void> => {
    try {
      await api.control.saveSelfAi(
        { LDST_REUSE_CLI_LOGINS: next ? '1' : '' },
        next ? [] : ['LDST_REUSE_CLI_LOGINS'],
      )
      await load()
    } catch (error) {
      setNotice({ tone: 'danger', text: `更新开关失败：${describeApiError(error).detail}` })
    }
  }

  const current = payload ? SOURCE_BADGE[payload.current_source] || SOURCE_BADGE.none : null
  const gateway = payload?.candidates.find((item) => item.source === 'workbuddy_gateway')

  return (
    <SectionCard
      title="零配置 AI"
      subtitle="自动发现本机可复用的 AI 通道，命中即可用，全程无需填 key"
      actions={
        <Button variant="secondary" onClick={() => void load()} loading={loading}>
          <RefreshCw size={14} /> 重新探测
        </Button>
      }
    >
      {loading && !payload && <SkeletonText lines={3} />}
      {payload && current && (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-body text-text-secondary">当前通道来源</span>
            <Badge tone={current.tone}>{current.text}</Badge>
            {gateway?.ok && (
              <span className="text-caption text-text-muted">
                探测延迟 <span className="ldst-num">{gateway.latency_ms}ms</span>
              </span>
            )}
          </div>

          {!payload.ok && gateway && !gateway.ok && (
            <Banner
              tone="warn"
              title="本地 AI 服务未启动"
              action={
                <Button variant="secondary" onClick={() => void repair()} loading={repairing}>
                  <Wrench size={14} /> 点击修复
                </Button>
              }
            >
              {gateway.detail}
            </Banner>
          )}

          {notice && <Banner tone={notice.tone === 'ok' ? 'ok' : 'danger'}>{notice.text}</Banner>}

          <div className="flex flex-col gap-2">
            {payload.candidates.map((item: DiscoveryCandidate) => (
              <div
                key={item.source}
                className="flex flex-wrap items-center gap-2 rounded-lg border border-line px-3 py-2"
              >
                <Badge tone={item.ok ? 'ok' : 'warn'}>{item.label}</Badge>
                <span className="text-caption text-text-muted">
                  {item.ok
                    ? `可用${item.latency_ms !== null ? ` · ${item.latency_ms}ms` : ''} · ${item.base_url}`
                    : item.detail}
                </span>
                {item.ok && payload.current_source !== item.source && (
                  <Button
                    variant="secondary"
                    className="ml-auto"
                    loading={busySource === item.source}
                    onClick={() => void apply(item.source)}
                  >
                    使用此通道
                  </Button>
                )}
                {item.ok && payload.current_source === item.source && (
                  <span className="ml-auto flex items-center gap-1 text-caption text-text-muted">
                    <CheckCircle2 size={14} /> 生效中
                  </span>
                )}
              </div>
            ))}
          </div>

          <div className="flex items-center gap-3 border-t border-line pt-3">
            <Switch
              checked={payload.include_cli_logins}
              label="复用本机 Codex / Claude 登录态"
              onChange={(next) => {
                if (!next || window.confirm('允许读取本机 Codex / Claude 的登录态密钥？密钥只在本机流转，不上传。')) {
                  void toggleCliLogins(next)
                }
              }}
            />
            <span className="text-caption text-text-secondary">
              默认关闭；关闭时完全不读取这两个目录
            </span>
          </div>
        </div>
      )}
    </SectionCard>
  )
}
