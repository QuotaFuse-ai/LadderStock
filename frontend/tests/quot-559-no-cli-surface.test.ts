import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

/**
 * QUOT-559 守卫：用户可见面零命令行。
 *
 * 普通用户看不懂也不该看到 `ldst sync` / `qfw run` 这类命令串，更不该有
 * 「复制命令」「在终端运行」入口（QUOT-457 时代的产物）。所有生成 / 同步 /
 * 重启动作都收敛到两处页内出口：右上「本页数据同步」（PageCommandBar 自动
 * 就绪）与 S5 健康弹窗的一键重启。
 *
 * 扫描 frontend/src 全部 ts/tsx 源文件——文案「注释也要清洗」是有意为之：
 * 注释与用户可见字符串在同一次 copy/paste 里互相渗透，分家扫不住。
 * `ease-ldst` / `ldst-num` 是 CSS 类名（设计系统的动效曲线与等宽数字），
 * 与命令无关，白名单剔除。
 */
const SRC_ROOT = fileURLToPath(new URL('../src', import.meta.url))
const ALLOWED = ['ease-ldst', 'ldst-num']
const CLI_PATTERN = /ldst\s+\w|qfw\s+\w|复制命令|在终端/

function walk(dir: string): string[] {
  const out: string[] = []
  for (const name of readdirSync(dir)) {
    const full = join(dir, name)
    const stat = statSync(full)
    if (stat.isDirectory()) out.push(...walk(full))
    else out.push(full)
  }
  return out
}

describe('QUOT-559 零 CLI 用户可见面（守卫）', () => {
  it('src 全部 ts/tsx 不含 ldst/qfw 命令串与「复制命令」「在终端」入口', () => {
    const offenders: string[] = []
    for (const file of walk(SRC_ROOT)) {
      if (!/\.(tsx|ts)$/.test(file)) continue
      const text = readFileSync(file, 'utf8')
      const stripped = text.replace(new RegExp(ALLOWED.join('|'), 'g'), '')
      if (CLI_PATTERN.test(stripped)) offenders.push(file)
    }
    expect(offenders).toEqual([])
  })
})
