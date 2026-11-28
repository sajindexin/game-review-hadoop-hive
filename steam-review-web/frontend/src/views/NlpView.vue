<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import SubNav from '../components/SubNav.vue'
import { loadIndex, loadNlpItems, loadNlpTopics } from '../api'
import { fmtDate, fmtInt } from '../lib/format'

const route = useRoute()

const T = ref(null)
const I = ref(null)
const INDEX = ref(null)
const loading = ref(true)
const error = ref('')

const game = ref('')
const patch = ref('')
const lang = ref('all')
const phase = ref('all')
const topicSort = ref('total')
const itemSort = ref('total')

const LANGS = ['zh', 'en']
const LANG_CN = { zh: '中文', en: '英文', all: '中英合计' }
const PHASE_CN = { all: '全部阶段', before: '更新前 14 天', release: '发布当天', after: '更新后 14 天' }

const PHASES = [
  ['all', '全部'],
  ['before', '更新前 14 天'],
  ['release', '发布当天'],
  ['after', '更新后 14 天'],
]

const blank = () => ({ positive: 0, negative: 0, neutral: 0 })
const totalOf = (v) => (v.positive || 0) + (v.negative || 0) + (v.neutral || 0)

onMounted(async () => {
  try {
    const [topics, items, index] = await Promise.all([loadNlpTopics(), loadNlpItems(), loadIndex()])
    T.value = topics
    I.value = items
    INDEX.value = index
  } catch (e) {
    error.value = e.message
    loading.value = false
    return
  }

  // 深链 ?patch= / ?game= / ?lang= / ?phase=
  const qp = route.query.patch
  if (qp && T.value.patch_meta[qp]) {
    patch.value = qp
    game.value = T.value.patch_meta[qp].appid
  }
  const qg = route.query.game
  if (qg && INDEX.value.games.some((x) => x.appid === qg)) game.value = qg
  const ql = route.query.lang
  if (ql && ['all', 'zh', 'en'].includes(ql)) lang.value = ql
  const qph = route.query.phase
  if (qph && ['all', 'before', 'release', 'after'].includes(qph)) phase.value = qph

  loading.value = false
})

/* ------------------------------------------------------------ 切片计算 */

const langsToUse = computed(() => (lang.value === 'all' ? LANGS : [lang.value]))

function merge(into, from) {
  if (!from) return into
  into.positive += from.positive || 0
  into.negative += from.negative || 0
  into.neutral += from.neutral || 0
  return into
}

/* 选定具体版本 → 窗口内；否则按游戏或全局 → 全时间线 */
const sliceTopics = computed(() => {
  const out = {}
  if (!T.value) return out
  const langs = langsToUse.value

  if (patch.value) {
    const bucketAll = T.value.by_patch[patch.value] || {}
    for (const lg of langs) {
      const bucket = (bucketAll[lg] || {})[phase.value] || {}
      for (const a of Object.keys(bucket)) out[a] = merge(out[a] || blank(), bucket[a])
    }
    return out
  }

  if (game.value) {
    const g = T.value.by_game[game.value] || {}
    for (const lg of langs) {
      const bucket = g[lg] || {}
      for (const a of Object.keys(bucket)) out[a] = merge(out[a] || blank(), bucket[a])
    }
    return out
  }

  for (const lg of langs) {
    const bucket = T.value.global[lg] || {}
    for (const a of Object.keys(bucket)) out[a] = merge(out[a] || blank(), bucket[a])
  }
  return out
})

const totalHits = computed(() =>
  Object.values(sliceTopics.value).reduce((s, v) => s + totalOf(v), 0)
)

const heroStat = computed(() => {
  let pos = 0
  let neg = 0
  let neu = 0
  for (const v of Object.values(sliceTopics.value)) {
    pos += v.positive
    neg += v.negative
    neu += v.neutral
  }
  const all = pos + neg + neu
  const pct = (n) => (all ? ((n / all) * 100).toFixed(1) : '0.0')
  return { all, pos, neg, neu, posPct: pct(pos), negPct: pct(neg), neuPct: pct(neu) }
})

const scopeText = computed(() => {
  if (!T.value) return ''
  if (patch.value) {
    const m = T.value.patch_meta[patch.value]
    const phaseTxt = phase.value === 'all' ? '窗口全段' : PHASE_CN[phase.value]
    return `${m.game} · ${m.title_zh || m.patch_name}｜${phaseTxt}（${m.window_start} ~ ${m.window_end}）`
  }
  if (game.value) {
    const g = INDEX.value?.games.find((x) => x.appid === game.value)
    return `${g ? g.name : game.value}｜全时间线（不限版本窗口）`
  }
  return '全部 12 款游戏｜全时间线（不限版本窗口）'
})

/* ------------------------------------------------------------ 主题分布 */

const MIN_N = 10

const topicRows = computed(() => {
  if (!T.value) return []
  const dist = sliceTopics.value
  const rows = T.value.aspects
    .map((a) => {
      const v = dist[a.id] || blank()
      const total = totalOf(v)
      const negRate = total ? v.negative / total : 0
      return { ...a, ...v, total, negRate, thin: total < MIN_N }
    })
    .filter((r) => r.total > 0)

  rows.sort((x, y) => {
    if (topicSort.value === 'neg') {
      // 样本不足的一律排后面，避免「3 条里 1 条差评 = 33%」占据前排
      if (x.thin !== y.thin) return x.thin ? 1 : -1
      return y.negRate - x.negRate || y.total - x.total
    }
    return y.total - x.total
  })
  return rows
})

const topicMax = computed(() => (topicRows.value.length ? topicRows.value[0].total : 1))

const topicSubText = computed(() => {
  const grand = topicRows.value.reduce((s, r) => s + r.total, 0)
  return `${topicRows.value.length} 类主题有命中 · 共 ${fmtInt(grand)} 条`
})

/* ------------------------------------------------------------ 官方改动 */

const itemRows = computed(() => {
  if (!I.value) return []
  let rows = (I.value.items || []).slice()
  if (game.value) rows = rows.filter((x) => x.appid === game.value)
  if (patch.value) rows = rows.filter((x) => x.patch_id === patch.value)

  rows.sort((x, y) => {
    if (itemSort.value === 'neg') return y.sentiment.negative - x.sentiment.negative || y.total - x.total
    if (itemSort.value === 'pos') return y.sentiment.positive - x.sentiment.positive || y.total - x.total
    return y.total - x.total
  })
  return rows
})

const itemSubText = computed(() => {
  const hit = itemRows.value.filter((r) => r.total > 0).length
  return `${itemRows.value.length} 项在范围内 · ${hit} 项有评论命中`
})

const itemTotalAll = computed(() => (I.value ? I.value.items.reduce((s, x) => s + x.total, 0) : 0))

/* 标题随筛选变化：全量时是 75 项，选定版本后是本版本项数 */
const itemTitle = computed(() => {
  if (!I.value) return '官方改动的反馈统计'
  if (game.value || patch.value) return `${itemRows.value.length} 项官方改动的反馈统计`
  return `${I.value.total_items || I.value.items.length} 项官方改动的反馈统计`
})

const windowHits = (r) =>
  (r.bucket.pre_14d || 0) + (r.bucket.release_day || 0) + (r.bucket.post_14d || 0)

/* ------------------------------------------------------------ 下拉联动 */

const gameOptions = computed(() => INDEX.value?.games || [])

const patchOptions = computed(() => {
  if (!T.value) return []
  const list = []
  for (const [pid, m] of Object.entries(T.value.patch_meta)) {
    if (game.value && m.appid !== game.value) continue
    list.push({ pid, ...m })
  }
  list.sort((a, b) => (a.release_date < b.release_date ? 1 : -1))
  return list
})

watch(game, () => {
  if (patch.value && !patchOptions.value.some((x) => x.pid === patch.value)) patch.value = ''
})

watch(patch, (v) => {
  if (!v || !T.value) return
  const m = T.value.patch_meta[v]
  if (m && !game.value) game.value = m.appid
})

const resetDisabled = computed(
  () => !game.value && !patch.value && lang.value === 'all' && phase.value === 'all'
)

function reset() {
  game.value = ''
  patch.value = ''
  lang.value = 'all'
  phase.value = 'all'
}

/* ------------------------------------------------------------ 口径说明 */

const methodSources = computed(() => T.value?.sources || { zh: 0, en: 0 })

const navItems = [
  { label: '主题情感', href: '#', active: true },
  { label: '官方改动', href: '#itemTable' },
  { label: '口径说明', href: '#method' },
  { label: '回版本页', to: '/' },
]
</script>

<template>
  <SubNav :items="navItems" meta="数据更新于 2026.11.27" />

  <main class="wrap">
    <div v-if="error" class="empty" style="margin-top: 40px">NLP 数据加载失败：{{ error }}</div>

    <template v-else-if="!loading && T && I">
      <div class="nlp-hero">
        <div class="nlp-hero-txt">
          <div class="kicker">自然语言处理 · 两层标签体系</div>
          <h1>11 类通用主题 × 75 项官方改动</h1>
          <p>
            第一层把每条评论拆到 11 类通用主题，标注正 / 负 /
            中性；第二层把同一批评论关联到 75 项官方改动。
            两个层级都支持按游戏、版本、语种、更新阶段筛选。
          </p>
        </div>
        <div class="nlp-hero-stat">
          <div class="hs-main">
            <div class="hs-num">{{ fmtInt(heroStat.all) }}</div>
            <div class="hs-label">主题命中条数</div>
          </div>
          <div class="hs-split">
            <span class="pos"><b>{{ heroStat.posPct }}%</b>正向 {{ fmtInt(heroStat.pos) }}</span>
            <span class="neg"><b>{{ heroStat.negPct }}%</b>负向 {{ fmtInt(heroStat.neg) }}</span>
            <span class="neu"><b>{{ heroStat.neuPct }}%</b>中性 {{ fmtInt(heroStat.neu) }}</span>
          </div>
        </div>
      </div>

      <!-- 筛选工具条 -->
      <section class="nlp-filter">
        <div class="filter-row">
          <span class="filter-label">游戏</span>
          <select v-model="game">
            <option value="">全部游戏</option>
            <option v-for="g in gameOptions" :key="g.appid" :value="g.appid">{{ g.name }}</option>
          </select>

          <span class="filter-label">版本</span>
          <select v-model="patch">
            <option value="">全部版本（全时间线）</option>
            <option v-for="m in patchOptions" :key="m.pid" :value="m.pid">
              {{ m.game }} · {{ m.title_zh || m.patch_name }}（{{ fmtDate(m.release_date) }}）
            </option>
          </select>

          <span class="filter-label">语种</span>
          <div id="fLang" class="seg-group">
            <button
              v-for="opt in [
                ['all', '全部'],
                ['zh', '中文'],
                ['en', '英文'],
              ]"
              :key="opt[0]"
              class="chip"
              :class="{ on: lang === opt[0] }"
              :data-lang="opt[0]"
              @click="lang = opt[0]"
            >
              {{ opt[1] }}
            </button>
          </div>

          <span class="filter-label">更新阶段</span>
          <div id="fPhase" class="seg-group">
            <button
              v-for="opt in PHASES"
              :key="opt[0]"
              class="chip"
              :class="{ on: phase === opt[0], dim: !patch }"
              :data-phase="opt[0]"
              :disabled="!patch"
              @click="patch && (phase = opt[0])"
            >
              {{ opt[1] }}
            </button>
          </div>

          <button v-if="!resetDisabled" class="chip chip-clear" @click="reset">重置</button>
        </div>
        <div id="scopeNote" class="nlp-scope">
          <span class="scope-label">当前口径</span>{{ scopeText }}
          <span class="scope-sep"></span>语种：{{ LANG_CN[lang] }}
          <span class="scope-sep"></span>命中 {{ fmtInt(totalHits) }} 条
        </div>
      </section>

      <!-- 第一层 -->
      <div class="section-head">
        <h2>11 类通用主题的情感分布</h2>
        <span class="sub">{{ topicSubText }}</span>
        <span class="right">
          排序
          <button
            class="chip"
            :class="{ on: topicSort === 'total' }"
            data-sort="total"
            @click="topicSort = 'total'"
          >
            提及量
          </button>
          <button class="chip" :class="{ on: topicSort === 'neg' }" data-sort="neg" @click="topicSort = 'neg'">
            负向率
          </button>
        </span>
      </div>

      <div class="panel">
        <div class="topic-head">
          <span>主题</span>
          <span>正 / 负 / 中性构成</span>
          <span class="th-nums"><i>合计</i><i>正</i><i>负</i><i>中</i><i>负向率</i></span>
        </div>
        <div class="topic-bars">
          <div v-if="!topicRows.length" class="empty">当前筛选下没有主题命中，试试放宽版本或阶段</div>
          <div v-for="r in topicRows" :key="r.id" class="topic-row">
            <div class="topic-name" :title="r.id">{{ r.name_zh }}</div>
            <div class="topic-bar-wrap">
              <div class="topic-bar" :style="{ width: ((r.total / topicMax) * 100).toFixed(2) + '%' }">
                <span
                  v-if="r.positive > 0"
                  class="seg pos"
                  :style="{ width: ((r.positive / r.total) * 100).toFixed(2) + '%' }"
                  :title="'正向 ' + r.positive"
                ></span>
                <span
                  v-if="r.negative > 0"
                  class="seg neg"
                  :style="{ width: ((r.negative / r.total) * 100).toFixed(2) + '%' }"
                  :title="'负向 ' + r.negative"
                ></span>
                <span
                  v-if="r.neutral > 0"
                  class="seg neu"
                  :style="{ width: ((r.neutral / r.total) * 100).toFixed(2) + '%' }"
                  :title="'中性 ' + r.neutral"
                ></span>
              </div>
            </div>
            <div class="topic-nums">
              <span class="n-total">{{ fmtInt(r.total) }}</span>
              <span class="pos">{{ fmtInt(r.positive) }}</span>
              <span class="neg">{{ fmtInt(r.negative) }}</span>
              <span class="neu">{{ fmtInt(r.neutral) }}</span>
              <span
                class="n-rate"
                :class="{ hot: r.negRate >= 0.5 && !r.thin, thin: r.thin }"
                :title="r.thin ? `样本仅 ${r.total} 条，负向率不稳定` : '负向率'"
              >
                {{ (r.negRate * 100).toFixed(0) }}%
              </span>
            </div>
          </div>
        </div>
        <div class="topic-legend">
          <span><i class="dot pos"></i>正向</span>
          <span><i class="dot neg"></i>负向</span>
          <span><i class="dot neu"></i>中性</span>
          <span class="muted">
            条形长度按提及量相对最大主题缩放；末列为负向率（负向 ÷
            合计），样本不足 10 条的主题按负向率排序时排末位并以斜体标注
          </span>
        </div>
      </div>

      <!-- 第二层 -->
      <div class="section-head">
        <h2>{{ itemTitle }}</h2>
        <span class="sub">{{ itemSubText }}</span>
        <span class="right">
          <button
            class="chip"
            :class="{ on: itemSort === 'total' }"
            data-isort="total"
            @click="itemSort = 'total'"
          >
            提及量
          </button>
          <button class="chip" :class="{ on: itemSort === 'neg' }" data-isort="neg" @click="itemSort = 'neg'">
            负向数
          </button>
          <button class="chip" :class="{ on: itemSort === 'pos' }" data-isort="pos" @click="itemSort = 'pos'">
            正向数
          </button>
        </span>
      </div>

      <div class="panel">
        <div class="nlp-note">
          条目情感统计合计 <b>{{ fmtInt(itemTotalAll) }}</b> 条，以中文评论为主（1,585 / 1,593，英文仅 8
          条），因此<b>不提供语种细分</b>；「窗口内」指命中该条目的评论落在其所属版本的前后 14 天窗口内。
        </div>
        <div class="table-scroll">
          <table id="itemTable" class="table">
            <thead>
              <tr>
                <th style="width: 38px">#</th>
                <th>官方改动</th>
                <th>游戏 / 版本</th>
                <th class="num">正向</th>
                <th class="num">负向</th>
                <th class="num">中性</th>
                <th class="num">合计</th>
                <th class="num">窗口内</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!itemRows.length">
                <td colspan="8"><div class="empty">当前筛选下没有官方改动</div></td>
              </tr>
              <tr v-for="(r, i) in itemRows" :key="r.item_id" :class="{ 'row-zero': r.total === 0 }">
                <td class="muted">{{ i + 1 }}</td>
                <td>
                  <span class="item-name">{{ r.title }}</span>
                  <span class="item-id">{{ r.item_id }}</span>
                </td>
                <td class="muted small">
                  {{ r.game }}<br />
                  <span class="muted">{{ r.patch_title_zh || r.patch_name }} · {{ fmtDate(r.release_date) }}</span>
                </td>
                <td class="num pos">{{ r.total === 0 ? '—' : fmtInt(r.sentiment.positive) }}</td>
                <td class="num neg">{{ r.total === 0 ? '—' : fmtInt(r.sentiment.negative) }}</td>
                <td class="num neu">{{ r.total === 0 ? '—' : fmtInt(r.sentiment.neutral) }}</td>
                <td class="num strong">{{ r.total === 0 ? '0' : fmtInt(r.total) }}</td>
                <td class="num muted">{{ windowHits(r) || '0' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- 口径说明 -->
      <section id="method" class="panel">
        <h3 class="panel-title">口径与方法</h3>
        <div class="nlp-method">
          <ul class="method-list">
            <li>
              <b>第一层（通用主题）</b>：11 类主题为
              <code
                >content / level_design / multiplayer / graphics / performance / story / controls / monetization /
                cheating / audio / community</code
              >，一条评论可命中多个主题，逐条标注 positive / negative / neutral 并保留证据子句。
            </li>
            <li>
              <b>第二层（官方改动）</b>：把同一批评论按官方别名匹配到 75 项改动，窗口按<b>条目自身所属版本</b>计算（pre_14d
              / release_day / post_14d），不按评论日期去猜同游戏其它版本。
            </li>
            <li>
              <b>语种</b>：中文语料 {{ fmtInt(methodSources.zh) }} 行、英文语料
              {{ fmtInt(methodSources.en) }} 行，分两套规则词表分别标注； 英文结果不覆盖俄语、日语等其它语种。
            </li>
            <li>
              <b>阶段口径</b>：选定具体版本时按该版本窗口切分；不选版本时展示全时间线（2010–2026），两者不可直接对比样本量。
            </li>
            <li>
              <b>中性偏多</b>：规则标注要求命中情感词才判正/负，只提到主题但无明确褒贬的评论记为中性，因此中性占比天然偏高。
            </li>
          </ul>
        </div>
      </section>
    </template>

    <div v-else class="empty" style="margin-top: 40px">正在加载 NLP 数据…</div>
  </main>
</template>
