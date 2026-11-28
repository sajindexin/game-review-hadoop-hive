<script setup>
import GlobalHeader from './components/GlobalHeader.vue'
import SiteFooter from './components/SiteFooter.vue'
</script>

<template>
  <GlobalHeader />
  <router-view />
  <SiteFooter />
</template>
