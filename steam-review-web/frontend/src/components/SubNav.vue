<script setup>
/* 二级导航：各页面自带的横向链接条
 * items: [{ label, to?, href?, active? }]
 * meta:  右侧的补充说明
 */
defineProps({
  items: { type: Array, default: () => [] },
  meta: { type: String, default: '' },
})
</script>

<template>
  <div class="sub-nav">
    <div class="wrap">
      <template v-for="(item, i) in items" :key="i">
        <router-link v-if="item.to" :to="item.to" :class="item.active ? 'active' : ''">
          {{ item.label }}
        </router-link>
        <a v-else :href="item.href || '#'" :class="item.active ? 'active' : ''">{{ item.label }}</a>
      </template>
      <template v-if="meta">
        <span class="spacer"></span>
        <span class="meta">{{ meta }}</span>
      </template>
    </div>
  </div>
</template>
