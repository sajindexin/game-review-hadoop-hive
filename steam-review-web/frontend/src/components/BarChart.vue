<script setup>
import { onMounted, ref, watch } from 'vue'
import { barChart } from '../lib/chart'

const props = defineProps({
  points: { type: Array, default: () => [] },
})

const host = ref(null)

const draw = () => barChart(host.value, props.points)

onMounted(draw)
watch(() => props.points, draw)
</script>

<template>
  <div ref="host" class="chart-box"></div>
</template>
