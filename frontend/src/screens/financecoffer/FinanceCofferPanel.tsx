/**
 * 设置页「增强包」分区：体积明细 → embedding 勾选 → 确认安装 → 启停 → 卸载。
 *
 * 这一块是 DEC-086 六条决策的界面落点，几处硬要求：
 * - 体积按**磁盘实际占用**展示明细（PG 848 / Redis 38 / MinIO 108 / API 40 / embedding 2822），
 *   合计随勾选变化，用户看完必须点一次确认才动盘（决策 3）；
 * - embedding 可勾可不勾，但两侧后果写清楚：不勾 = 向量检索长期不可用、结构化查询照常（决策 1）；
 * - 数据全在安装目录内，装 C 盘会被后端拒绝，这里原样显示原因（决策 4）；
 * - 卸载 = 整目录删除（含 pgdata/miniodata/redisdata），文案写明不可恢复（决策 5）；
 * - 没有「轻量模式」开关：只有完整包一条路径（决策 6）。
 */

import { useEffect, useState } from 'react'
import { Play, RefreshCw, Square, Trash2 } from 'lucide-react'

import { api, describeApiError, type FcComponent, type FcJob, type FcSummary } from '../../api'
import { useAsync } from '../../lib/useAsync'
import { Badge, Banner, Button, Checkbox, EmptyState, ErrorBlock, SectionCard, StatusDot } from '../../components/ui'

const STATE_LABEL: Record<string, string> = {
  ready: '已就绪',
  failed: '安装失败',
  fetching: '取件中',
  planning: '已确认，准备中',
  initializing: '初始化数据库',
  configuring: '写配置',
  uninstalling: '卸载中',
}

const SERVICE_LABEL: Record<string, string> = {
  pg: 'PostgreSQL（宽表 / 结构化查询）',
  redis: 'Redis（缓存与任务队列）',
  minio: 'MinIO（报告与导出物料）',
  api: 'FinanceCoffer API（宽表 / 公告查询入口）',
  embedding: 'embedding 服务（bge-m3，向量检索）',
  mcp: 'MCP（agent 调用的三个取数工具）',
  etl: 'ETL 消费者（事件流 → ODS → DWD）',
}

const SVC_STATE_LABEL: Record<string, string> = {
  running: '运行中',
  stopped: '已停止',
  installed: '权重在位',
  not_installed: '未安装',
  missing_deps: '缺运行依赖',
  missing_script: '缺启动脚本',
  needs_pg: '等数据库先起',
  starting: '启动中',
  needs_unprivileged: '需普通账户',
}

function sizeText(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(2)} GB` : `${mb} MB`
}

function SizeTable({ rows }: { rows: FcComponent[] }) {
  return (
    <table className="w-full text-body">
      <caption className="sr-only">增强包组件与磁盘占用</caption>
      <thead>
        <tr className="text-caption text-text-muted">
          <th className="py-1 text-left font-normal">组件</th>
          <th className="py-1 text-right font-normal">占用</th>
          <th className="py-1 text-left font-normal">说明</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key} className="border-t border-border-subtle align-top">
            <td className="py-1.5 pr-2">
              {row.label}
              <span className="ml-2 text-caption text-text-muted">{row.required ? '必装' : '可选'}</span>
            </td>
            <td className="ldst-num py-1.5 pr-2 text-right whitespace-nowrap">{sizeText(row.size_mb)}</td>
            <td className="py-1.5 text-caption text-text-secondary">{row.note}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function ServiceRow({ name, service, port }: { name: string; service: FcSummary['doctor']['services'][string]; port?: number }) {
  return (
    <div className="flex items-start justify-between gap-3 border-t border-border-subtle py-2">
      <div className="flex items-start gap-2">
        <StatusDot tone={service?.ok ? 'ok' : 'warn'}>{service?.ok ? '就绪' : '未就绪'}</StatusDot>
        <div>
          <p className="text-body">{SERVICE_LABEL[name] ?? name}</p>
          <p className="text-caption text-text-muted">{service?.detail ?? '没有状态'}</p>
        </div>
      </div>
      <span className="ldst-num shrink-0 text-caption text-text-muted">
        {port ? `端口 ${port}` : SVC_STATE_LABEL[service?.state ?? ''] ?? service?.state ?? '—'}
      </span>
    </div>
  )
}

export default function FinanceCofferPanel() {
  const summary = useAsync((signal) => api.financecoffer.summary(signal), [])
  const [withEmbedding, setWithEmbedding] = useState(true)
  const [job, setJob] = useState<FcJob | null>(null)
  const [busy, setBusy] = useState<'install' | 'start' | 'stop' | 'uninstall' | null>(null)
  const [armed, setArmed] = useState<'install' | 'uninstall' | null>(null)
  const [notice, setNotice] = useState<{ tone: 'ok' | 'warn' | 'danger'; title: string; text: string } | null>(null)

  const data = summary.data
  const components = withEmbedding ? data?.components.full ?? [] : data?.components.core ?? []
  const totalMb = components.reduce((sum, row) => sum + row.size_mb, 0)

  // 安装是后端后台任务：这里只轮询进度，不自己拼命令行
  useEffect(() => {
    if (!job || job.status !== 'running') return
    const timer = setInterval(() => {
      void (async () => {
        try {
          const next = await api.financecoffer.job()
          if (next.job) setJob(next.job)
          if (next.job && next.job.status !== 'running') summary.reload()
        } catch {
          // 轮询失败不该打断页面；下一次再取
        }
      })()
    }, 2000)
    return () => clearInterval(timer)
  }, [job?.status, summary])

  const run = async (kind: 'install' | 'start' | 'stop' | 'uninstall', fn: () => Promise<unknown>) => {
    setBusy(kind)
    setNotice(null)
    try {
      const result = await fn()
      if (kind === 'install') {
        const created = result as FcJob
        setJob(created)
        if (created?.status === 'busy') {
          setNotice({ tone: 'warn', title: '已有任务在跑', text: '等当前任务结束再开始新的安装' })
        } else {
          setNotice({ tone: 'ok', title: '安装任务已开始', text: '进度每 2 秒刷新；完成后自动体检并显示服务状态' })
        }
      } else if (kind === 'uninstall') {
        const payload = result as { ok: boolean; status: string; detail: string }
        setNotice({
          tone: payload.ok ? 'ok' : 'danger',
          title: payload.ok ? '已卸载' : '卸载未完成',
          text: payload.detail,
        })
        summary.reload()
      } else {
        const payload = result as { ok: boolean; services: Record<string, { detail?: string }> }
        setNotice({
          tone: payload.ok ? 'ok' : 'warn',
          title: payload.ok ? '服务已拉起' : '部分服务没起来',
          text: Object.entries(payload.services)
            .map(([key, item]) => `${key}: ${item.detail ?? '—'}`)
            .join('；'),
        })
        summary.reload()
      }
    } catch (error) {
      setNotice({ tone: 'danger', title: '操作失败', text: describeApiError(error).detail })
    } finally {
      setBusy(null)
      setArmed(null)
    }
  }

  if (summary.status === 'error' || summary.error) {
    return <ErrorBlock {...describeApiError(summary.error)} onRetry={summary.reload} />
  }

  if (summary.status === 'loading') {
    return <EmptyState title="读取增强包状态…" description="第一次读取会顺带体检四个服务，约 3 秒" />
  }

  const doctor = data?.doctor
  const installed = data?.installed
  const stateText = installed ? (STATE_LABEL[installed] ?? installed) : '未安装'

  return (
    <div className="flex flex-col gap-4">
      {notice && (
        <Banner tone={notice.tone} title={notice.title}>
          {notice.text}
        </Banner>
      )}

      <SectionCard
        title="增强包状态"
        subtitle={data ? `${stateText} · ${data.module_root}` : '读取中'}
        actions={
          <>
            <Button
              variant="secondary"
              leadingIcon={<Play className="size-3.5" aria-hidden="true" />}
              onClick={() => void run('start', () => api.financecoffer.services('start'))}
              disabled={busy !== null || !installed}
            >
              启动全部
            </Button>
            <Button
              variant="secondary"
              leadingIcon={<Square className="size-3.5" aria-hidden="true" />}
              onClick={() => void run('stop', () => api.financecoffer.services('stop'))}
              disabled={busy !== null || !installed}
            >
              停止全部
            </Button>
            <Button
              variant="ghost"
              leadingIcon={<RefreshCw className="size-3.5" aria-hidden="true" />}
              onClick={() => summary.reload()}
              disabled={busy !== null}
            >
              重新体检
            </Button>
          </>
        }
      >
        {doctor ? (
          <>
            <div className="flex items-center gap-2 pb-2">
              <Badge
                tone={
                  !(doctor.usable ?? true)
                    ? 'danger'
                    : doctor.ready === doctor.total
                      ? 'ok'
                      : 'warn'
                }
              >
                {doctor.ready}/{doctor.total} 就绪
              </Badge>
              {doctor.optional && doctor.optional.length > 0 && (
                <span className="text-caption text-text-muted">
                  {doctor.optional.map((key) => SERVICE_LABEL[key] ?? key).join('、')} 是可选件，
                  未装只影响向量检索，结构化查询照常
                </span>
              )}
            </div>
            {Object.entries(doctor.services).map(([name, service]) => (
              <ServiceRow key={name} name={name} service={service} port={doctor.ports[name]} />
            ))}
          </>
        ) : (
          <EmptyState compact title="没有体检结果" description="后端没返回 doctor 字段" />
        )}
      </SectionCard>

      <SectionCard
        title="装什么、占多少"
        subtitle="体积按磁盘实际占用算：PG 下载包 315MB、解开 848MB；API 源码 3MB、带依赖 40MB；embedding 权重 2.14GB、带 torch 2.76GB"
      >
        {data ? (
          <>
            <SizeTable rows={components} />

            <div className="mt-3 border-t border-border-subtle pt-3">
              <Checkbox checked={withEmbedding} onChange={setWithEmbedding}>
                <span className="text-body">加入 embedding 运行时（模型权重 bge-m3，+{sizeText(data.sizes.embedding_mb)}）</span>
              </Checkbox>
              <ul className="mt-2 flex flex-col gap-1 pl-6 text-caption text-text-secondary">
                <li>勾选：向量检索可用；首次加载 30–60 秒，常驻内存约 +1.5 GB</li>
                <li>不勾：向量检索长期不可用；宽表 / 公告等结构化查询照常可用</li>
                <li>以后想补装，重新走一次安装即可，已有数据不会丢</li>
              </ul>
            </div>

            <div className="mt-3 flex items-center justify-between border-t border-border-subtle pt-3">
              <div>
                <p className="text-body">
                  合计 <span className="ldst-num font-medium">{sizeText(totalMb)}</span>
                  <span className="ml-2 text-caption text-text-muted">
                    （不勾 embedding {sizeText(data.sizes.core_mb)}）
                  </span>
                </p>
                <p className="text-caption text-text-muted">数据全部落在这个目录里，卸载整目录删除</p>
              </div>
              <Button
                variant="primary"
                onClick={() => setArmed('install')}
                disabled={busy !== null || job?.status === 'running'}
              >
                {installed ? '重新安装 / 补齐组件' : '开始安装'}
              </Button>
            </div>

            {armed === 'install' && (
              <div className="mt-3 rounded border border-border-subtle bg-surface-2 p-3">
                <p className="text-body">
                  即将写入 <span className="ldst-num font-medium">{sizeText(totalMb)}</span> 到{' '}
                  <span className="ldst-num">{data.module_root}</span>
                  {withEmbedding ? '（含 embedding）' : '（不含 embedding）'}。
                </p>
                <ul className="mt-1 flex flex-col gap-1 text-caption text-text-secondary">
                  {data.consequences.map((text) => (
                    <li key={text}>· {text}</li>
                  ))}
                </ul>
                <div className="mt-3 flex gap-2">
                  <Button
                    variant="primary"
                    onClick={() => void run('install', () => api.financecoffer.install({ with_embedding: withEmbedding, confirm: true }))}
                    disabled={busy !== null}
                  >
                    确认，开始安装
                  </Button>
                  <Button variant="secondary" onClick={() => setArmed(null)} disabled={busy !== null}>
                    取消
                  </Button>
                </div>
              </div>
            )}

            {job && (
              <div className="mt-3 rounded border border-border-subtle bg-surface-2 p-3">
                <div className="flex items-center gap-2">
                  <Badge tone={job.status === 'done' ? 'ok' : job.status === 'failed' ? 'danger' : 'warn'}>
                    {job.status === 'done' ? '完成' : job.status === 'failed' ? '失败' : job.status === 'busy' ? '忙' : '进行中'}
                  </Badge>
                  <span className="text-caption text-text-muted">任务：{job.kind}</span>
                </div>
                {job.progress.length > 0 && (
                  <ul className="mt-2 flex flex-col gap-1 text-caption text-text-secondary">
                    {job.progress.slice(-3).map((item, index) => (
                      <li key={`${item.at}-${index}`}>· {item.text}</li>
                    ))}
                  </ul>
                )}
                {job.result?.detail && (
                  <p className="mt-2 text-caption">结果：{job.result.detail}</p>
                )}
              </div>
            )}
          </>
        ) : (
          <EmptyState compact title="没有体积明细" description="/api/financecoffer 没返回组件清单" />
        )}
      </SectionCard>

      <SectionCard title="Redis 三端适配" subtitle="官方没有 Windows 版；四端目录各放一份二进制，共用同一份 redis.conf">
        {data ? (
          <div className="flex flex-col gap-1">
            {Object.entries(data.redis.platforms).map(([name, info]) => (
              <div key={name} className="flex items-center justify-between border-t border-border-subtle py-1.5">
                <div className="flex items-center gap-2">
                  <StatusDot tone={info.installed ? 'ok' : 'flat'}>{info.installed ? '已装' : '缺'}</StatusDot>
                  <span className="text-body">{name}</span>
                  {name === data.redis.current && <Badge tone="flat">当前机器</Badge>}
                </div>
                <span className="ldst-num truncate text-caption text-text-muted">{info.dir}</span>
              </div>
            ))}
            <p className="pt-2 text-caption text-text-muted">
              打包时四端各带一份；单机只解压本平台那一份，其余目录留空但保留路径。
            </p>
          </div>
        ) : null}
      </SectionCard>

      <SectionCard title="卸载" subtitle="删除整个模块目录，包括数据库、对象存储与 Redis 数据">
        <p className="text-body">
          卸载后数据<strong>不可恢复</strong>：pgdata / miniodata / redisdata 一起删除，没有「保留数据」选项。
        </p>
        {armed === 'uninstall' ? (
          <div className="mt-3 flex gap-2">
            <Button
              variant="danger"
              leadingIcon={<Trash2 className="size-3.5" aria-hidden="true" />}
              onClick={() => void run('uninstall', () => api.financecoffer.uninstall())}
              disabled={busy !== null}
            >
              确认删除整个目录
            </Button>
            <Button variant="secondary" onClick={() => setArmed(null)} disabled={busy !== null}>
              取消
            </Button>
          </div>
        ) : (
          <Button
            variant="secondary"
            className="mt-3"
            leadingIcon={<Trash2 className="size-3.5" aria-hidden="true" />}
            onClick={() => setArmed('uninstall')}
            disabled={busy !== null || !installed}
          >
            卸载增强包
          </Button>
        )}
      </SectionCard>
    </div>
  )
}
