#!/usr/bin/env node
/**
 * UI 一致性口径检查（QUOT-306 / QUOT-317）。
 *
 * 六项可复算口径，全部退出码可判：
 *   1. bare-table    全仓无裸 `<table>`（`components/ui/Table.tsx` 内部实现除外）
 *   2. hex           源码无硬编码 hex（`styles/tokens.css` 与 `charts/cssVar.ts` 兜底值除外）
 *   3. arbitrary     屏幕/组件无任意值 class（`p-[13px]` / `gap-[…]` / `w-[…]` / `text-[…]`）
 *   4. numeric       数值列走 `.ldst-num`：`numeric: true` 只出现在引用 DataTable 的文件里，
 *                    屏幕内不手写 `<td>`，也不内联 `fontVariantNumeric`
 *   5. datatable     DataTable 真实引用屏数 ≥ 3（不接受“导出但没人用”）
 *   6. durations     过渡时长取 token（只允许 `duration-fast` / `duration-base`），
 *                    内联毫秒值必须在 24–160ms 或 0ms（reduced-motion）内
 *
 * 用法：npm run check:ui
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { dirname, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const FRONTEND = resolve(HERE, '..')
const SRC = join(FRONTEND, 'src')
const SCREENS = join(SRC, 'screens')
const ALLOW_HEX = new Set(['src/styles/tokens.css', 'src/charts/cssVar.ts'])
const TABLE_FILE = 'src/components/ui/Table.tsx'

const files = []
function walk(dir) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full)
    else if (/\.(ts|tsx|css)$/.test(entry)) files.push(full)
  }
}
walk(SRC)

const rel = (file) => relative(FRONTEND, file).replaceAll('\\', '/')
const read = (file) => readFileSync(file, 'utf8')

/** 去掉注释，避免把说明文字里的 class/hex 当违规（例如 Button 注释里的示例）。 */
function stripComments(text) {
  return text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
}

function lineHits(file, pattern) {
  const hits = []
  const lines = read(file).split('\n')
  lines.forEach((line, index) => {
    if (pattern.test(line)) hits.push(`${rel(file)}:${index + 1}  ${line.trim().slice(0, 120)}`)
  })
  return hits
}

const results = []
function report(name, offenders, detail) {
  results.push({ name, ok: offenders.length === 0, offenders, detail })
}

// 1. 裸 <table>
{
  const offenders = files
    .filter((file) => rel(file) !== TABLE_FILE)
    .flatMap((file) => lineHits(file, /<table[\s>]/))
  report('bare-table', offenders, '裸 <table> 仅允许出现在 components/ui/Table.tsx')
}

// 2. 硬编码 hex（排除生成物与图表兜底；`&#123;` 之类 HTML 实体不算）
{
  const HEX = /(?<!&)#[0-9a-fA-F]{3,8}\b/g
  const offenders = []
  for (const file of files) {
    if (ALLOW_HEX.has(rel(file))) continue
    const lines = stripComments(read(file)).split('\n')
    lines.forEach((line, index) => {
      if (HEX.test(line)) offenders.push(`${rel(file)}:${index + 1}  ${line.trim().slice(0, 120)}`)
      HEX.lastIndex = 0
    })
  }
  report('hex', offenders, '颜色只能来自 --ldst-* 变量')
}

// 3. 任意值 class
{
  const UTILITY = '(?:p[trblxy]?|m[trblxy]?|gap|w|h|min-w|min-h|max-w|max-h|size|text|leading|tracking|rounded|top|left|right|bottom|inset)'
  const ARBITRARY = new RegExp(`(?:^|[\\s"'\`])${UTILITY}-\\[[^\\]]+\\]`, 'g')
  const offenders = []
  for (const file of files.filter((file) => rel(file).startsWith('src/screens/') || rel(file).startsWith('src/components/'))) {
    const lines = stripComments(read(file)).split('\n')
    lines.forEach((line, index) => {
      if (ARBITRARY.test(line)) offenders.push(`${rel(file)}:${index + 1}  ${line.trim().slice(0, 120)}`)
      ARBITRARY.lastIndex = 0
    })
  }
  report('arbitrary', offenders, '新刻度先进 tailwind.config.ts / token，不写 p-[13px]')
}

// 4. 数值列 .ldst-num
{
  const offenders = []
  for (const file of files) {
    const path = rel(file)
    if (path === TABLE_FILE) continue
    const text = stripComments(read(file))
    if (path.startsWith('src/screens/') && /<td[\s>]/.test(text)) {
      offenders.push(`${path}  屏幕内手写 <td>：数值基线必须交给 DataTable`)
    }
    if (/fontVariantNumeric/.test(text) && path !== 'src/styles/tokens.css') {
      offenders.push(`${path}  内联 fontVariantNumeric：请用 .ldst-num`)
    }
    if (/numeric:\s*true/.test(text) && !/DataTable/.test(text)) {
      offenders.push(`${path}  出现 numeric: true 却未引用 DataTable`)
    }
  }
  report('numeric', offenders, 'DataTable 的 numeric 列在组件内统一套 .ldst-num')
}

// 5. DataTable 真实引用屏数
{
  const refs = files.filter((file) => rel(file).startsWith('src/screens/') && /<DataTable[\s<]/.test(read(file))).map(rel)
  const offenders = refs.length >= 3 ? [] : [`DataTable 只在 ${refs.length} 张屏使用：${refs.join(', ') || '无'}`]
  report('datatable', offenders, `${refs.length} 张屏引用：${refs.join(', ')}`)
}

// 6. 过渡时长
{
  const offenders = []
  for (const file of files) {
    const path = rel(file)
    const lines = stripComments(read(file)).split('\n')
    lines.forEach((line, index) => {
      if (/duration-\[\d/.test(line) || /(?:^|[\s"'])duration-\d+/.test(line)) {
        offenders.push(`${path}:${index + 1}  非 token 时长  ${line.trim().slice(0, 120)}`)
        return
      }
      // 只查 transition，不把骨架扫光 / 呼吸点这类 ambient animation 算进过渡
      if (!/transition/.test(line)) return
      const inline = line.match(/(\d+(?:\.\d+)?)ms/g)
      if (!inline) return
      for (const value of inline) {
        const ms = Number.parseFloat(value)
        const reducedMotionKill = ms <= 1
        const inRange = ms >= 24 && ms <= 160
        const baseException = ms === 180 && /duration-base|drawer|toast|抽屉|Toast/.test(line)
        if (!reducedMotionKill && !inRange && !baseException) {
          offenders.push(`${path}:${index + 1}  内联时长 ${value} 超出 24–160ms  ${line.trim().slice(0, 120)}`)
        }
      }
    })
  }
  report('durations', offenders, 'hover/press/展开用 duration-fast；抽屉/Toast 的 duration-base 是登记例外')
}

let failed = 0
for (const result of results) {
  console.log(`${result.ok ? 'PASS' : 'FAIL'}  ${result.name.padEnd(11)}  ${result.detail}`)
  for (const offender of result.offenders) console.log(`      ${offender}`)
  if (!result.ok) failed += 1
}
console.log(`\n${results.length - failed}/${results.length} 项通过`)
process.exit(failed === 0 ? 0 : 1)
