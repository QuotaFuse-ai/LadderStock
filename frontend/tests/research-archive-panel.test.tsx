// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import ResearchArchivePanel from '../src/components/research/ResearchArchivePanel'
import type { ArchiveItem } from '../src/api/archive'

/**
 * F8 研究档案面板：
 * 1. 空目录 → 「还没有研究档案」空态，不伪造行；
 * 2. 有档案 → 行内体检如实写缺段/未核实，点击行可打开对应研究页；
 * 3. 检索命中 → 回原文片段与所在段名。
 */

const { loadArchiveList, searchArchive, exportArchive } = vi.hoisted(() => ({
  loadArchiveList: vi.fn(),
  searchArchive: vi.fn(),
  exportArchive: vi.fn(),
}))

vi.mock('../src/api/archive', () => ({ loadArchiveList, searchArchive, exportArchive }))

function item(overrides: Partial<ArchiveItem> = {}): ArchiveItem {
  return {
    code: '600202',
    date: '2026-11-18',
    stageCount: 1,
    totalStages: 6,
    complete: false,
    missing: ['financial', 'forecast', 'valuation', 'risk', 'report'],
    degraded: [],
    unverified: ['company'],
    failed: [],
    updatedAt: '2026-11-18T19:40:03',
    stages: [],
    matches: [],
    matchedBy: null,
    ...overrides,
  }
}

function renderPanel() {
  return render(
    <MemoryRouter initialEntries={['/research']}>
      <ResearchArchivePanel />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  exportArchive.mockResolvedValue({
    ok: true,
    code: '600202',
    date: '2026-11-18',
    path: 'D:\\data\\research_archive\\2026-11-18\\600202.md',
    bytes: 2636,
    complete: false,
    missing: ['financial'],
  })
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
})

describe('ResearchArchivePanel', () => {
  it('没有档案时给空态', async () => {
    loadArchiveList.mockResolvedValue([])
    renderPanel()
    expect(await screen.findByText('还没有研究档案')).toBeTruthy()
    expect(screen.queryByText('600202')).toBeNull()
  })

  it('有档案时如实写体检（缺段 / 未核实）', async () => {
    loadArchiveList.mockResolvedValue([item()])
    renderPanel()
    expect(await screen.findByText('600202')).toBeTruthy()
    expect(screen.getByText(/1\/6 段 · 缺 financial/)).toBeTruthy()
    expect(screen.getByText(/未核实 1/)).toBeTruthy()
    expect(screen.getByText('共 1 份档案')).toBeTruthy()
  })

  it('检索命中时回原文片段与所在段', async () => {
    loadArchiveList.mockResolvedValue([])
    searchArchive.mockResolvedValue([
      item({
        matchedBy: 'content',
        matches: [{ stage: 'company', stageTitle: '公司画像', text: '主营业务与产业链位置：快照未提供…' }],
      }),
    ])
    renderPanel()
    const input = await screen.findByLabelText('检索研究档案')
    fireEvent.change(input, { target: { value: '主营业务' } })
    fireEvent.click(screen.getByRole('button', { name: '检索' }))
    expect(await screen.findByText(/公司画像 — 主营业务与产业链位置/)).toBeTruthy()
    expect(screen.getByText('检索「主营业务」命中 1 份')).toBeTruthy()
  })

  it('导出按钮写盘后提示落点', async () => {
    loadArchiveList.mockResolvedValue([item()])
    renderPanel()
    await screen.findByText('600202')
    fireEvent.click(screen.getByRole('button', { name: '导出' }))
    await waitFor(() => expect(screen.getByText('导出完成')).toBeTruthy())
    expect(screen.getByText(/600202\.md/)).toBeTruthy()
  })
})
