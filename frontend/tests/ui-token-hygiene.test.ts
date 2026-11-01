import { readFileSync, readdirSync, statSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

/**
 * QUOT-310 验收 2：`components/ui/**` 里不允许出现硬编码 hex 与 arbitrary-value 类，
 * 过渡一律挂在 token 档上。刻度不够就去 `tokens.json` / `tokens.css` 加，不在组件里写死。
 *
 * 这条检查是「同源」的守门人：屏幕子单（QUOT-311/312/313）之后往组件里加东西时，
 * 跑 `npm test` 就会被打回来，不靠 review 时肉眼盯。
 */

const UI_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'src', 'components', 'ui')

function* walk(dir: string): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const full = path.join(dir, entry)
    if (statSync(full).isDirectory()) yield* walk(full)
    else if (/\.tsx?$/.test(entry)) yield full
  }
}

/** 注释里允许出现 hex（说明某个色值长什么样），所以先把注释剥掉再扫。 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|\s)\/\/[^\n]*/g, '$1')
}

/** 只扫引号里的字符串字面量：`columns[0]` 之类的下标表达式不是样式。 */
function literals(source: string): string[] {
  return source.match(/'((?:[^'\\]|\\.)*)'|`((?:[^`\\]|\\.)*)`/g) ?? []
}

/** 文件内的 §7.3 例外：抽屉 / 弹窗 / Toast 用 duration-base。 */
const BASE_TIER_FILES = new Set(['Overlay.tsx', 'Feedback.tsx'])

/** 任意值写法：`w-[3px]` / `z-[1]` / `grid-cols-[200px_1fr]`（前面一定贴着 `-`、`:` 或 `)`） */
const ARBITRARY_VALUE = /[-:)\]]\s*\[/
const HEX = /#[0-9a-fA-F]{3,8}\b/

const scanned = [...walk(UI_DIR)].map((file) => {
  const source = stripComments(readFileSync(file, 'utf8'))
  return {
    name: path.basename(file),
    source,
    values: literals(source),
    transitions: literals(source).filter((item) => item.includes('transition')),
  }
})

const transitionCount = scanned.reduce((total, item) => total + item.transitions.length, 0)

describe('组件层 token 纪律（QUOT-310 验收 2）', () => {
  it('扫描器真的读到了内容（规则不是空跑）', () => {
    expect(scanned.length).toBeGreaterThanOrEqual(11)
    expect(scanned.reduce((total, item) => total + item.values.length, 0)).toBeGreaterThan(200)
    expect(transitionCount).toBeGreaterThan(10)
  })

  it('没有硬编码 hex', () => {
    for (const file of scanned) {
      for (const literal of file.values) {
        expect(literal, `${file.name} 写了 hex：${literal}`).not.toMatch(HEX)
      }
    }
  })

  it('没有 arbitrary-value 类（刻度不够就加 token）', () => {
    for (const file of scanned) {
      for (const literal of file.values) {
        expect(literal, `${file.name} 写了任意值：${literal}`).not.toMatch(ARBITRARY_VALUE)
      }
    }
  })

  it('过渡一律带 duration 档位与 ease-ldst，不出现裸 transition', () => {
    for (const file of scanned) {
      for (const literal of file.transitions) {
        expect(literal, `${file.name} 的过渡缺 duration：${literal}`).toMatch(/duration-(fast|base)/)
        expect(literal, `${file.name} 的过渡缺 ease-ldst：${literal}`).toContain('ease-ldst')
      }
    }
  })

  it('duration-base 只出现在抽屉 / 弹窗 / Toast（§7.3 唯一例外）', () => {
    for (const file of scanned) {
      if (BASE_TIER_FILES.has(file.name)) continue
      const hits = file.values.filter((literal) => literal.includes('duration-base'))
      expect(hits, `${file.name} 的类名里用了 duration-base，请改回 duration-fast`).toEqual([])
    }
  })

  it('例外文件确实在用 duration-base（例外不是摆设）', () => {
    for (const file of scanned.filter((item) => BASE_TIER_FILES.has(item.name))) {
      expect(file.values.some((literal) => literal.includes('duration-base'))).toBe(true)
    }
  })
})
