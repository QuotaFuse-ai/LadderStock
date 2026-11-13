/**
 * 首页双模式之间的一次性交接（QUOT-582 / F4）。
 *
 * 对话里问过的问题切到研究台要继续用：研究台的输入框属于既有 `HomeScreen`（版式不动），
 * 所以交接走「排队中的问题」，读到即清：
 * - 存在 `sessionStorage`，刷新后不重放（同一条问题不会在刷新时又填回去）；
 * - 隐私模式读写都静默降级为「没有交接」，切模式本身不受影响。
 */

export const HOME_HANDOFF_KEY = 'home_question'

export function queueHomeQuestion(question: string): void {
  const text = question.trim()
  if (!text) return
  try {
    window.sessionStorage.setItem(HOME_HANDOFF_KEY, text)
  } catch {
    /* 隐私模式：这次交接丢一次，用户可以在研究台自己再输入 */
  }
}

/** 取出并清空排队的问题 */
export function takeHomeQuestion(): string | null {
  try {
    const text = window.sessionStorage.getItem(HOME_HANDOFF_KEY)
    if (text === null) return null
    window.sessionStorage.removeItem(HOME_HANDOFF_KEY)
    return text.trim() || null
  } catch {
    return null
  }
}
