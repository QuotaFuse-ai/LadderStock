/**
 * 自选批量添加的输入解析（QUOT-431）。
 *
 * 只支持 6 位 A 股：港股 / 美股 / B 股一律**明确拒绝并给原因**，不猜市场、
 * 不静默丢弃、不把「认不出」当成「已存在」。纯函数，页面只负责展示与写库。
 *
 * 分隔符：空格 / 逗号（中英文）/ 顿号 / 分号 / 换行。代码可带 `sh`/`sz`/`bj`
 * 前缀或 `.SH`/`.SZ`/`.BJ` 后缀，归一化成裸 6 位后再与现有清单比对——后端
 * `journal.watchlist.clean_code()` 存的就是裸 6 位。
 */

export interface BatchReject {
  token: string
  reason: string
}

export interface BatchPlan {
  /** 可加入的裸 6 位代码：输入内去重、且不在现有清单里 */
  accepted: string[]
  /** 已在自选里（本次不会重复写） */
  duplicates: string[]
  /** 输入自己重复的代码：只落一条，其余在这里 */
  repeated: string[]
  /** 不能加入的条目 + 原因（逐条给，不只报个数） */
  rejected: BatchReject[]
}

const SEPARATOR = /[\s,，、;；]+/

/** 沪深京 A 股代码段（前三位；北交所按前两位判）。 */
const SH_PREFIXES = ['600', '601', '603', '605', '688', '689']
const SZ_PREFIXES = ['000', '001', '002', '003', '300', '301']
const BJ_PREFIXES = ['43', '83', '87', '88', '92']
/** 沪 B（900xxx）/ 深 B（200xxx）：6 位数字但不是 A 股。 */
const B_PREFIXES = ['900', '200']

const PREFIX = /^(SH|SZ|BJ)[.:]?(\d{6})$/i
const SUFFIX = /^(\d{6})[.](SH|SZ|BJ)$/i
/** 带港股 / 美股后缀的写法（`09988.HK`、`AAPL.US`）：先认出市场再拒绝。 */
const FOREIGN_SUFFIX = /\.(HK|US)$/i

function isAShare(code: string): boolean {
  const three = code.slice(0, 3)
  const two = code.slice(0, 2)
  return (
    SH_PREFIXES.includes(three) ||
    SZ_PREFIXES.includes(three) ||
    BJ_PREFIXES.includes(two)
  )
}

/** 一个 token → 归一化代码，或一句「为什么不收」。 */
function readToken(raw: string): { code: string } | { reason: string } {
  const token = raw.trim()
  if (!token) return { reason: '空条目' }

  const prefixed = PREFIX.exec(token)
  if (prefixed) return readBare(prefixed[2])

  const suffixed = SUFFIX.exec(token)
  if (suffixed) return readBare(suffixed[1])

  if (FOREIGN_SUFFIX.test(token)) {
    return { reason: token.toUpperCase().endsWith('.HK') ? '港股暂不支持，只收 6 位 A 股' : '美股暂不支持，只收 6 位 A 股' }
  }
  if (/^(HK|US)[.:]/i.test(token)) {
    return { reason: /^HK/i.test(token) ? '港股暂不支持，只收 6 位 A 股' : '美股暂不支持，只收 6 位 A 股' }
  }

  return readBare(token)
}

function readBare(token: string): { code: string } | { reason: string } {
  if (/^\d{6}$/.test(token)) {
    if (B_PREFIXES.some((prefix) => token.startsWith(prefix))) {
      return { reason: 'B 股暂不支持，只收 A 股' }
    }
    if (!isAShare(token)) {
      return { reason: '不是 A 股代码（6 位，但前缀不在沪深京 A 股范围）' }
    }
    return { code: token }
  }
  if (/^\d{5}$/.test(token)) return { reason: '港股代码是 5 位，暂不支持' }
  if (/^\d{1,4}$/.test(token)) return { reason: '代码位数不足 6 位' }
  if (/^\d{7,}$/.test(token)) return { reason: '代码超过 6 位' }
  if (/^[A-Z][A-Z.]{0,7}$/i.test(token)) return { reason: '美股代码（字母）暂不支持' }
  return { reason: '无法识别的代码格式' }
}

/**
 * 解析批量输入。`existing` 是当前自选清单的裸 6 位代码。
 *
 * 顺序即用户输入顺序：先加入的先落库，错误原因也按输入顺序给。
 */
export function planWatchBatch(input: string, existing: readonly string[] = []): BatchPlan {
  const held = new Set(existing.map((code) => code.trim().toUpperCase()))
  const seen = new Set<string>()
  const plan: BatchPlan = { accepted: [], duplicates: [], repeated: [], rejected: [] }

  for (const token of input.split(SEPARATOR)) {
    if (!token.trim()) continue
    const result = readToken(token)
    if ('reason' in result) {
      plan.rejected.push({ token: token.trim(), reason: result.reason })
      continue
    }
    const code = result.code
    if (held.has(code)) {
      plan.duplicates.push(code)
      continue
    }
    if (seen.has(code)) {
      plan.repeated.push(code)
      continue
    }
    seen.add(code)
    plan.accepted.push(code)
  }

  return plan
}
