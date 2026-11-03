// @vitest-environment jsdom
import 'fake-indexeddb/auto'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render, screen, within } from '@testing-library/react'
import type { ReactNode } from 'react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { DemoStateBadge } from '../src/components/PageShell'
import { HBarList, SourceBadges, SourceStatusBadge, worstSourceTone } from '../src/components/ui'
import { countOrMissing } from '../src/lib/format'
import SettingsScreen from '../src/screens/SettingsScreen'

/**
 * QUOT-320 共享层四件事的回归口径：
 * 1. `?demo=1` 的水马克自己上屏，不再依赖 `?state=`（R-18 根因）；
 * 2. `source_status` 色序按 §5.2 / §5.3，且各屏不得自带实现（§5.5 R-01）；
 * 3. 溯源徽标只有 `components/ui/SourceBadges.tsx` 一份；
 * 4. 缺失值不冒充真数（§8）：`countOrMissing` 与 `HBarList` 的空槽。
 */

afterEach(cleanup)

function at(url: string, node: ReactNode) {
  return render(<MemoryRouter initialEntries={[url]}>{node}</MemoryRouter>)
}

describe('QUOT-320 · ?demo=1 水印根因（§5.6 / R-18）', () => {
  it('只有 ?demo=1、没有 ?state= 时也必须上屏', () => {
    at('/x?demo=1', <DemoStateBadge state={null} />)
    expect(screen.getByText('演示数据 · ?demo=1')).toBeTruthy()
  })

  it('?demo=true 同样算演示源；?demo=0 与不带参数不算', () => {
    at('/x?demo=true', <DemoStateBadge state={null} />)
    expect(screen.getByText('演示数据 · ?demo=1')).toBeTruthy()
    cleanup()
    at('/x?demo=0', <DemoStateBadge state={null} />)
    expect(screen.queryByText(/演示数据/)).toBeNull()
    cleanup()
    at('/x', <DemoStateBadge state={null} />)
    expect(screen.queryByText(/演示数据/)).toBeNull()
  })

  it('旧签名不变：<DemoStateBadge state={forced} /> 的强制态文案原样保留', () => {
    at('/x', <DemoStateBadge state="loading" />)
    expect(screen.getByText('演示态：加载中（?state=loading）')).toBeTruthy()
    expect(screen.queryByText(/演示数据 ·/)).toBeNull()
  })

  it('两个开关同时打开时并排两枚，互不替代（§5.6）', () => {
    at('/x?demo=1&state=partial', <DemoStateBadge state="partial" />)
    expect(screen.getByText('演示数据 · ?demo=1')).toBeTruthy()
    expect(screen.getByText('演示态：部分失败（?state=partial）')).toBeTruthy()
  })

  it('演示徽标是 warn 底色（§5.6 硬约束）', () => {
    at('/x?demo=1', <DemoStateBadge state={null} />)
    const badge = screen.getByText('演示数据 · ?demo=1').parentElement
    expect(badge?.getAttribute('class')).toContain('bg-warn-soft')
  })
})

describe('QUOT-320 · 色序与严重度（§5.2 / §5.3）', () => {
  it('partial 是 danger 橙红、degraded 是 warn 黄 —— 屏侧旧实现写反的那两条', () => {
    render(
      <div>
        <SourceStatusBadge status="degraded" />
        <SourceStatusBadge status="partial" />
      </div>,
    )
    expect(screen.getByText('已降级').parentElement?.getAttribute('class')).toContain('bg-warn-soft')
    expect(screen.getByText('部分缺失').parentElement?.getAttribute('class')).toContain('bg-danger-soft')
  })

  it('契约里的第四档 missing（未取到）不轻于 partial', () => {
    render(<SourceStatusBadge status="missing" />)
    expect(screen.getByText('未取到').parentElement?.getAttribute('class')).toContain('bg-danger-soft')
    expect(worstSourceTone('realtime', 'missing')).toBe('danger')
  })

  it('提示条取色按 §5.3 排序取最严重者', () => {
    expect(worstSourceTone('realtime', 'partial')).toBe('danger')
    expect(worstSourceTone('cached', 'ok')).toBe('warn')
    expect(worstSourceTone('realtime', 'degraded')).toBe('warn')
  })

  it('envelope 徽标只出中文标签，后端 key 不直显（§5.5 R-06）', () => {
    render(
      <SourceBadges
        envelope={{ freshness: 'archive', source_status: 'partial', source_detail: '缺 3 只封单', data_as_of: null }}
      />,
    )
    expect(screen.getByText('存档')).toBeTruthy()
    expect(screen.getByText('部分缺失')).toBeTruthy()
    expect(screen.queryByText('archive')).toBeNull()
    expect(screen.queryByText('partial')).toBeNull()
  })
})

describe('QUOT-320 · §5.5 单一实现（R-01，一处不留）', () => {
  const SRC = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')

  function* walk(dir: string): Generator<string> {
    for (const entry of readdirSync(dir)) {
      const full = path.join(dir, entry)
      if (statSync(full).isDirectory()) yield* walk(full)
      else if (/\.tsx?$/.test(entry)) yield full
    }
  }

  /** 屏侧不许再写第二份：本地 label map、本地徽标本体、被写反的「归档」都算违规。 */
  const FORBIDDEN: Array<{ pattern: RegExp; why: string }> = [
    { pattern: /const FRESHNESS_LABEL\b/, why: 'freshness 标签只准写在 components/ui/SourceBadges.tsx' },
    { pattern: /const SOURCE_LABEL\b/, why: 'source_status 标签只准写在 components/ui/SourceBadges.tsx' },
    { pattern: /function SourceBadge\b/, why: '溯源徽标不得有第二份实现（§5.5 R-01）' },
    { pattern: /archive: '归档'/, why: "§5.1 的 archive 标签是「存档」" },
  ]

  it('src/screens 下没有第二份 label map / SourceBadge 实现', () => {
    const offenders: string[] = []
    for (const file of walk(path.join(SRC, 'screens'))) {
      const text = readFileSync(file, 'utf8')
      for (const rule of FORBIDDEN) {
        if (rule.pattern.test(text)) offenders.push(`${path.relative(SRC, file)} → ${rule.why}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('溯源徽标的 import 只来自 components/ui 桶，不再有屏内相对路径', () => {
    const offenders: string[] = []
    for (const file of walk(SRC)) {
      const text = readFileSync(file, 'utf8')
      if (/from '[^']*sourceBadges'/.test(text)) offenders.push(path.relative(SRC, file))
    }
    expect(offenders).toEqual([])
  })
})

describe('QUOT-320 · 设置页端口离线不冒充', () => {
  it('本地服务未连接：端口列写「—」，不把默认端口当真实读数', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => {
      throw new TypeError('offline')
    }))
    render(
      <MemoryRouter initialEntries={['/settings?tab=ports']}>
        <SettingsScreen />
      </MemoryRouter>,
    )
    const web = await screen.findByText('看板 Web')
    const row = web.closest('tr') ?? web.closest('li')
    expect(row?.textContent).toContain('—')
    expect(row?.textContent).not.toContain('5950')
    expect(screen.queryByText('5950')).toBeNull()
    expect(screen.queryByText('5951')).toBeNull()
    vi.unstubAllGlobals()
  })
})

describe('QUOT-320 · 缺失不冒充真数（§8）', () => {
  it('countOrMissing：没取到一律「—」，0 是真实读数不许变成「—」', () => {
    expect(countOrMissing(null)).toBe('—')
    expect(countOrMissing(undefined)).toBe('—')
    expect(countOrMissing(Number.NaN)).toBe('—')
    expect(countOrMissing(Number.POSITIVE_INFINITY)).toBe('—')
    expect(countOrMissing(0)).toBe('0')
    expect(countOrMissing(37)).toBe('37')
  })

  it('HBarList：value 为 null 的槽位不画实条，读数写「—」且不挂单位', () => {
    const { container } = render(
      <HBarList
        items={[
          { label: '有读数', value: 10 },
          { label: '没取到', value: null },
        ]}
        unit=" 只"
      />,
    )
    const rows = container.querySelectorAll('li')
    expect(rows).toHaveLength(2)
    // 只有第一条里有实心条
    expect(within(rows[0] as HTMLElement).getAllByText('10')).toHaveLength(1)
    expect(within(rows[1] as HTMLElement).getByText('—')).toBeTruthy()
    expect(rows[1].textContent).not.toContain(' 只')
    expect(container.querySelectorAll('li')[1].querySelectorAll('span[style]')).toHaveLength(0)
  })

  it('HBarList：null 不参与最大值计算，其余条的宽度比例不受影响', () => {
    const { container } = render(
      <HBarList items={[{ label: 'a', value: null }, { label: 'b', value: 4 }, { label: 'c', value: 2 }]} />,
    )
    const bars = container.querySelectorAll('span[style]')
    expect(bars).toHaveLength(2)
    expect((bars[0] as HTMLElement).style.width).toBe('100%')
    expect((bars[1] as HTMLElement).style.width).toBe('50%')
  })
})
