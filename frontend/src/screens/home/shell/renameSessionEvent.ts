/**
 * 壳内广播：某条会话的标题在别处改了（列表菜单 / 抽屉菜单），右侧 `ChatPane` 标题行要跟上。
 *
 * `ChatPane` 自带一份本地标题（首问回写 / 它自己标题行的改名），只在 `activeSessionId`
 * 变化时重读；从左侧菜单改名不会换会话，所以用这个轻量广播把新标题送过去，而不是
 * 把外壳的标题状态硬塞进 ChatPane 再破坏它现有的「本地优先」口径。
 */
export const RENAME_EVENT = 'ldst:conversation-rename'

export interface RenameDetail {
  sessionId: string
  title: string
}

export function broadcastRename(detail: RenameDetail): void {
  if (typeof window === 'undefined') return
  window.dispatchEvent(new CustomEvent<RenameDetail>(RENAME_EVENT, { detail }))
}
