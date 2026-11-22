/**
 * 极简 Markdown 渲染（E 包 / DEC-091：笔记正文在页面上要能读）。
 *
 * 为什么自己写而不是引依赖：前端依赖清单里没有 markdown 库，笔记是本地
 * 纯文本资产，用不到表格 / 图片 / 脚注这些重特性——支持标题、列表、引用、
 * 代码块、行内代码、加粗、链接这七样就够记东西了。
 *
 * 安全口径：**先整体转义再叠结构**——所有文本先过 escapeHtml，之后生成的
 * 标签全部由本模块自己拼，用户输入里的 `<script>` 到这一步已经是
 * `&lt;script&gt;`，不可能复活。链接只接受 http(s)，其余协议直接丢掉。
 */

const ESCAPES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => ESCAPES[char] ?? char)
}

/** 行内片段：加粗 / 行内代码 / 链接（输入必须已经转义过）。 */
function inline(text: string): string {
  let out = text
  // 行内代码：`code`（先于加粗，避免代码里的星号被误吃）
  out = out.replace(/`([^`]+)`/g, '<code>$1</code>')
  // 加粗：**text**（代码占位不受影响——code 里不会有成对 **？可能有，所以先代码后加粗且二者不嵌套）
  out = out.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
  // 链接：[文字](http(s)://…)——其余协议一律不生成 <a>
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, (_m, label: string, href: string) => {
    return `<a href="${href}" target="_blank" rel="noreferrer noopener">${label}</a>`
  })
  return out
}

/**
 * 把一小段 markdown 转成 HTML 字符串（配合 dangerouslySetInnerHTML 使用）。
 * 支持的语法：`# 标题`（1–3 级）、`- 列表`、`1. 列表`、`> 引用`、
 * ```代码块```、**加粗**、`行内代码`、[文字](https://链接)、普通段落。
 */
export function renderMarkdownLite(raw: string): string {
  const source = String(raw ?? '')
  const lines = escapeHtml(source).split(/\r?\n/)
  const out: string[] = []
  let inCode = false
  let listMode: 'ul' | 'ol' | null = null

  const closeList = () => {
    if (listMode) {
      out.push(`</${listMode}>`)
      listMode = null
    }
  }

  for (const line of lines) {
    const trimmed = line.trim()
    if (trimmed.startsWith('```')) {
      closeList()
      if (inCode) {
        out.push('</code></pre>')
        inCode = false
      } else {
        out.push('<pre><code>')
        inCode = true
      }
      continue
    }
    if (inCode) {
      out.push(line)
      continue
    }
    if (!trimmed) {
      closeList()
      continue
    }
    const heading = /^(#{1,3})\s+(.*)$/.exec(trimmed)
    if (heading) {
      closeList()
      const level = heading[1].length
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`)
      continue
    }
    const bullet = /^[-*]\s+(.*)$/.exec(trimmed)
    if (bullet) {
      if (listMode !== 'ul') {
        closeList()
        out.push('<ul>')
        listMode = 'ul'
      }
      out.push(`<li>${inline(bullet[1])}</li>`)
      continue
    }
    const ordered = /^\d+[.、]\s*(.*)$/.exec(trimmed)
    if (ordered) {
      if (listMode !== 'ol') {
        closeList()
        out.push('<ol>')
        listMode = 'ol'
      }
      out.push(`<li>${inline(ordered[1])}</li>`)
      continue
    }
    if (trimmed.startsWith('&gt;')) {
      closeList()
      out.push(`<blockquote>${inline(trimmed.slice(4).trim())}</blockquote>`)
      continue
    }
    closeList()
    out.push(`<p>${inline(trimmed)}</p>`)
  }
  if (inCode) out.push('</code></pre>')
  closeList()
  return out.join('\n')
}

/** 给测试与预览用：从渲染结果里剥掉标签，方便断言纯文本。 */
export function markdownLiteText(raw: string): string {
  return renderMarkdownLite(raw)
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
}
