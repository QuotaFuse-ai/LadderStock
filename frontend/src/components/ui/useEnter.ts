import { useEffect, useState } from 'react'

/**
 * 挂载后「下一帧再置为 true」，让首次渲染就有可过渡的起止状态。
 *
 * CSS transition 不会为「mount 时直接带上的最终样式」而启动，浮层件（抽屉 / 确认框 /
 * Toast）都是 `if (!open) return null` 的即时挂载，没有本钩子就只能瞬间出现。
 * 双 rAF：单帧 rAF 可能跑在首帧绘制之前，起止样式被合进同一帧，transition 不触发。
 *
 * 时长与缓动由调用方给：抽屉与 Toast 用 `duration-base`（180ms，UI-SPEC §7.3 例外），
 * 其余一律 `duration-fast`（120ms）。`prefers-reduced-motion` 下 tokens.css:193
 * 已把两档都置 0ms，这里不再另写一套。
 */
export function useEnter(dependency?: unknown): boolean {
  const [entered, setEntered] = useState(false)

  useEffect(() => {
    setEntered(false)
    let inner = 0
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => setEntered(true))
    })
    return () => {
      cancelAnimationFrame(outer)
      if (inner) cancelAnimationFrame(inner)
    }
  }, [dependency])

  return entered
}
