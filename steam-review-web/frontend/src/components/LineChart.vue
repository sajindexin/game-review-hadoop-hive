<script setup>
import { onMounted, ref, watch } from 'vue'
import { lineChart } from '../lib/chart'

const props = defineProps({
  points: { type: Array, default: () => [] },
})

const host = ref(null)

const draw = () => lineChart(host.value, props.points)

onMounted(draw)
watch(() => props.points, draw)
</script>

<template>
  <div ref="host" class="chart-box"></div>
</template>
