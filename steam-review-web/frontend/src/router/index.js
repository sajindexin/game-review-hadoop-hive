import { createRouter, createWebHistory } from 'vue-router'

/* 路由用 history 模式，URL 更干净：/patch/sdv_20201221_15
 * 旧的多页链接（patch.html?id=xxx）保留重定向，历史文档和截图里的地址不会失效。
 */

const routes = [
  { path: '/', name: 'home', component: () => import('../views/HomeView.vue') },
  { path: '/patch/:patchId', name: 'patch', component: () => import('../views/PatchView.vue') },
  { path: '/nlp', name: 'nlp', component: () => import('../views/NlpView.vue') },
  { path: '/ask', name: 'ask', component: () => import('../views/AskView.vue') },

  // —— 兼容旧链接 ——
  { path: '/index.html', redirect: (to) => ({ path: '/', query: to.query, hash: to.hash }) },
  {
    path: '/patch.html',
    redirect: (to) => ({
      path: '/patch/' + (to.query.id || 'latest'),
      query: { ...to.query, id: undefined },
      hash: to.hash,
    }),
  },
  { path: '/nlp.html', redirect: (to) => ({ path: '/nlp', query: to.query, hash: to.hash }) },
  { path: '/ask.html', redirect: (to) => ({ path: '/ask', query: to.query, hash: to.hash }) },

  // 未匹配：回首页，避免白屏
  { path: '/:pathMatch(.*)*', redirect: '/' },
]

export default createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, from, saved) {
    if (saved) return saved
    if (to.hash) return { el: to.hash, behavior: 'smooth' }
    return { top: 0 }
  },
})
