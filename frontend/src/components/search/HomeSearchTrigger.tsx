import { Search } from 'lucide-react'

import { cn } from '../../lib/cn'

/**
 * C-21 `HomeSearchTrigger`（规格 §4）：首页右上角看起来是输入框、行为是按钮的入口。
 *
 * 三条规格口径不要改：
 * - **是 `<button>` 不是 input**（§4.3）：入口内打字等于把面板做两遍，`readonly` input
 *   会被读屏播报成编辑框却打字无声；
 * - **`pointerdown` 打开、`focus` 不打开**（§4.5 / §14-A2）：Tab 路过即弹遮罩会让键盘用户
 *   每按一次 Tab 都被迫 Esc 退出。键盘可达由 Enter / Space（button 原生）与全局 `/` 承担；
 * - 高度 40px = `--ldst-control-h-lg`，该档 usage 注释原文就是「主操作按钮 lg / 搜索框」；
 *   占位文案「搜索个股 / 行业 / 资讯」与三组结果一一对应，**不写「提问」**（§4.2 / §14-A1：
 *   仓内没有问答通道，写「问」是骗人的承诺）。
 */

export interface HomeSearchTriggerProps {
  expanded: boolean
  /** 面板元素 id，供 `aria-controls` 关联 */
  panelId: string
  onOpen: () => void
  /** 首页 v2 把入口移进「今天，想研究什么？」卡内，触发条改为满栏（SPEC-HOME-V2 §3.1） */
  fullWidth?: boolean
}

export function HomeSearchTrigger({ expanded, panelId, onOpen, fullWidth = false }: HomeSearchTriggerProps) {
  return (
    <button
      type="button"
      // `onPointerDown` 让入口按下即响应（§4.5 的「不等到 click 才开」）；
      // `onClick` 是键盘路径的必要补充：Enter / Space 只发 click，只挂 pointerdown 的按钮
      // 对键盘用户是死的（§7.1 第 1 条要求 Enter/Space 能打开）。两条都只做同一件幂等事。
      onPointerDown={onOpen}
      onClick={onOpen}
      aria-haspopup="dialog"
      aria-expanded={expanded}
      aria-controls={panelId}
      className={cn(
        'group relative inline-flex h-control-lg items-center rounded border border-border-strong bg-sunken pl-7 pr-2 text-body transition-colors duration-fast ease-ldst hover:border-accent active:translate-y-px focus-visible:border-accent',
        fullWidth ? 'w-full' : 'w-64 max-w-full rail:w-44',
      )}
    >
      <span aria-hidden="true" className="pointer-events-none absolute left-2 flex text-text-muted">
        <Search className="size-3.5" aria-hidden="true" />
      </span>
      {/* rail 断点下按 §4.2 截断为「搜索个股 / 行业 …」，文案本身不改：放大镜与键帽已足够表意 */}
      <span className="min-w-0 flex-1 truncate text-text-muted transition-colors duration-fast ease-ldst group-hover:text-text-secondary">
        搜索个股 / 行业 / 资讯
      </span>
      <kbd className="rounded-xs border border-border px-1 text-caption text-text-muted group-focus:hidden rail:hidden">
        /
      </kbd>
      <span className="hidden text-caption text-text-muted group-focus:inline">按 Enter</span>
    </button>
  )
}
