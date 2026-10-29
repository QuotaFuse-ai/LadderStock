#!/usr/bin/env node
/**
 * 令牌生成器：docs/tasks/TASK-UI-REDESIGN/tokens.json → 前端两份派生物
 *
 *   1. frontend/tokens.json        构建作用域内的机读副本（tailwind.config.ts 读取）
 *   2. frontend/src/styles/tokens.css  CSS 变量层（全站唯一允许出现 hex 的地方）
 *
 * 单一真源仍是 docs/ 下那份；本脚本只单向推导，`--check` 用于校验是否漂移。
 *
 * 用法：
 *   node scripts/gen-tokens.mjs            # 生成 / 覆盖
 *   node scripts/gen-tokens.mjs --check    # 只比对，不改文件（CI / 交付自查用）
 */

import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = path.dirname(fileURLToPath(import.meta.url))
const FRONTEND = path.resolve(HERE, '..')
const SOURCE = path.resolve(FRONTEND, '..', 'docs', 'tasks', 'TASK-UI-REDESIGN', 'tokens.json')
const OUT_JSON = path.join(FRONTEND, 'tokens.json')
const OUT_CSS = path.join(FRONTEND, 'src', 'styles', 'tokens.css')

const check = process.argv.includes('--check')

const tokens = JSON.parse(fs.readFileSync(SOURCE, 'utf8'))

/** 主题相关分组：这些令牌在 dark / light 两套变量里各写一遍。 */
const THEMED = ['color', 'chart', 'shadow']
/** 主题无关分组：只写一次。 */
const STATIC = ['typography', 'space', 'radius', 'layout', 'motion']

const lines = []
const push = (text = '') => lines.push(text)

function emitGroup(group, theme) {
  for (const [key, token] of Object.entries(tokens[group])) {
    if (!token || typeof token !== 'object' || !token.cssVar) continue
    const value = theme ? token[theme] : token.value
    if (value === undefined || value === null) continue
    const usage = token.usage ? ` /** ${token.usage} */` : ''
    push(`  ${token.cssVar}: ${value};${usage}`)
  }
}

function emitTypography() {
  const t = tokens.typography
  push(`  ${t.sans.cssVar}: ${t.sans.value};`)
  push(`  ${t.mono.cssVar}: ${t.mono.value};`)
  for (const [key, token] of Object.entries(t.size)) {
    push(`  ${token.cssVar}: ${token.value}; /** ${token.usage} */`)
    push(`  --ldst-leading-${key}: ${token.lineHeight};`)
  }
  for (const token of Object.values(t.weight)) push(`  ${token.cssVar}: ${token.value};`)
  for (const token of Object.values(t.lineHeight)) push(`  ${token.cssVar}: ${token.value};`)
  push(`  ${t.numeric.cssVar}: ${t.numeric.font};`)
}

function emitFocus(theme) {
  const ring = tokens.focus.ring
  push(`  ${ring.cssVar}: ${ring[theme]}; /** ${ring.usage} */`)
  push(`  ${tokens.focus.offset.cssVar}: ${tokens.focus.offset.value};`)
}

function emitLayout() {
  for (const token of Object.values(tokens.layout)) {
    if (!token.cssVar) continue
    const usage = token.usage ? ` /** ${token.usage} */` : ''
    push(`  ${token.cssVar}: ${token.value};${usage}`)
  }
}

function emitMotion() {
  for (const [key, token] of Object.entries(tokens.motion)) {
    if (key === 'reducedMotion') continue // 由 media query 覆盖，见下
    const usage = token.usage ? ` /** ${token.usage} */` : ''
    push(`  ${token.cssVar}: ${token.value};${usage}`)
  }
}

const header = `/**
 * 本文件由 frontend/scripts/gen-tokens.mjs 从 docs/tasks/TASK-UI-REDESIGN/tokens.json
 * 自动生成，请勿手改。改令牌 → 改 tokens.json → 重新生成。
 *
 * 这是全仓唯一允许出现颜色字面量（hex / rgba）的地方：
 * 其余样式一律用 var(--ldst-*) 或 Tailwind 语义类（bg-surface / text-text-muted …）。
 * 图表需要的 hex 在 src/charts/cssVar.ts 里统一从这些变量转换，不在业务代码里写字面量。
 *
 * 主题机制：默认（:root）= dark；<html data-ldst-theme="light"> 覆盖为浅色。
 * 主题选择（system / light / dark）存在 localStorage['ldst.theme']，
 * system 由 JS 解析后落到 data-ldst-theme，避免 CSS 里再写一份 prefers-color-scheme 分支。
 */`

push(header)

// --- dark（默认）
push()
push(':root {')
push(`  color-scheme: dark;`)
for (const group of THEMED) emitGroup(group, 'dark')
emitFocus('dark')
emitTypography()
for (const group of STATIC.filter((g) => g !== 'typography' && g !== 'motion')) emitGroup(group)
emitLayout()
emitMotion()
push('}')

// --- light 覆盖
push()
push(`:root[data-ldst-theme='light'] {`)
push(`  color-scheme: light;`)
for (const group of THEMED) emitGroup(group, 'light')
emitFocus('light')
push('}')

// --- 密度（§7.6：只影响表格行高）
push()
push('/* 表格密度（localStorage[\'ldst.density\']）：只改行高，不动其它令牌 */')
push(`:root[data-ldst-density='compact'] { --ldst-row-h: ${tokens.layout.rowCompact.value}; }`)
push(`:root[data-ldst-density='comfortable'] { --ldst-row-h: ${tokens.layout.rowComfortable.value}; }`)

// --- 减少动效（§9：时长归零）
push()
push('@media (prefers-reduced-motion: reduce) {')
push('  :root {')
push(`    --ldst-duration-fast: ${tokens.motion.reducedMotion.value};`)
push(`    --ldst-duration-base: ${tokens.motion.reducedMotion.value};`)
push('  }')
push('}')

// --- v1 别名（DEC 迁移期：老组件名保留到 P0 之后，见 DESIGN-SPEC §2.13）
push()
push('/* ---- v1.0 名称别名（DESIGN-SPEC §2.13）：新代码一律用上面的 v2 名称 ---- */')
push(':root {')
push('  --ldst-surface-raised: var(--ldst-surface-2);')
push('  --ldst-surface-sunken: var(--ldst-sunken);')
push('  --ldst-surface-hover: var(--ldst-surface-3);')
push('  --ldst-drag: var(--ldst-accent);')
push('  --ldst-font-size-display: var(--ldst-font-size-hero);')
push('}')
push('')

const css = `${lines.join('\n')}\n`
const json = `${JSON.stringify(tokens, null, 2)}\n`

if (check) {
  const problems = []
  for (const [file, expected] of [
    [OUT_JSON, json],
    [OUT_CSS, css],
  ]) {
    const actual = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : ''
    if (actual !== expected) problems.push(path.relative(FRONTEND, file))
  }
  if (problems.length) {
    console.error(`令牌派生物已漂移：${problems.join(', ')}。请运行 node scripts/gen-tokens.mjs`)
    process.exit(1)
  }
  console.log('令牌派生物与 tokens.json 一致')
  process.exit(0)
}

fs.mkdirSync(path.dirname(OUT_CSS), { recursive: true })
fs.writeFileSync(OUT_JSON, json, 'utf8')
fs.writeFileSync(OUT_CSS, css, 'utf8')
console.log(`已生成：${path.relative(FRONTEND, OUT_JSON)}、${path.relative(FRONTEND, OUT_CSS)}`)
