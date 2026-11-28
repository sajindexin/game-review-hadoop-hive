import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物输出到 steam-review-web/site，由 server.py 直接托管。
// 不用默认的 dist：仓库根 .gitignore 里排除了 dist/，产物会提交不上去。
export default defineConfig({
  plugins: [vue()],
  // 必须用绝对路径：路由是 /patch/sdv_xxx 这类多级路径，
  // 相对路径 ./assets/x.js 会被解析成 /patch/assets/x.js 而 404。
  // 部署到子目录时改这里与 server.py 的挂载前缀，别用相对路径。
  base: '/',
  build: {
    outDir: '../site',
    emptyOutDir: true,
    // 数据接口是运行时请求，不需要把 data/*.js 打进 bundle
    assetsInlineLimit: 0,
  },
  server: {
    port: 5180,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8077',
        changeOrigin: true,
      },
    },
  },
})
