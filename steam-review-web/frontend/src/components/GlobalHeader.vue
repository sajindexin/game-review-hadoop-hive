<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

const keyword = ref('')

const nav = [
  { to: { path: '/', hash: '' }, label: '商店', key: 'store' },
  { to: { path: '/', hash: '#patches' }, label: '版本', key: 'patches' },
  { to: { path: '/nlp' }, label: 'NLP 洞察', key: 'nlp' },
  { to: { path: '/ask' }, label: '问答', key: 'ask' },
]

/* 版本页在旧实现里也高亮「商店」，保持一致的导航认知 */
const activeKey = computed(() => {
  if (route.name === 'nlp') return 'nlp'
  if (route.name === 'ask') return 'ask'
  return 'store'
})

function submitSearch() {
  const q = keyword.value.trim()
  if (!q) return
  router.push({ path: '/', query: { q } })
  keyword.value = ''
}
</script>

<template>
  <header class="global-header">
    <div class="wrap gh-inner">
      <router-link class="gh-logo" to="/">
        <span class="mark"></span>STEAM<span
          style="font-size: 11px; font-weight: 400; letter-spacing: 2px; color: #7b8d9a; margin-left: 2px"
          >REVIEW LAB</span
        >
      </router-link>

      <nav class="gh-nav">
        <router-link
          v-for="item in nav"
          :key="item.key"
          :to="item.to"
          :class="activeKey === item.key ? 'active' : ''"
        >
          {{ item.label }}
        </router-link>
      </nav>

      <div class="gh-right">
        <label class="gh-search">
          <span class="icon">⌕</span>
          <input
            v-model="keyword"
            type="search"
            placeholder="搜索游戏或版本…"
            autocomplete="off"
            @keydown.enter="submitSearch"
          />
        </label>
        <router-link class="gh-link" to="/ask">数据分析台</router-link>
      </div>
    </div>
  </header>
</template>
