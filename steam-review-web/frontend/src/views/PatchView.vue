<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SubNav from '../components/SubNav.vue'
import LineChart from '../components/LineChart.vue'
import BarChart from '../components/BarChart.vue'
import EvidenceCard from '../components/EvidenceCard.vue'
import ReviewCard from '../components/ReviewCard.vue'
import { loadIndex, loadPatch, loadVerified } from '../api'
import { artStyle, initialOf } from '../lib/art'
import { fmtDate, fmtDelta, fmtInt, fmtPct, langName, statusText } from '../lib/format'

const route = useRoute()
const router = useRouter()

const patch = ref(null)
const index = ref(null)
const verified = ref([])
const loading = ref(true)
const notFound = ref(false)

const lang = ref('all')
const phaseFilter = ref('all')
const verdictFilter = ref('all')
const shown = ref(10)
const activeTab = ref('overview')
const openItem = ref(null)

const TABS = [
  { key: 'overview', label: '概览' },
  { key: 'items', label: '改动条目' },
  { key: 'reviews', label: '评测样本' },
  { key: 'ask', label: '问答入口' },
]

const p = computed(() => patch.value?.patch || {})
const items = computed(() => p.value.items || [])
const comments = computed(() => patch.value?.comments || [])

/* ------------------------------------------------------------ 数据加载 */

async function boot() {
  const id = route.params.patchId
  loading.value = true
  notFound.value = false
  patch.value = null
  verified.value = []
  openItem.value = null
  lang.value = 'all'
  activeTab.value = 'overview'

  try {
    index.value = await loadIndex()
  } catch (e) {
    index.value = null
  }

  let realId = id
  if (!realId || realId === 'latest') {
    const g0 = index.value?.games?.[0]
    realId = g0 ? g0.patches[g0.patches.length - 1].patch_id : ''
  }
  if (!realId) {
    notFound.value = true
    loading.value = false
    return
  }

  // 引文与版本数据并行取，少一次串行等待
  const verifiedTask = loadVerified(realId)

  try {
    patch.value = await loadPatch(realId)
  } catch (e) {
    notFound.value = true
    loading.value = false
    return
  }
  verified.value = await verifiedTask
  loading.value = false

  document.title = `${p.value.title_zh || p.value.patch_name} · ${p.value.game} · Steam 评测分析台`

  // 深链：?tab= / ?item= / ?review=
  const tab = route.query.tab
  if (tab && TABS.some((t) => t.key === tab)) activeTab.value = tab

  const wantItem = route.query.item
  if (wantItem) {
    openItem.value = wantItem
    activeTab.value = 'items'
    const wantReview = route.query.review
    await nextTick()
    if (wantReview) {
      document
        .getElementById(`quote-${wantItem}-${wantReview}`)
        ?.scrollIntoView({ block: 'center' })
    }
  }
}

onMounted(boot)
watch(() => route.params.patchId, boot)

/* ------------------------------------------------------------ 概览 */

const statsNow = computed(() => (patch.value?.stats || {})[lang.value] || {})
const statsAll = computed(() => (patch.value?.stats || {}).all || {})

const deltaNow = computed(() => {
  const b = statsNow.value.before
  const a = statsNow.value.after
  return b?.rate != null && a?.rate != null ? a.rate - b.rate : null
})

const statCards = computed(() => {
  const s = statsNow.value
  const b = s.before || {}
  const r = s.release || {}
  const a = s.after || {}
  const d = deltaNow.value
  return [
    { label: '更新前推荐率', value: fmtPct(b.rate, 1), foot: `${fmtInt(b.reviews)} 条 · 前 14 天` },
    { label: '更新日推荐率', value: fmtPct(r.rate, 1), foot: `${fmtInt(r.reviews)} 条 · 当日` },
    { label: '更新后推荐率', value: fmtPct(a.rate, 1), foot: `${fmtInt(a.reviews)} 条 · 后 14 天` },
    {
      label: '净变化',
      value: fmtDelta(d),
      unit: 'pp',
      foot: `Hive 口径 ${fmtInt(patch.value?.window_reviews_total)} 条 · 样本池 ${fmtInt(patch.value?.pool_reviews)} 条`,
      cls: d == null ? '' : d >= 0 ? 'pos' : 'neg',
    },
  ]
})

const LANGS = [
  ['all', '全部语种'],
  ['chinese', '简中 + 繁中'],
  ['en', '英语'],
]

const trendPoints = computed(() => {
  if (!patch.value) return []
  if (lang.value === 'all') return patch.value.trend || []
  return (patch.value.trend_lang || {})[lang.value] || []
})

const barPoints = computed(() => (patch.value?.trend || []).slice())

const chartFoot = computed(() => {
  const valid = trendPoints.value.filter((x) => x.rate != null)
  if (!valid.length) return '该语种样本不足'
  const peak = valid.reduce((m, x) => (x.reviews > m.reviews ? x : m), valid[0])
  const lo = valid.reduce((m, x) => (x.rate < m.rate ? x : m), valid[0])
  return `峰值 ${peak.date}（${fmtInt(peak.reviews)} 条）· 低点 ${lo.date}（${fmtPct(lo.rate, 1)}）`
})

const phaseCards = computed(() => {
  const s = statsNow.value
  return [
    { key: 'before', name: '更新前 14 天', d: s.before },
    { key: 'release', name: '更新当日', d: s.release },
    { key: 'after', name: '更新后 14 天', d: s.after },
  ]
})

/* language_compare 里 all 是聚合值，和具体语种并列会重复计数，单独排除 */
const langRows = computed(() => (patch.value?.language_compare || []).filter((r) => r.lang !== "all"))

/* ------------------------------------------------------------ 改动条目 */

const quotesFor = (itemId) =>
  verified.value.filter((e) => e.patch_id === p.value.patch_id && e.item_id === itemId)

const toggleItem = (id) => {
  openItem.value = openItem.value === id ? null : id
}

const itemHitCount = computed(() => items.value.filter((i) => i.mentions > 0).length)

/* ------------------------------------------------------------ 评测样本 */

const filteredReviews = computed(() =>
  comments.value.filter((c) => {
    if (phaseFilter.value !== 'all' && c.phase !== phaseFilter.value) return false
    if (verdictFilter.value === 'up' && !c.voted_up) return false
    if (verdictFilter.value === 'down' && c.voted_up) return false
    return true
  })
)

const shownReviews = computed(() => filteredReviews.value.slice(0, shown.value))

const reviewCount = computed(
  () => `共 ${filteredReviews.value.length} 条样本，已显示 ${shownReviews.value.length} 条`
)

watch([phaseFilter, verdictFilter], () => {
  shown.value = 10
})

/* ------------------------------------------------------------ 问答入口 */

const askQuestions = computed(() => {
  const title = p.value.title_zh || p.value.patch_name
  return [
    `${title} 更新后口碑变化如何？`,
    '中文玩家对这次更新的评价和英文玩家差多少？',
    '这次更新里哪一项改动被提到最多？',
    '帮我找几条不推荐的评测看看问题在哪。',
    '玩家主要在讨论什么？',
    '更新后哪些方面的负面反馈增加了？',
  ]
})

const askIntro = computed(
  () =>
    `已载入 ${p.value.game}「${p.value.title_zh || p.value.patch_name}」的窗口统计与 ${comments.value.length} 条评测样本，` +
    `可以直接问推荐率变化、语种差异或改动条目的讨论情况。`
)

const askLink = (q) =>
  '/ask?patch=' + encodeURIComponent(p.value.patch_id) + '&q=' + encodeURIComponent(q)

/* ------------------------------------------------------------ 导航与标签 */

const gameVersionCount = computed(() => {
  if (!index.value || !patch.value) return ''
  const g = index.value.games.find((x) => String(x.appid) === String(p.value.appid))
  return g ? `${g.patches.length} 个版本窗口` : ''
})

const navItems = computed(() => [
  { label: '商店', to: '/' },
  { label: '版本更新', to: { path: '/', hash: '#patches' } },
  { label: p.value.game || '', to: '/?q=' + encodeURIComponent(p.value.game || '') },
  { label: p.value.title_zh || p.value.patch_name || '', active: true },
  { label: 'Agent 问答', to: '/ask?patch=' + encodeURIComponent(p.value.patch_id || '') },
])

function setTab(key) {
  activeTab.value = key
  router.replace({ query: { ...route.query, tab: key === 'overview' ? undefined : key } })
}
</script>

<template>
  <SubNav :items="navItems" :meta="gameVersionCount" />

  <main class="wrap">
    <div v-if="notFound" class="empty" style="margin-top: 40px">
      找不到版本「{{ route.params.patchId }}」的数据。<router-link to="/">返回总览</router-link>
    </div>

    <div v-else-if="loading" class="empty" style="margin-top: 40px">正在加载版本数据…</div>

    <template v-else-if="patch">
      <div class="breadcrumbs">
        <router-link to="/">全部游戏</router-link><span class="sep">›</span>
        <router-link :to="{ path: '/', query: { q: p.game } }">{{ p.game }}</router-link>
        <span class="sep">›</span>
        <span>{{ p.title_zh || p.patch_name }}</span>
      </div>

      <!-- 游戏页头 -->
      <section class="game-header">
        <div class="cover">
          <div class="cover-art" :style="{ background: artStyle(p.appid, 120) }"></div>
          <div class="cover-initial">{{ initialOf(p.game) }}</div>
          <div class="cover-word">
            <div class="kicker">{{ p.game }}</div>
            <div class="title">{{ p.title_zh || p.patch_name }}</div>
            <div class="sub">{{ p.patch_name }} · {{ fmtDate(p.release_date) }}</div>
          </div>
          <div class="thumb-strip"><span></span><span></span><span></span><span></span></div>
        </div>

        <div class="game-info">
          <h2 class="game-title">{{ p.game }}</h2>
          <h1 class="patch-title">{{ p.title_zh || p.patch_name }}</h1>
          <div class="patch-sub">
            {{ p.patch_name }} · 发布于 {{ fmtDate(p.release_date) }} ·
            {{ patch.delta_pp == null ? '' : statusText(patch.delta_pp) }}
          </div>

          <div class="tag-row">
            <span class="tag">{{ p.platform || 'PC / Steam' }}</span>
            <span class="tag gray">AppID {{ p.appid }}</span>
            <span class="tag gray">{{ items.length }} 项改动</span>
            <span v-if="patch.delta_pp != null" class="tag" :class="patch.delta_pp >= 0 ? 'up' : 'down'">
              {{ fmtDelta(patch.delta_pp) }} pp
            </span>
          </div>

          <div class="meta-table">
            <div class="meta-row"><span class="k">更新名称</span><span class="v">{{ p.patch_name }}</span></div>
            <div class="meta-row"><span class="k">发布日期</span><span class="v">{{ fmtDate(p.release_date) }}</span></div>
            <div class="meta-row">
              <span class="k">统计窗口</span>
              <span class="v">{{ fmtDate(p.window_start) }} — {{ fmtDate(p.window_end) }}（UTC）</span>
            </div>
            <div class="meta-row">
              <span class="k">窗口评论</span>
              <span class="v">
                {{ fmtInt(patch.window_reviews_total) }} 条（其中 {{ fmtInt(patch.pool_reviews) }} 条进入样本池）
              </span>
            </div>
          </div>

          <div class="action-row">
            <a v-if="p.source_url" class="btn btn-green" :href="p.source_url" target="_blank" rel="noopener">
              查看原始公告
            </a>
            <router-link class="btn btn-blue" :to="'/ask?patch=' + encodeURIComponent(p.patch_id)">
              就此版本提问
            </router-link>
            <router-link
              class="btn btn-blue"
              :to="'/nlp?patch=' + encodeURIComponent(p.patch_id) + '&phase=after'"
            >
              NLP 主题洞察
            </router-link>
          </div>
          <div class="small muted mt8">{{ p.selection_note }}</div>
        </div>
      </section>

      <!-- 标签页 -->
      <div class="tabs">
        <button
          v-for="t in TABS"
          :key="t.key"
          :class="{ active: activeTab === t.key }"
          @click="setTab(t.key)"
        >
          {{ t.label }}
        </button>
      </div>

      <!-- 概览 -->
      <section v-show="activeTab === 'overview'" class="tab-panel active">
        <div class="stat-grid">
          <div v-for="c in statCards" :key="c.label" class="stat-card" :class="c.cls">
            <div class="label">{{ c.label }}</div>
            <div class="value">{{ c.value }} <small v-if="c.unit">{{ c.unit }}</small></div>
            <div class="foot">{{ c.foot }}</div>
          </div>
        </div>

        <div class="panel">
          <h3 class="panel-title">
            推荐率趋势
            <span class="hint">窗口 −14 ～ +14 天，虚线为更新日</span>
            <span class="right flex">
              <span
                v-for="[k, label] in LANGS"
                :key="k"
                class="chip"
                :class="{ on: lang === k }"
                @click="lang = k"
              >
                {{ label }}
              </span>
            </span>
          </h3>
          <LineChart :points="trendPoints" />
          <div class="chart-legend">
            <span><i></i>每日推荐率</span>
            <span><i class="dash"></i>更新日</span>
            <span class="muted">{{ chartFoot }}</span>
          </div>
        </div>

        <div class="panel">
          <h3 class="panel-title">
            每日评论量
            <span class="hint">更新当日讨论量通常出现峰值，可作为窗口有效性佐证</span>
          </h3>
          <BarChart :points="barPoints" />
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px">
          <div class="panel">
            <h3 class="panel-title">三阶段对比 <span class="hint">更新前 / 更新日 / 更新后</span></h3>
            <div class="phase-grid">
              <div v-for="ph in phaseCards" :key="ph.key" class="phase-card">
                <div class="name">{{ ph.name }}</div>
                <template v-if="ph.d">
                  <div class="rate">{{ fmtPct(ph.d.rate, 1) }}</div>
                  <div class="cnt">{{ fmtInt(ph.d.reviews) }} 条评价 · 推荐 {{ fmtInt(ph.d.recommended) }}</div>
                  <div class="bar-bg">
                    <div
                      class="bar-fill"
                      :style="{ width: Math.max(0, Math.min(100, ph.d.rate || 0)) + '%' }"
                    ></div>
                  </div>
                </template>
                <div v-else class="rate">—</div>
              </div>
            </div>
          </div>

          <div class="panel">
            <h3 class="panel-title">分语种口碑变化 <span class="hint">灰色为更新前，蓝色为更新后</span></h3>
            <div v-if="!langRows.length" class="empty">该版本没有可用的分语种统计</div>
            <div v-for="r in langRows" :key="r.lang" class="lang-row">
              <span class="lname">{{ langName(r.lang) }}</span>
              <span class="track">
                <span class="before" :style="{ left: 0, width: r.before_rate + '%', opacity: 0.5 }"></span>
                <span class="after" :style="{ left: 0, width: r.after_rate + '%', opacity: 0.85 }"></span>
              </span>
              <span class="delta" :class="r.delta > 0 ? 'pos' : r.delta < 0 ? 'neg' : ''">
                {{ fmtDelta(r.delta) }} pp
              </span>
            </div>
          </div>
        </div>
      </section>

      <!-- 改动条目 -->
      <section v-show="activeTab === 'items'" class="tab-panel active">
        <div class="panel">
          <h3 class="panel-title">
            版本改动条目
            <span class="hint">来自官方更新公告，关键词由组内人工整理；命中数为窗口内评测关键词近似统计</span>
          </h3>

          <div v-if="!items.length" class="empty">该版本没有登记改动条目</div>

          <div
            v-for="it in items"
            :key="it.item_id"
            class="item-block"
            :class="{ open: openItem === it.item_id }"
          >
            <div class="item-card" @click="toggleItem(it.item_id)">
              <div class="idx">{{ it.item_id }}</div>
              <div>
                <div class="t">
                  {{ it.title }}
                  <span class="muted small">{{ it.title_en }}</span>
                </div>
                <div class="d">{{ it.description }}</div>
                <div class="words">
                  <span v-if="it.section" class="tag">{{ it.section }}</span>
                  <span v-for="w in (it.aliases || []).slice(0, 5)" :key="w" class="tag gray">{{ w }}</span>
                </div>
              </div>
              <div class="metric">
                <div class="n">{{ fmtInt(it.mentions) }}</div>
                <div class="u">窗口提及</div>
                <div class="toggle">{{ openItem === it.item_id ? '收起引文 ▲' : '查看引文 ▼' }}</div>
              </div>
            </div>

            <div v-if="openItem === it.item_id" class="evi-list">
              <div class="evi-title">已复核引文（{{ quotesFor(it.item_id).length }} 条）</div>
              <EvidenceCard
                v-for="e in quotesFor(it.item_id)"
                :key="e.review_id"
                :evidence="e"
                :appid="p.appid"
              />
              <div v-if="!quotesFor(it.item_id).length" class="empty small">暂无已复核引文</div>
            </div>
          </div>
        </div>

        <div class="panel">
          <h3 class="panel-title">窗口说明</h3>
          <div class="muted small">
            共 {{ items.length }} 条改动，其中 {{ itemHitCount }} 条在窗口内被评测正文直接提及。命中数采用「官方条目 +
            别名表」做不区分大小写的子串匹配，仅衡量该改动在评测中的能见度，不等价于情感极性；多数 Steam
            评测不会逐条点评更新内容，因此该数值普遍偏低属于正常现象。点击条目可查看与 Agent
            同源的已复核引文，并跳转 Steam 原文。
          </div>
        </div>
      </section>

      <!-- 评测样本 -->
      <section v-show="activeTab === 'reviews'" class="tab-panel active">
        <div class="review-toolbar">
          <span
            v-for="opt in [
              ['all', '全部阶段'],
              ['before', '更新前'],
              ['release', '更新日'],
              ['after', '更新后'],
            ]"
            :key="'p' + opt[0]"
            class="chip"
            :class="{ on: phaseFilter === opt[0] }"
            data-filter="phase"
            :data-value="opt[0]"
            @click="phaseFilter = opt[0]"
          >
            {{ opt[1] }}
          </span>
          <span style="width: 10px"></span>
          <span
            v-for="opt in [
              ['all', '全部评价'],
              ['up', '推荐'],
              ['down', '不推荐'],
            ]"
            :key="'v' + opt[0]"
            class="chip"
            :class="{ on: verdictFilter === opt[0] }"
            data-filter="verdict"
            :data-value="opt[0]"
            @click="verdictFilter = opt[0]"
          >
            {{ opt[1] }}
          </span>
          <span class="spacer" style="margin-left: auto"></span>
          <span class="muted small">{{ reviewCount }}</span>
        </div>

        <div v-if="!shownReviews.length" class="empty">当前筛选条件下没有评论，换个条件试试</div>
        <ReviewCard
          v-for="c in shownReviews"
          :key="c.review_id"
          :comment="c"
          :appid="p.appid"
          :items="items"
        />

        <div class="flex" style="justify-content: center; margin-top: 12px">
          <button
            v-if="shownReviews.length < filteredReviews.length"
            class="btn btn-ghost btn-sm"
            @click="shown += 10"
          >
            显示更多评论
          </button>
        </div>
        <div class="small muted mt8" style="text-align: center">
          评论为窗口内真实 Steam 评测抽样，保留原文；用户 ID 做脱敏处理。
        </div>
      </section>

      <!-- 问答入口 -->
      <section v-show="activeTab === 'ask'" class="tab-panel active">
        <div class="panel">
          <h3 class="panel-title">
            就这个版本提问 <span class="hint">问答基于当前版本的统计口径与评测样本</span>
          </h3>
          <div class="muted" style="margin-bottom: 12px">{{ askIntro }}</div>
          <div class="action-row">
            <router-link v-for="q in askQuestions" :key="q" class="chip" :to="askLink(q)">{{ q }}</router-link>
          </div>
          <div class="mt16">
            <router-link class="btn btn-blue" :to="'/ask?patch=' + encodeURIComponent(p.patch_id)">
              打开完整问答页
            </router-link>
          </div>
        </div>

        <div class="panel">
          <h3 class="panel-title">这个数据怎么来的</h3>
          <ul class="muted small" style="margin: 0; padding-left: 18px; line-height: 2">
            <li>评测清洗：BBCode / HTML 标记剥离、换行折叠、非法记录剔除（口径见交付包 FIELD_DICTIONARY）。</li>
            <li>窗口：更新日前 14 天、更新当日、更新后 14 天，按 UTC 日期切分。</li>
            <li>推荐率：voted_up 为 true 的评论数 ÷ 窗口内有效评论数，缺失 voted_up 不计入。</li>
            <li>改动条目关键词命中：官方条目 + 组内别名表，对正文做不区分大小写的包含匹配，仅作能见度参考。</li>
          </ul>
        </div>
      </section>
    </template>
  </main>
</template>
