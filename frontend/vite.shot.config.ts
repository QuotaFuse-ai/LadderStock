// 视觉巡检取证用配置（QUOT-307，沿用 QUOT-155 的取证配置位）：与 vite.config.ts 相同，
// 只把 /api 代理指向本机已启动的 `ldst run`（5950），并把 Host 改写成后端监听端口——
// 后端的 DNS rebinding 白名单要求 Host 端口与监听端口一致（web/control.py:host_header_allowed）。
// 只用于截图取证，不进入产品代码。
import { defineConfig, mergeConfig } from 'vite'

import base from './vite.config'

export default mergeConfig(
  base,
  defineConfig({
    server: {
      host: '127.0.0.1',
      port: 5199,
      strictPort: true,
      proxy: {
        '/api': {
          target: 'http://127.0.0.1:5950',
          changeOrigin: true,
        },
      },
    },
  }),
)
