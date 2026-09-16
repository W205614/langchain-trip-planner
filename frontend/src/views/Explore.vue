<template>
  <main class="page"><a-card class="glass-card card" :bordered="false">
    <header><a-button @click="router.push('/')">← 首页</a-button><div><h1>景点发现</h1><p>普通搜索由 Java 直接访问高德 REST，不调用大模型。</p></div><a-button v-if="logged" @click="router.push('/favorites')">我的收藏</a-button></header>
    <a-space wrap class="search"><a-input v-model:value="city" placeholder="城市" style="width:140px" />
      <a-select v-model:value="category" style="width:140px"><a-select-option value="">全部类别</a-select-option><a-select-option value="博物馆">博物馆</a-select-option><a-select-option value="公园">公园</a-select-option><a-select-option value="历史文化">历史文化</a-select-option></a-select>
      <a-input-search v-model:value="keyword" placeholder="输入景点名称" enter-button="搜索" :loading="loading" style="width:360px" @search="search" /></a-space>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-empty v-if="searched&&!loading&&!results.length" description="没有找到可信景点" />
    <a-row :gutter="16"><a-col v-for="poi in results" :key="poi.id" :xs="24" :md="12" :lg="8">
      <a-card class="poi" :title="poi.name"><p>{{ poi.type || '景点' }}</p><p>{{ poi.address || '地址待确认' }}</p><p>{{ poi.opening_hours || '营业时间待确认' }}</p>
        <template #actions><a-button type="link" :disabled="saving===poi.id" @click="favorite(poi.id)">{{ logged?'收藏':'登录后收藏' }}</a-button></template>
      </a-card></a-col></a-row>
  </a-card></main>
</template>
<script setup lang="ts">
import { ref } from 'vue'; import { useRouter } from 'vue-router'; import { message } from 'ant-design-vue'
import { searchAttractionPOIs } from '@/services/map'; import { addFavorite } from '@/services/api'; import { isAuthenticated } from '@/services/auth'; import type { POIInfo } from '@/types'
const router=useRouter(),logged=isAuthenticated(); const city=ref('北京'),keyword=ref('景点'),category=ref(''),results=ref<POIInfo[]>([]),loading=ref(false),searched=ref(false),error=ref(''),saving=ref('')
async function search(){loading.value=true;error.value='';try{results.value=await searchAttractionPOIs(city.value,`${keyword.value} ${category.value}`.trim());searched.value=true}catch(e:any){error.value=e?.response?.data?.message||'搜索失败'}finally{loading.value=false}}
async function favorite(id:string){if(!logged){router.push('/login');return} saving.value=id;try{await addFavorite(id);message.success('已收藏')}catch(e:any){message.error(e?.response?.data?.message||'收藏失败')}finally{saving.value=''}}
search()
</script>
<style scoped>.page{padding:36px;min-height:100vh}.card{max-width:1180px;margin:auto;border-radius:22px}header{display:flex;align-items:center;justify-content:space-between;gap:20px}h1{margin:0}.search{margin:24px 0}.poi{margin-bottom:16px;min-height:220px}@media(max-width:700px){.page{padding:12px}header{align-items:flex-start;flex-direction:column}}</style>
