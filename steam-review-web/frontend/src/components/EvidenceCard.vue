<script setup>
import { computed } from 'vue'
import { fmtTime, langName, reviewUrl } from '../lib/format'

const props = defineProps({
  evidence: { type: Object, required: true },
  appid: { type: [String, Number], default: '' },
})

const PHASE = { before: '更新前', release: '发布当天', after: '更新后' }

const phaseText = computed(() => PHASE[props.evidence.phase] || '')

const srcUrl = computed(() => props.evidence.url || reviewUrl(props.evidence, props.appid))

const anchorId = computed(() => `quote-${props.evidence.item_id}-${props.evidence.review_id}`)
</script>

<template>
  <div :id="anchorId" class="evi">
    <div class="evi-head">
      <span class="verified-badge" :title="evidence.verification || '已复核'">已复核</span>
      <span class="verdict" :class="evidence.voted_up ? 'up' : 'down'">
        {{ evidence.voted_up ? '👍 推荐' : '👎 不推荐' }}
      </span>
      <span class="tag gray">{{ langName(evidence.language_std) }}</span>
      <span class="tag gray">{{ phaseText }}</span>
      <span class="muted">{{ fmtTime(evidence.created_at) }}</span>
      <span class="muted">评测 #{{ evidence.review_id }}</span>
      <span class="right">
        <a v-if="srcUrl" class="src-link" :href="srcUrl" target="_blank" rel="noopener">
          在 Steam 打开原文 →
        </a>
        <span v-else class="src-link muted">原文链接不可用</span>
      </span>
    </div>
    <div class="evi-body">{{ evidence.evidence }}</div>
  </div>
</template>
