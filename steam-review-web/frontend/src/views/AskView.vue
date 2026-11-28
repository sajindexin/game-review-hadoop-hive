<script setup>
import { computed, nextTick, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import SubNav from '../components/SubNav.vue'
import { askAgent, loadIndex, loadPatch } from '../api'
import { esc, fmtDate, fmtInt } from '../lib/format'
import { localAnswer, pct, pp, renderBackendAnswer } from '../lib/answers'

const route = useRoute()
const router = useRouter()

const index = ref(null)
const data = ref(null)
const loading = ref(true)
const notFound = ref(false)
const busy = ref(false)
const input = ref('')
const messages = ref([])
const chatBody = ref(null)

let seq = 0
const nextId = () => ++seq

/* 顶栏显示的是否走接口模式。构建产物由 server.py 托管，始终同源可用。 */
const useBackend = true

const navItems = [
  { label: '商店', to: '/' },
  { label: '版本更新', to: { path: '/', hash: '#patches' } },
  { label: 'Agent 问答', active: true },
]

const p = computed(() => data.value?.patch || {})
const comments = computed(() => data.value?.comments || [])

const stageStats = computed(() => data.value?.stats?.all || {})

const ctxLine = computed(() => {
  if (!data.value) return '载入数据中…'
  return `${p.value.game}｜${p.value.patch_name}｜窗口 ${fmtDate(p.value.window_start)} — ${fmtDate(
    p.value.window_end
  )}`
})

const ctxRows = computed(() => {
  if (!data.value) return []
  const b = stageStats.value.before || {}
  const a = stageStats.value.after || {}
  return [
    ['版本', p.value.title_zh || p.value.patch_name, true],
    ['发布', fmtDate(p.value.release_date), false],
    ['更新前推荐率', `${pct(b.rate)}（${fmtInt(b.reviews)} 条）`, false],
    ['更新后推荐率', `${pct(a.rate)}（${fmtInt(a.reviews)} 条）`, false],
    ['净变化', pp(data.value.delta_pp), false],
    ['改动条目', `${(p.value.items || []).length} 项`, false],
    ['抽样评测', `${comments.value.length} 条`, false],
  ]
})

const suggestions = computed(() => {
  if (!data.value) return []
  const title = p.value.title_zh || p.value.patch_name
  return [
    `${title} 更新后口碑变化如何？`,
    '中文玩家和英文玩家的评价差多少？',
    '这次更新里哪一项改动被提到最多？',
    '帮我找几条不推荐的评测看看问题在哪。',
    '评论量峰值出现在哪一天？',
    '玩家主要在讨论什么？',
    '更新后哪些方面的负面反馈增加了？',
  ]
})

const patchOptions = computed(() => index.value?.games || [])

/* ------------------------------------------------------------ 消息 */

function pushMessage(role, html) {
  messages.value.push({ id: nextId(), role, html })
  scrollToEnd()
}

async function scrollToEnd() {
  await nextTick()
  const el = chatBody.value
  if (el) el.scrollTop = el.scrollHeight
}

function focusLastBot() {
  nextTick(() => {
    const el = chatBody.value
    if (!el) return
    const nodes = el.querySelectorAll('.msg.bot')
    const last = nodes[nodes.length - 1]
    if (!last) return
    const top = last.getBoundingClientRect().top - el.getBoundingClientRect().top + el.scrollTop
    el.scrollTop = Math.max(0, top - 8)
  })
}

async function ask(text) {
  const q = (text || '').trim()
  if (!q || busy.value || !data.value) return

  busy.value = true
  input.value = ''
  pushMessage('user', esc(q))
  await scrollToEnd()

  let html
  try {
    if (useBackend) {
      const json = await askAgent(data.value.patch.patch_id, q)
      html = renderBackendAnswer(json, data.value)
    } else {
      html = localAnswer(q, data.value)
    }
  } catch (e) {
    html = `<p>问答接口暂时不可用（${esc(e.message)}），已回退到本地规则引擎。</p>` + localAnswer(q, data.value)
  }

  await new Promise((r) => setTimeout(r, 260))
  pushMessage('bot', html)
  focusLastBot()
  busy.value = false
}

function onSubmit() {
  ask(input.value)
}

function switchPatch(id) {
  router.push({ path: '/ask', query: { patch: id } })
}

/* ------------------------------------------------------------ 加载 */

async function boot() {
  loading.value = true
  notFound.value = false
  messages.value = []

  try {
    index.value = await loadIndex()
  } catch (e) {
    index.value = null
  }

  let id = route.query.patch
  if (!id) {
    const g0 = index.value?.games?.[0]
    id = g0 ? g0.patches[0].patch_id : ''
  }
  if (!id) {
    notFound.value = true
    loading.value = false
    return
  }

  try {
    data.value = await loadPatch(id)
  } catch (e) {
    notFound.value = true
    loading.value = false
    return
  }
  loading.value = false

  document.title = `${p.value.game} · Agent 问答 · Steam 评测分析台`

  pushMessage(
    'bot',
    `<p>已载入 <b>${esc(p.value.game)} · ${esc(p.value.title_zh || p.value.patch_name)}</b> 的版本窗口数据。</p>
     <p>我可以回答推荐率变化、分语种差异、改动条目讨论度，也能查询 NLP 讨论主题与负面反馈变化，并检索已复核的评测原文。下方示例问题可直接点。</p>`
  )

  const q = route.query.q
  if (q) setTimeout(() => ask(q), 320)
}

onMounted(boot)
</script>

<template>
  <SubNav :items="navItems" meta="本地数据包 · 4,884,580 条评测" />

  <main class="wrap">
    <div class="breadcrumbs">
      <router-link to="/">全部游戏</router-link><span class="sep">›</span><span>Agent 问答</span>
    </div>

    <div v-if="notFound" class="empty" style="margin-top: 40px">
      找不到版本数据「{{ route.query.patch }}」，<router-link to="/">返回总览</router-link>
    </div>

    <template v-else>
      <div class="flex" style="margin: 4px 0 14px">
        <div>
          <div style="font-size: 21px; color: #e6edf3">评测问答 Agent</div>
          <div class="small muted">{{ ctxLine }}</div>
        </div>
        <div class="right">
          <select
            class="chip"
            style="
              height: 30px;
              background: #16202d;
              border-color: #2a475e;
              color: #c6d4df;
              padding: 0 8px;
              max-width: 360px;
            "
            :value="p.patch_id"
            @change="switchPatch($event.target.value)"
          >
            <optgroup v-for="g in patchOptions" :key="g.appid" :label="g.name">
              <option v-for="pt in g.patches" :key="pt.patch_id" :value="pt.patch_id">
                {{ pt.title_zh || pt.patch_name }}（{{ pt.release_date }}）
              </option>
            </optgroup>
          </select>
        </div>
      </div>

      <div class="chat-shell">
        <div class="chat-panel">
          <div class="chat-head">
            <span class="dot"></span>
            <div>
              <div class="t">Review Analyst</div>
              <div class="s">基于版本窗口统计 + 评测样本回答，回答中会标注数据口径</div>
            </div>
            <span class="right">{{ useBackend ? '接口模式（/api/ask）' : '本地规则引擎' }}</span>
          </div>

          <div ref="chatBody" class="chat-body">
            <div v-if="loading" class="empty">正在载入版本数据…</div>

            <div v-for="m in messages" :key="m.id" class="msg" :class="m.role">
              <div class="who">{{ m.role === 'user' ? '你' : 'AI' }}</div>
              <!-- 内容来自自己的后端 / 本地数据包，与原实现一致用 HTML 渲染 -->
              <div class="bubble" v-html="m.html"></div>
            </div>

            <div v-if="busy" class="msg bot">
              <div class="who">AI</div>
              <div class="bubble"><span class="muted">正在检索窗口统计与评测样本…</span></div>
            </div>
          </div>

          <form class="chat-input" @submit.prevent="onSubmit">
            <input
              v-model="input"
              type="text"
              placeholder="例如：这次更新的口碑变化如何？中文玩家怎么看？"
              autocomplete="off"
            />
            <button class="btn btn-green" type="submit" :disabled="busy">发送</button>
          </form>
        </div>

        <aside>
          <div class="side-card">
            <h3>示例问题</h3>
            <div>
              <button v-for="q in suggestions" :key="q" class="q" @click="ask(q)">{{ q }}</button>
            </div>
          </div>

          <div class="side-card">
            <h3>当前上下文</h3>
            <div class="small" style="line-height: 1.9">
              <div v-for="row in ctxRows" :key="row[0]">
                {{ row[0] }}：<b v-if="row[2]" style="color: #fff">{{ row[1] }}</b
                ><template v-else>{{ row[1] }}</template>
              </div>
            </div>
          </div>

          <div class="side-card">
            <h3>接口约定</h3>
            <p>
              页面通过 <code>POST /api/ask</code> 获取回答，入参
              <code>{ patch_id, question }</code>；接口不可用时自动回退本地规则引擎。
            </p>
          </div>
        </aside>
      </div>
    </template>
  </main>
</template>
