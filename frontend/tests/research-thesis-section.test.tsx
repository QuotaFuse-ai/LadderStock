// @vitest-environment jsdom
/**
 * 我的论点与判断（QUOT-452）界面走查：
 * 空态引导 → 校验失败 → 新建 → 到期未判高亮 → 判定留痕 → 编辑 → 删除二次确认。
 *
 * 断言的是用户能看见的结论；持久化细节在 `thesis-notes-store.test.ts`。
 */

import 'fake-indexeddb/auto'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { ThesisSection } from '../src/components/research/sections/ThesisSection'
import { KV_STORE, closeStateDatabase, idbDelete } from '../persist/indexeddb'
import { useThesisNotes } from '../stores/thesisNotes'

const PAST_DUE = '2020-01-31'

async function waitFlush(ms = 60): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

function resetMemory(): void {
  useThesisNotes.setState({ records: [], updatedAt: null })
}

function renderSection(code: string | null = '600000', name: string | null = '浦发银行') {
  return render(<ThesisSection code={code} name={name} />)
}

async function fillNewThesis(options: { title?: string; condition?: string; due?: string } = {}) {
  fireEvent.click(screen.getByRole('button', { name: /写论点/ }))
  if (options.title !== undefined) {
    fireEvent.change(screen.getByLabelText(/^标题/), { target: { value: options.title } })
  }
  fireEvent.change(screen.getByLabelText('正文'), { target: { value: '行业扩产周期见顶。' } })
  fireEvent.change(screen.getByLabelText('建仓或观察理由'), {
    target: { value: '只观察，等下一份财报。' },
  })
  if (options.condition !== undefined) {
    fireEvent.change(screen.getByLabelText(/^判定条件/), { target: { value: options.condition } })
  }
  if (options.due !== undefined) {
    fireEvent.change(screen.getByLabelText(/^到期日/), { target: { value: options.due } })
  }
}

async function createThesis(): Promise<void> {
  await fillNewThesis({
    title: '产能护城河，绕不开',
    condition: '下一季毛利率 ≥ 30% 算成立，否则不成立',
    due: PAST_DUE,
  })
  fireEvent.click(screen.getByRole('button', { name: '保存论点' }))
  await screen.findByRole('heading', { name: '产能护城河，绕不开' })
}

beforeEach(async () => {
  resetMemory()
  await waitFlush()
  await closeStateDatabase()
  await idbDelete(KV_STORE, 'thesis_notes')
})

afterEach(() => {
  cleanup()
})

describe('空态与引导', () => {
  it('没有记录时给出论点怎么用、裁决点为什么必须写', () => {
    renderSection()

    expect(screen.getByText(/还没为浦发银行写下论点/)).toBeTruthy()
    expect(screen.getByText(/以后每次打开就知道该回来核什么/)).toBeTruthy()
    expect(screen.getByText(/裁决点必须写明判定条件与到期日/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '写第一条论点' })).toBeTruthy()
    // 不展示任何示例论点，也不假装有记录
    expect(screen.getByText(/只保存在本机 IndexedDB（thesis_notes）/)).toBeTruthy()
  })

  it('没有标的时只提示先选标的，不渲染新建入口', () => {
    renderSection(null, null)
    expect(screen.getByText('先选择一只标的')).toBeTruthy()
    expect(screen.queryByRole('button', { name: /写论点/ })).toBeNull()
  })
})

describe('保存校验', () => {
  it('标题、判定条件、到期日为空都拦住，且不写入分片', async () => {
    renderSection()
    await fillNewThesis({ title: '', condition: '', due: '' })
    fireEvent.click(screen.getByRole('button', { name: '保存论点' }))

    const alerts = screen.getAllByRole('alert').map((node) => node.textContent ?? '')
    expect(alerts.join('\n')).toContain('论点需要一个标题')
    expect(alerts.join('\n')).toContain('裁决点必须写清判定条件')
    expect(alerts.join('\n')).toContain('裁决点必须填到期日')
    expect(useThesisNotes.getState().records).toHaveLength(0)
    // 表单留在原地，错误可被读屏读到
    expect(screen.getByRole('button', { name: '保存论点' })).toBeTruthy()
  })

  it('只填标题、不写裁决点仍被拦住（每条论点至少一个裁决点）', async () => {
    renderSection()
    await fillNewThesis({ title: '只有标题', condition: '   ', due: '' })
    fireEvent.click(screen.getByRole('button', { name: '保存论点' }))

    expect(useThesisNotes.getState().records).toHaveLength(0)
    expect(screen.getAllByRole('alert').map((node) => node.textContent).join()).toContain(
      '裁决点必须写清判定条件',
    )
  })
})

describe('新建 → 到期未判 → 判定 → 回看留痕', () => {
  it('保存后记录上屏，并高亮已到期未判', async () => {
    renderSection()
    await createThesis()

    expect(screen.getByText('行业扩产周期见顶。')).toBeTruthy()
    expect(screen.getByText(/只观察，等下一份财报。/)).toBeTruthy()
    expect(screen.getAllByText(/已到期未判/).length).toBeGreaterThan(0)
    expect(screen.getByText(/逾期 \d+ 天/)).toBeTruthy()
    expect(screen.getByText('待判')).toBeTruthy()
  })

  it('判定后显示结论、判定时间与留痕，可展开回看', async () => {
    renderSection()
    await createThesis()

    fireEvent.click(screen.getByRole('button', { name: '判定' }))
    fireEvent.change(screen.getByLabelText(/^判定结论/), { target: { value: 'met' } })
    fireEvent.change(screen.getByLabelText('判定说明（可留空）'), {
      target: { value: '毛利率 31.2%，达标' },
    })
    fireEvent.click(screen.getByRole('button', { name: '记录判定' }))

    await waitFor(() => expect(screen.getByText('成立')).toBeTruthy())
    expect(screen.getByText(/最近判定于 \d{4}-\d{2}-\d{2} \d{2}:\d{2}/)).toBeTruthy()
    expect(screen.getByText(/最近判定于 .*毛利率 31\.2%，达标/)).toBeTruthy()

    // 判定后不再计入「已到期未判」
    expect(screen.queryByText(/已到期未判/)).toBeNull()

    const history = screen.getByText(/判定留痕（1 次）/).closest('details')
    expect(history).not.toBeNull()
    expect(within(history as HTMLElement).getByText(/毛利率 31\.2%，达标/)).toBeTruthy()

    const point = useThesisNotes.getState().records[0].points[0]
    expect(point.verdict).toBe('met')
    expect(point.judgedAt).toEqual(expect.any(String))
    expect(point.history).toHaveLength(1)
  })

  it('判定不选结论时提示，不写入留痕', async () => {
    renderSection()
    await createThesis()

    fireEvent.click(screen.getByRole('button', { name: '判定' }))
    fireEvent.click(screen.getByRole('button', { name: '记录判定' }))

    expect(screen.getByRole('alert').textContent).toContain('先选判定结论')
    expect(useThesisNotes.getState().records[0].points[0].history).toHaveLength(0)
  })
})

describe('编辑与删除', () => {
  it('编辑标题与正文后立即反映到面板', async () => {
    renderSection()
    await createThesis()

    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    fireEvent.change(screen.getByLabelText(/^标题/), { target: { value: '改过的标题' } })
    fireEvent.change(screen.getByLabelText('正文'), { target: { value: '改过的正文' } })
    fireEvent.click(screen.getByRole('button', { name: '保存修改' }))

    expect(await screen.findByRole('heading', { name: '改过的标题' })).toBeTruthy()
    expect(screen.getByText('改过的正文')).toBeTruthy()
    // 编辑论点不改裁决点
    expect(screen.getByText('下一季毛利率 ≥ 30% 算成立，否则不成立')).toBeTruthy()
  })

  it('删除必须二次确认；取消保留，确认后清空', async () => {
    renderSection()
    await createThesis()

    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    let dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText('删除这条论点？')).toBeTruthy()
    fireEvent.click(within(dialog).getByRole('button', { name: '保留' }))
    expect(screen.getByRole('heading', { name: '产能护城河，绕不开' })).toBeTruthy()

    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    dialog = await screen.findByRole('dialog')
    fireEvent.click(within(dialog).getByRole('button', { name: '删除' }))

    await waitFor(() => expect(useThesisNotes.getState().records).toHaveLength(0))
    expect(screen.getByText(/还没为浦发银行写下论点/)).toBeTruthy()
  })
})

describe('按标的隔离', () => {
  it('别的标的的论点不会出现在本页', async () => {
    renderSection()
    await createThesis()

    cleanup()
    renderSection('600001', '邯郸钢铁')
    expect(screen.queryByRole('heading', { name: '产能护城河，绕不开' })).toBeNull()
    expect(screen.getByText(/还没为邯郸钢铁写下论点/)).toBeTruthy()
  })
})
