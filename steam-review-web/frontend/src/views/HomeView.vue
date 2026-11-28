<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SubNav from '../components/SubNav.vue'
import { loadIndex } from '../api'
import { artStyle, initialOf } from '../lib/art'
import { deltaClass, fmtDate, fmtDelta, fmtInt, fmtPct, statusText } from '../lib/format'

const route = useRoute()
const router = useRouter()

const data = ref(null)
const error = ref('')

const keyword = ref((route.query.q || '').trim())
const localSearch = ref(keyword.value)
const gFilter = ref('all')
const gSort = ref('reviews')
const sortKey = ref('date')

onMounted(async () => {
  try {
    data.value = await loadIndex()
  } catch (e) {
    error.value = e.message
  }
})

/* 顶栏搜索跳过来时同步输入框 */
watch(
  () => route.query.q,
  (v) => {
    const next = (v || '').trim()
    if (next === keyword.value) return
    keyword.value = next
    localSearch.value = next
  }
)

/* 本地输入做 160ms 防抖，再同步到 URL，避免每敲一个字都写历史记录 */
let debounceTimer = null
watch(localSearch, (v) => {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(() => {
    keyword.value = v.trim()
    router.replace({ path: '/', query: keyword.value ? { q: keyword.value } : {} })
  }, 160)
})

const allPatches = computed(() => {
  if (!data.value) return []
  const out = []
  for (const g of data.value.games) {
    for (const p of g.patches) out.push({ ...p, game: g.name, appid: g.appid })
  }
  return out
})

const latest = computed(() => {
  const list = allPatches.value.slice().sort((a, b) => (a.release_date < b.release_date ? 1 : -1))
  return list[0] || null
})

const heroPicks = computed(() =>
  allPatches.value
    .slice()
    .sort((a, b) => Math.abs(b.delta_pp || 0) - Math.abs(a.delta_pp || 0))
    .slice(0, 3)
)

const visibleGames = computed(() => {
  if (!data.value) return []
  const kw = keyword.value.toLowerCase()
  const games = data.value.games.filter((g) => {
    if (gFilter.value === 'down' && !(g.avg_delta != null && g.avg_delta <= -1)) return false
    if (gFilter.value === 'up' && !(g.avg_delta != null && g.avg_delta >= 1)) return false
    if (gFilter.value === 'flat' && !(g.avg_delta != null && Math.abs(g.avg_delta) < 1)) return false
    if (!kw) return true
    return (
      g.name.toLowerCase().includes(kw) ||
      g.patches.some((p) => ((p.title_zh || '') + ' ' + p.patch_name).toLowerCase().includes(kw))
    )
  })

  if (gSort.value === 'reviews') games.sort((a, b) => b.reviews_total - a.reviews_total)
  if (gSort.value === 'delta') games.sort((a, b) => (a.avg_delta || 0) - (b.avg_delta || 0))
  if (gSort.value === 'name') games.sort((a, b) => a.name.localeCompare(b.name, 'zh-CN'))
  return games
})

const totalPatches = computed(() =>
  data.value ? data.value.games.reduce((s, g) => s + g.patches.length, 0) : 0
)

const gameCountText = computed(() => {
  if (!data.value) return ''
  return `${visibleGames.value.length} / ${data.value.games.length} 款游戏 · 共 ${totalPatches.value} 个版本窗口`
})

const filterActive = computed(() => gFilter.value !== 'all' || !!keyword.value)

const filterHint = computed(() => {
  if (!data.value) return ''
  if (!filterActive.value) return '共 12 款游戏，点击胶囊进入版本详情'
  const n = visibleGames.value.reduce((s, g) => s + g.patches.length, 0)
  return `筛选后 ${visibleGames.value.length} 款 · ${n} 个版本`
})

const tableRows = computed(() => {
  const kw = keyword.value.toLowerCase()
  const rows = allPatches.value.filter(
    (p) =>
      !kw ||
      p.game.toLowerCase().includes(kw) ||
      (p.patch_name || '').toLowerCase().includes(kw) ||
      (p.title_zh || '').toLowerCase().includes(kw)
  )
  if (sortKey.value === 'date') rows.sort((a, b) => (a.release_date < b.release_date ? 1 : -1))
  if (sortKey.value === 'impact') rows.sort((a, b) => (a.delta_pp || 0) - (b.delta_pp || 0))
  if (sortKey.value === 'volume') rows.sort((a, b) => b.window_reviews - a.window_reviews)
  return rows
})

const patchLink = (id) => '/patch/' + encodeURIComponent(id)
const latestTitle = (g) => {
  const l = g.patches[g.patches.length - 1]
  return l ? l.title_zh || l.patch_name : ''
}

const clearFilter = () => {
  keyword.value = ''
  localSearch.value = ''
  gFilter.value = 'all'
  router.replace({ path: '/' })
}

const goPatch = (id) => router.push(patchLink(id))

const heroSummary = computed(() => {
  const p = latest.value
  if (!p) return ''
  return (
    `${p.game} · ${p.patch_name}｜${fmtDate(p.release_date)} 上线，窗口内 ${fmtInt(p.window_reviews)} 条评测，` +
    `推荐率 ${fmtPct(p.before_rate, 1)} → ${fmtPct(p.after_rate, 1)}（${fmtDelta(p.delta_pp)} pp）`
  )
})

const navItems = computed(() => [
  { label: '商店', to: '/', active: true },
  { label: '版本更新', to: { path: '/', hash: '#patches' } },
  { label: 'Agent 问答', to: '/ask' },
])
</script>

<template>
  <SubNav :items="navItems" :meta="keyword ? `筛选：${keyword}` : '数据更新于 2026.11.24'" />

  <main class="wrap">
    <div v-if="error" class="empty" style="margin-top: 40px">数据加载失败：{{ error }}</div>

    <template v-else-if="data">
      <!-- 头图区 -->
      <section class="hero">
        <router-link v-if="latest" class="hero-main" :to="patchLink(latest.patch_id)">
          <div class="art" :style="{ background: artStyle(latest.appid, 120) }"></div>
          <div class="inner">
            <div class="kicker">本周焦点更新</div>
            <h1>{{ latest.title_zh || latest.patch_name }}</h1>
            <p>{{ heroSummary }}</p>
            <span class="btn btn-green">查看版本详情</span>
          </div>
        </router-link>

        <div class="hero-side">
          <router-link
            v-for="p in heroPicks"
            :key="p.patch_id"
            class="mini-card"
            :to="patchLink(p.patch_id)"
          >
            <span class="thumb" :style="{ background: artStyle(p.appid, 160) }"></span>
            <span>
              <span class="t">{{ p.title_zh || p.patch_name }}</span>
              <span class="s">{{ p.game }} · {{ fmtDelta(p.delta_pp) }} pp · {{ fmtDate(p.release_date) }}</span>
            </span>
          </router-link>
        </div>
      </section>

      <!-- 游戏 -->
      <div class="section-head">
        <h2>按游戏浏览</h2>
        <span class="sub">{{ gameCountText }}</span>
        <span class="right"><a href="#patches">查看全部版本 ↓</a></span>
      </div>

      <div class="filter-bar">
        <div class="filter-row">
          <span class="filter-label">口碑筛选</span>
          <button class="chip" :class="{ on: gFilter === 'all' }" @click="gFilter = 'all'">全部 12 款</button>
          <button class="chip" :class="{ on: gFilter === 'down' }" @click="gFilter = 'down'">口碑下滑</button>
          <button class="chip" :class="{ on: gFilter === 'up' }" @click="gFilter = 'up'">口碑回升</button>
          <button class="chip" :class="{ on: gFilter === 'flat' }" @click="gFilter = 'flat'">基本持平</button>
          <span class="filter-sep"></span>
          <span class="filter-label">排序</span>
          <button class="chip" :class="{ on: gSort === 'reviews' }" @click="gSort = 'reviews'">评测量</button>
          <button class="chip" :class="{ on: gSort === 'delta' }" @click="gSort = 'delta'">口碑变化</button>
          <button class="chip" :class="{ on: gSort === 'name' }" @click="gSort = 'name'">名称</button>
        </div>
        <div class="filter-row">
          <label class="filter-search">
            <span>⌕</span>
            <input
              v-model="localSearch"
              type="search"
              placeholder="按游戏名 / 版本名过滤，例如「星露谷」「1.5」"
            />
          </label>
          <span class="filter-hint">{{ filterHint }}</span>
          <button v-if="filterActive" class="chip chip-clear" @click="clearFilter">清除筛选</button>
        </div>
      </div>

      <div class="capsule-grid">
        <div v-if="!visibleGames.length" class="empty" style="grid-column: 1 / -1">
          没有匹配「{{ keyword }}」的游戏，换个关键词试试
        </div>
        <router-link
          v-for="g in visibleGames"
          :key="g.appid"
          class="capsule"
          :to="patchLink(g.latest)"
        >
          <span class="art" :style="{ background: artStyle(g.appid, 135) }"></span>
          <span class="initial">{{ initialOf(g.name) }}</span>
          <span class="badge">{{ g.patches.length }} 个版本</span>
          <span class="txt">
            <span class="n">{{ g.name }}</span>
            <span class="m">{{ fmtInt(g.reviews_total) }} 条评测 · 平均 {{ fmtDelta(g.avg_delta) }} pp</span>
            <span class="m">
              <span class="tag" :class="g.avg_delta == null ? 'gray' : g.avg_delta >= 0 ? 'up' : 'down'">
                {{ statusText(g.avg_delta) }}
              </span>
              最新：{{ latestTitle(g) }}
            </span>
          </span>
        </router-link>
      </div>

      <!-- 版本表 -->
      <div id="patches" class="section-head">
        <h2>版本更新一览</h2>
        <span class="sub">更新前后各 14 天窗口 · 推荐率口径为「好评数 / 有效评论数」</span>
        <span class="right">
          <button class="chip" :class="{ on: sortKey === 'date' }" @click="sortKey = 'date'">按时间</button>
          <button class="chip" :class="{ on: sortKey === 'impact' }" @click="sortKey = 'impact'">
            按口碑变化
          </button>
          <button class="chip" :class="{ on: sortKey === 'volume' }" @click="sortKey = 'volume'">
            按评论量
          </button>
        </span>
      </div>

      <div class="panel" style="padding: 6px 10px">
        <table class="table">
          <thead>
            <tr>
              <th style="width: 34px">#</th>
              <th>游戏</th>
              <th>版本</th>
              <th style="width: 92px">发布日期</th>
              <th class="num" style="width: 84px">更新前</th>
              <th class="num" style="width: 84px">更新后</th>
              <th class="num" style="width: 88px">变化</th>
              <th class="num" style="width: 92px">窗口评论</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="!tableRows.length">
              <td colspan="8"><div class="empty">没有匹配「{{ keyword }}」的版本</div></td>
            </tr>
            <tr
              v-for="(p, i) in tableRows"
              :key="p.patch_id"
              style="cursor: pointer"
              @click="goPatch(p.patch_id)"
            >
              <td class="muted">{{ i + 1 }}</td>
              <td>{{ p.game }}</td>
              <td>
                <span style="color: #fff">{{ p.title_zh || p.patch_name }}</span>
                <span class="muted small"> {{ p.patch_name !== p.title_zh ? p.patch_name : '' }}</span>
              </td>
              <td class="muted">{{ fmtDate(p.release_date) }}</td>
              <td class="num">{{ fmtPct(p.before_rate, 1) }}</td>
              <td class="num">{{ fmtPct(p.after_rate, 1) }}</td>
              <td class="num" :class="deltaClass(p.delta_pp)">{{ fmtDelta(p.delta_pp) }} pp</td>
              <td class="num muted">{{ fmtInt(p.window_reviews) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>

    <div v-else class="empty" style="margin-top: 40px">正在加载数据…</div>
  </main>
</template>
