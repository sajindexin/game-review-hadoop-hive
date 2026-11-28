<script setup>
import { computed } from 'vue'
import { avatarStyle, avatarText } from '../lib/art'
import { fmtInt, fmtTime, langName, reviewUrl } from '../lib/format'

const props = defineProps({
  comment: { type: Object, required: true },
  appid: { type: [String, Number], default: '' },
  items: { type: Array, default: () => [] },
})

const PHASE = { before: '更新前', release: '更新日', after: '更新后' }

const phaseName = computed(() => PHASE[props.comment.phase] || '')

const hitTitles = computed(() =>
  (props.comment.items || []).map((id) => (props.items.find((i) => i.item_id === id) || {}).title || id)
)

const srcUrl = computed(() => props.comment.url || reviewUrl(props.comment, props.appid))

const avatar = computed(() => avatarStyle(props.comment.review_id))
</script>

<template>
  <article class="review">
    <div class="avatar" :style="avatar">{{ avatarText(comment.review_id) }}</div>
    <div>
      <div class="head">
        <span class="name">评测者 #{{ String(comment.review_id).slice(-4) }}</span>
        <span class="verdict" :class="comment.voted_up ? 'up' : 'down'">
          {{ comment.voted_up ? '👍 推荐' : '👎 不推荐' }}
        </span>
        <span class="tag gray">{{ langName(comment.language) }}</span>
        <span class="tag gray">{{ phaseName }}</span>
        <span class="right muted">{{ fmtTime(comment.created_at) }}</span>
      </div>
      <div class="body">{{ comment.text }}</div>
      <div class="foot">
        <span>游玩 {{ comment.playtime_hours }} 小时</span>
        <span class="helpful">👍 {{ fmtInt(comment.votes_up) }} 人觉得有用</span>
        <span v-if="comment.votes_funny">😄 {{ fmtInt(comment.votes_funny) }}</span>
        <span v-for="t in hitTitles" :key="t" class="tag">{{ t }}</span>
        <a v-if="srcUrl" class="src-link" :href="srcUrl" target="_blank" rel="noopener">
          在 Steam 打开原文 →
        </a>
        <span v-else class="src-link muted">原文链接不可用</span>
      </div>
    </div>
  </article>
</template>
