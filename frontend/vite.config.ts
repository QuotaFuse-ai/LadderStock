import fs from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vitest/config'

/**
 * 开发态中间件：`/__state/index`
 *
 * 进程边界（DEC-001 / 任务要求 5）：Python 不读 IndexedDB。前端把分片清单
 * 落到 `data/state/frontend-index.json`，`ldst state inspect` 只读这个文件。
 *
 * - `POST` 前端上报索引（含可选 `ack_reset` 回执）；
 * - `GET`  返回当前索引 + `pending_reset` 登记，供前端启动时清理分片。
 *
 * 只在 `npm run dev` 生效；生产构建（`frontend/dist`）不包含本中间件。
 */

const HERE = path.dirname(fileURLToPath(import.meta.url))
const REPO_ROOT = path.resolve(HERE, '..')
const INDEX_ROUTE = '/__state/index'
const MAX_BODY_BYTES = 2 * 1024 * 1024

interface PendingResetEntry {
  store: string
  requested_at: string
  note?: string
}

interface IndexDocument {
  backend: string
  schema_version: number | null
  generated_at: string | null
  stores: Record<string, unknown>
  pending_reset: PendingResetEntry[]
}

function indexPath(): string {
  const dataDir = process.env.LDST_DATA_DIR
    ? path.resolve(process.env.LDST_DATA_DIR)
    : path.join(REPO_ROOT, 'data')
  return path.join(dataDir, 'state', 'frontend-index.json')
}

function emptyDocument(): IndexDocument {
  return {
    backend: 'indexeddb',
    schema_version: null,
    generated_at: null,
    stores: {},
    pending_reset: [],
  }
}

function normalisePendingReset(value: unknown): PendingResetEntry[] {
  if (!Array.isArray(value)) return []
  const entries: PendingResetEntry[] = []
  for (const item of value) {
    if (typeof item !== 'object' || item === null) continue
    const record = item as Record<string, unknown>
    const store = typeof record.store === 'string' ? record.store : null
    if (!store) continue
    entries.push({
      store,
      requested_at:
        typeof record.requested_at === 'string' ? record.requested_at : new Date().toISOString(),
      ...(typeof record.note === 'string' ? { note: record.note } : {}),
    })
  }
  return entries
}

async function readDocument(): Promise<IndexDocument> {
  try {
    const raw = await fs.readFile(indexPath(), 'utf8')
    const parsed = JSON.parse(raw) as Record<string, unknown>
    return {
      backend: typeof parsed.backend === 'string' ? parsed.backend : 'indexeddb',
      schema_version: typeof parsed.schema_version === 'number' ? parsed.schema_version : null,
      generated_at: typeof parsed.generated_at === 'string' ? parsed.generated_at : null,
      stores:
        typeof parsed.stores === 'object' && parsed.stores !== null
          ? (parsed.stores as Record<string, unknown>)
          : {},
      pending_reset: normalisePendingReset(parsed.pending_reset),
    }
  } catch {
    // 文件不存在 / 内容损坏：当作空索引，不阻塞开发服务器
    return emptyDocument()
  }
}

/** 原子写入，避免 Python 侧读到半截 JSON。 */
async function writeDocument(document: IndexDocument): Promise<void> {
  const target = indexPath()
  await fs.mkdir(path.dirname(target), { recursive: true })
  const temp = `${target}.tmp`
  await fs.writeFile(temp, `${JSON.stringify(document, null, 2)}\n`, 'utf8')
  await fs.rename(temp, target)
}

async function readBody(request: import('node:http').IncomingMessage): Promise<string> {
  const chunks: Buffer[] = []
  let size = 0
  for await (const chunk of request) {
    const buffer = typeof chunk === 'string' ? Buffer.from(chunk) : (chunk as Buffer)
    size += buffer.length
    if (size > MAX_BODY_BYTES) throw new Error(`索引上报超过 ${MAX_BODY_BYTES} 字节上限`)
    chunks.push(buffer)
  }
  return Buffer.concat(chunks).toString('utf8')
}

function respond(response: import('node:http').ServerResponse, status: number, body: unknown): void {
  const text = JSON.stringify(body)
  response.statusCode = status
  response.setHeader('content-type', 'application/json; charset=utf-8')
  response.setHeader('cache-control', 'no-store')
  response.end(text)
}

function stateIndexPlugin(): Plugin {
  return {
    name: 'ladderstock-state-index',
    apply: 'serve',
    configureServer(server) {
      server.middlewares.use(INDEX_ROUTE, (request, response) => {
        void (async () => {
          try {
            if (request.method === 'GET') {
              respond(response, 200, await readDocument())
              return
            }
            if (request.method === 'POST') {
              const raw = await readBody(request)
              const body = JSON.parse(raw) as Record<string, unknown>
              const previous = await readDocument()
              const acked = new Set(
                Array.isArray(body.ack_reset)
                  ? body.ack_reset.filter((item): item is string => typeof item === 'string')
                  : [],
              )
              const pendingReset = previous.pending_reset.filter((item) => !acked.has(item.store))
              // 部分上报（典型：只带 ack_reset）保留上一份的字段，避免把概况清空
              const document: IndexDocument = {
                backend: typeof body.backend === 'string' ? body.backend : previous.backend,
                schema_version:
                  typeof body.schema_version === 'number'
                    ? body.schema_version
                    : previous.schema_version,
                generated_at:
                  typeof body.generated_at === 'string'
                    ? body.generated_at
                    : previous.generated_at,
                stores:
                  typeof body.stores === 'object' && body.stores !== null
                    ? (body.stores as Record<string, unknown>)
                    : previous.stores,
                pending_reset: pendingReset,
              }
              await writeDocument(document)
              respond(response, 200, {
                ok: true,
                path: 'data/state/frontend-index.json',
                cleared_reset: [...acked],
              })
              return
            }
            response.setHeader('allow', 'GET, POST')
            respond(response, 405, { ok: false, error: '只支持 GET / POST' })
          } catch (error) {
            respond(response, 500, {
              ok: false,
              error: error instanceof Error ? error.message : String(error),
            })
          }
        })()
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), stateIndexPlugin()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: false,
    proxy: {
      // 只读 API 由 `ldst run` 提供（M2）；未启动时前端显示「未接入」，不编造数据。
      '/api': {
        target: 'http://127.0.0.1:5950',
        changeOrigin: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    target: 'es2022',
  },
  test: {
    environment: 'node',
    include: ['tests/**/*.test.ts'],
  },
})
