// 临时验收用配置（QUOT-155 截图取证）：与 vite.config.ts 相同，只把 /api 代理指向 5952。
// 取证完成后删除，不进入产品代码。
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
          target: 'http://127.0.0.1:5952',
          changeOrigin: false,
        },
      },
    },
  }),
)
