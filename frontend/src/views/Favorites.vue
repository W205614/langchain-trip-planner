<template><main class="page"><a-card class="glass-card card" :bordered="false">
  <header><a-button @click="router.push('/explore')">← 景点发现</a-button><h1>我的收藏</h1><a-button type="primary" :disabled="!selected.length" @click="build">用所选景点创建行程</a-button></header>
  <a-alert v-if="error" type="error" :message="error" show-icon/><a-spin :spinning="loading">
  <a-checkbox-group v-model:value="selected" class="grid"><a-card v-for="item in items" :key="item.poi_id" class="item"><a-checkbox :value="item.poi_id"><strong>{{item.name}}</strong></a-checkbox><p>{{item.city}} · {{item.address}}</p><a-button danger type="link" @click.stop="remove(item.poi_id)">取消收藏</a-button></a-card></a-checkbox-group>
  <a-empty v-if="!loading&&!items.length" description="还没有收藏景点"/></a-spin>
</a-card></main></template>
<script setup lang="ts">import{ref,onMounted}from'vue';import{useRouter}from'vue-router';import{message}from'ant-design-vue';import{fetchFavorites,deleteFavorite}from'@/services/api'
const router=useRouter(),items=ref<any[]>([]),selected=ref<string[]>([]),loading=ref(false),error=ref('')
async function load(){loading.value=true;try{items.value=(await fetchFavorites()).data||[]}catch(e:any){error.value=e?.response?.data?.message||'读取收藏失败'}finally{loading.value=false}}
async function remove(id:string){await deleteFavorite(id);selected.value=selected.value.filter(v=>v!==id);await load();message.success('已取消收藏')}
function build(){sessionStorage.setItem('manualFavoritePois',JSON.stringify(items.value.filter(v=>selected.value.includes(v.poi_id))));router.push('/trips/new')}
onMounted(load);
</script>
<style scoped>.page{padding:36px;min-height:100vh}.card{max-width:1050px;margin:auto;border-radius:22px}header{display:flex;justify-content:space-between;align-items:center;margin-bottom:24px}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:16px;width:100%}.item{min-height:150px}@media(max-width:700px){.page{padding:12px}header{align-items:flex-start;flex-direction:column;gap:12px}}</style>
