// @vitest-environment jsdom
import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'

import {
  FRESHNESS_LABEL,
  FreshnessBadge,
  SOURCE_SEVERITY,
  SOURCE_STATUS_LABEL,
  SourceBadges,
  type SourceBadgeMeta,
  SourceStatusBadge,
  worstSourceTone,
} from '../src/components/ui'
import type { LadderEnvelope } from '../src/api'

afterEach(cleanup)

/** 徽标只读 envelope 的公共字段，payload 类型不影响可传性。 */
const envelope: LadderEnvelope<{ items: number }> = {
  domain: 'review',
  topic: 'sentiment',
  event_id: null,
  data_as_of: '2026-10-30T15:00:00+08:00',
  payload: { items: 3 },
  source: 'ladderstock',
  source_detail: 'TiData 主工具',
  fetched_at: '2026-10-30T15:00:04+08:00',
  freshness: 'cached',
  confidence: 'high',
  source_status: 'degraded',
}

/** 取徽标外层 `<span>`（Badge 的根）：Badge 把 children 包在 `.ldst-num` 里，所以要从文字节点上跳一层。 */
const badgeOf = (name: string): HTMLElement => {
  const node = screen.getByText(name).parentElement
  if (!node) throw new Error(`没有找到「${name}」这枚徽标`)
  return node
}

describe('SourceBadges · §5.1 freshness 三态', () => {
  it('标签 / 图形 / 色调三列都对得上规范', () => {
    render(
      <div>
        <FreshnessBadge freshness="realtime" />
        <FreshnessBadge freshness="cached" />
        <FreshnessBadge freshness="archive" />
      </div>,
    )
    expect(badgeOf('实时').classList).toContain('text-info')
    expect(badgeOf('缓存').classList).toContain('text-warn')
    expect(badgeOf('存档').classList).toContain('text-flat')
    // 图形是颜色之外的第二条通道，且对读屏隐藏（文案已经说了状态）
    // 图形是颜色之外的第二条通道，且对读屏隐藏（文案已经说了状态）
    expect(screen.getByText('●').getAttribute('aria-hidden')).toBe('true')
    expect(screen.getByText('◐')).toBeTruthy()
    expect(screen.getAllByText('○')).toHaveLength(1)
  })

  it('悬浮说明带数据时间，as_of 缺失时写「未知」而不是留空', () => {
    render(<FreshnessBadge freshness="cached" asOf="2026-10-30T15:00:00+08:00" sourceDetail="TiData" />)
    const title = badgeOf('缓存').getAttribute('title') ?? ''
    expect(title).toContain('本地缓存回放，非实时')
    expect(title).toContain('TiData')
    cleanup()
    render(<FreshnessBadge freshness="realtime" asOf={null} />)
    expect(badgeOf('实时').getAttribute('title')).toContain('数据时间 未知')
  })

  it('后端 key 不在枚举里（radar 的 freshness: string）→ 不直显英文，退到「未知」并把原值放悬浮', () => {
    render(<FreshnessBadge freshness="daily" />)
    expect(screen.getByText('未知')).toBeTruthy()
    expect(screen.queryByText('daily')).toBeNull()
    expect(badgeOf('未知').getAttribute('title')).toContain('未登记的 freshness：daily')
  })
})

describe('SourceBadges · §5.2 source_status 三态', () => {
  it('degraded 是 warn、partial 是 danger —— 屏侧旧实现把这两条写反了', () => {
    render(
      <div>
        <SourceStatusBadge status="ok" />
        <SourceStatusBadge status="degraded" />
        <SourceStatusBadge status="partial" />
      </div>,
    )
    expect(badgeOf('数据完整').classList).toContain('text-ok')
    expect(badgeOf('已降级').classList).toContain('text-warn')
    expect(badgeOf('部分缺失').classList).toContain('text-danger')
  })

  it('三态都有文字标签，颜色不是唯一编码（§9）', () => {
    render(<SourceStatusBadge status="partial" detail="缺 3 只个股的封单" />)
    expect(screen.getByText('部分缺失')).toBeTruthy()
    const title = badgeOf('部分缺失').getAttribute('title') ?? ''
    expect(title).toContain('必须列出缺失项')
    expect(title).toContain('缺 3 只个股的封单')
  })

  it('标签表是本模块唯一真源：archive 是「存档」，三份屏侧旧映射的「归档」不再出现', () => {
    expect(FRESHNESS_LABEL).toEqual({ realtime: '实时', cached: '缓存', archive: '存档' })
    expect(SOURCE_STATUS_LABEL).toEqual({ ok: '数据完整', degraded: '已降级', partial: '部分缺失' })
  })
})

describe('SourceBadges · §5.3 组合', () => {
  it('两枚并排，不合并成一枚「健康度」', () => {
    render(<SourceBadges envelope={envelope} />)
    expect(screen.getByText('缓存')).toBeTruthy()
    expect(screen.getByText('已降级')).toBeTruthy()
    expect(screen.queryByText(/缓存·/)).toBeNull()
    expect(screen.queryByText(/·已降级/)).toBeNull()
  })

  it('envelope 为空不占位（屏侧取数未完成时不该出现空壳）', () => {
    const { container } = render(<SourceBadges envelope={null} />)
    expect(container.firstChild).toBeNull()
  })

  it('严重度排序一次定死：partial > degraded > cached > realtime', () => {
    expect(SOURCE_SEVERITY).toEqual(['partial', 'degraded', 'cached', 'realtime'])
    // 完整性更严重时取完整性
    expect(worstSourceTone('realtime', 'partial')).toBe('danger')
    expect(worstSourceTone('cached', 'degraded')).toBe('warn')
    // 新鲜度更严重时取新鲜度
    expect(worstSourceTone('cached', 'ok')).toBe('warn')
    expect(worstSourceTone('realtime', 'ok')).toBe('info')
    // 都不在排序里（又新又全 / 存档且完整）：退回完整性本身
    expect(worstSourceTone('archive', 'ok')).toBe('ok')
  })

  it('类型级：任何 payload 的 LadderEnvelope 都能直接喂给 envelope prop', () => {
    // SourceBadgeMeta 就是 Pick<LadderEnvelope<unknown>, …>，这里让 TS 编译器当场验一次
    const meta: SourceBadgeMeta = envelope
    render(<SourceBadges envelope={meta} />)
    expect(screen.getByText('已降级')).toBeTruthy()
  })
})

describe('SourceBadges · 组件层只有一份 label map', () => {
  const UI_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'src', 'components', 'ui')

  function* walk(dir: string): Generator<string> {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry)
      if (statSync(full).isDirectory()) yield* walk(full)
      else if (/\.tsx?$/.test(entry)) yield full
    }
  }

  it('FRESHNESS_LABEL / SOURCE_STATUS_LABEL 的定义只出现在 SourceBadges.tsx', () => {
    const owners = [...walk(UI_DIR)]
      .filter((file) => /const (FRESHNESS_LABEL|SOURCE_STATUS_LABEL)\b/.test(readFileSync(file, 'utf8')))
      .map((file) => path.basename(file))
    expect(owners).toEqual(['SourceBadges.tsx'])
  })
})
