// @vitest-environment jsdom
import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { cleanup, render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it } from 'vitest'

import PoolScreen from '../src/screens/radar/PoolScreen'

/**
 * QUOT-464：单顶栏收敛（DEC-063）后，「重算候选池」等页面命令已从页内搬进顶栏，
 * 空态引导不能再写「页内顶部」——新用户按那句话找不到入口。
 */

const SRC_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'src')

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    if (statSync(full).isDirectory()) yield* walk(full)
    else if (/\.tsx?$/.test(entry)) yield full
  }
}

afterEach(cleanup)

describe('空态引导指向顶栏入口（QUOT-464）', () => {
  it('候选池空态写「本页命令」与「重算候选池」，不写「页内顶部」', () => {
    render(
      <MemoryRouter initialEntries={['/pool?demo=1&state=empty']}>
        <Routes>
          <Route path="/pool" element={<PoolScreen />} />
        </Routes>
      </MemoryRouter>,
    )

    const text = document.body.textContent ?? ''
    expect(text).toContain('本页命令')
    expect(text).toContain('重算候选池')
    expect(text).not.toContain('页内顶部')
  })

  it('源码里没有残留的「页内顶部」引导', () => {
    const hits = [...walk(SRC_DIR)]
      .filter((file) => readFileSync(file, 'utf8').includes('页内顶部'))
      .map((file) => path.relative(SRC_DIR, file))
    expect(hits).toEqual([])
  })
})
