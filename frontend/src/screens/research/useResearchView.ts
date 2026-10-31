import { useMemo } from 'react'
import { useParams, useSearchParams } from 'react-router-dom'

import { isApiError } from '../../api'
import {
  httpResearchSource,
  isResearchEndpointMissing,
  normalizeResearchCode,
  type ResearchSource,
  type ResearchStatus,
} from '../../api/research'
import { localDateIso } from '../../lib/format'
import { resolveState, useForcedState, type PageState } from '../../lib/pageState'
import { useAsync } from '../../lib/useAsync'
import { DEMO_DATE, createDemoResearchSource, type DemoVariant } from './demo'

const VARIANTS: DemoVariant[] = ['full', 'partial', 'empty', 'all-unverified']

function isVariant(value: unknown): value is DemoVariant {
  return typeof value === 'string' && (VARIANTS as string[]).includes(value)
}

export interface ResearchView {
  /** 规范化后的证券代码；非法输入为 null */
  code: string | null
  rawCode: string
  date: string
  demo: boolean
  variant: DemoVariant
  forced: PageState | null
  source: ResearchSource
  /** 仅 normal / partial 时可展示的数据；其余状态一律 null，避免强制态泄露真实数据 */
  status: ResearchStatus | null
  /** 原始请求结果（含 error 态），用于取错误文案 */
  data: ResearchStatus | null
  error: unknown
  endpointMissing: boolean
  state: PageState
  usable: boolean
  requesting: boolean
  name: string | null
  reload: () => void
}

/**
 * 研究页共用的取数入口。
 *
 * - 数据通道只有两个：真实 HTTP（默认）与 `?demo=1` 演示源；**演示源绝不自动兜底**。
 * - 五态优先级与全站一致：`?state=` 强制态 > 真实请求派生态。
 * - `?state=partial|empty` 在演示模式下一并切换演示变体，让五态可以只靠 URL 复现。
 */
export function useResearchView(): ResearchView {
  const { code: routeCode } = useParams()
  const [params] = useSearchParams()
  const forced = useForcedState()

  const rawCode = (routeCode ?? '').trim()
  const code = normalizeResearchCode(rawCode)
  const demo = params.get('demo') === '1'
  const variantParam = params.get('variant')
  const verify = params.get('verify')
  const variant: DemoVariant = isVariant(variantParam)
    ? variantParam
    : verify === 'none'
      ? 'all-unverified'
      : forced === 'partial'
        ? 'partial'
        : forced === 'empty'
          ? 'empty'
          : 'full'
  const date = params.get('date') || (demo ? DEMO_DATE : localDateIso())

  const source = useMemo<ResearchSource>(
    () => (demo ? createDemoResearchSource(variant) : httpResearchSource),
    [demo, variant],
  )

  const request = useAsync<ResearchStatus>(
    (signal) => source.status(code as string, date, signal),
    [code, date, source],
    { enabled: Boolean(code) },
  )

  const missing = isResearchEndpointMissing(request.error)
  const failed = request.data?.failed ?? []
  const pending = request.data?.pending ?? []

  let derived: PageState
  if (!code) {
    derived = 'error'
  } else if (request.status === 'loading') {
    derived = 'loading'
  } else if (request.status === 'error') {
    derived =
      isApiError(request.error) && request.error.kind === 'not-connected'
        ? 'offline'
        : missing
          ? 'empty'
          : 'error'
  } else if (!request.data?.available) {
    derived = 'empty'
  } else if (failed.length > 0 || pending.length > 0) {
    derived = 'partial'
  } else {
    derived = 'normal'
  }

  const state = resolveState(forced, derived)
  const usable = state === 'normal' || state === 'partial'

  const name = useMemo(() => {
    for (const stage of request.data?.stages ?? []) {
      const value = stage.artifact?.document.facts.name
      if (typeof value === 'string' && value.trim()) return value.trim()
    }
    return null
  }, [request.data])

  return {
    code,
    rawCode,
    date,
    demo,
    variant,
    forced,
    source,
    status: usable ? request.data : null,
    data: request.data,
    error: request.error,
    endpointMissing: missing,
    state,
    usable,
    requesting: request.status === 'loading',
    name,
    reload: request.reload,
  }
}
