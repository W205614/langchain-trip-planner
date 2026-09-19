<template>
  <main class="admin-page"><section class="panel glass-card">
    <header><a-button @click="router.push('/')">← 首页</a-button><div><h1>社区行程审核</h1><p>审核的是提交时的不可变行程快照。</p></div><a-button :loading="loading" @click="load">刷新</a-button></header>
    <a-table :data-source="rows" :loading="loading" row-key="id" :pagination="false">
      <a-table-column title="投稿" data-index="id" />
      <a-table-column title="标题"><template #default="{record}"><b>{{ record.title }}</b><div>{{ record.city }} · {{ record.author }} · v{{ record.record_version }}</div></template></a-table-column>
      <a-table-column title="提交时间" data-index="created_at" />
      <a-table-column title="操作"><template #default="{record}"><a-space><a-button type="primary" @click="review(record.id,'approve')">通过</a-button><a-button danger @click="review(record.id,'reject')">拒绝</a-button></a-space></template></a-table-column>
      <template #expandedRowRender="{record}">
        <div class="snapshot-facts">{{ record.snapshot.start_date }} ~ {{ record.snapshot.end_date }} · {{ record.snapshot.travel_days }} 天 · {{ record.snapshot.transportation }}</div>
        <div v-for="day in (record.snapshot.plan?.days || [])" :key="day.day_index" class="snapshot-day">
          <b>Day {{ day.day_index + 1 }} {{ day.theme || '' }}</b>
          <span>{{ (day.attractions || []).map((item:any)=>item.name).join(' → ') || '无景点' }}</span>
        </div>
      </template>
    </a-table>
    <a-empty v-if="!loading&&!rows.length" description="没有待审核投稿" />
  </section></main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { fetchCommunityReviewQueue, reviewCommunityCard } from '@/services/api'
const router=useRouter(); const rows=ref<any[]>([]); const loading=ref(false)
async function load(){loading.value=true;try{const result=await fetchCommunityReviewQueue();rows.value=result.data||[]}catch(e:any){message.error(e.response?.data?.detail||'读取审核队列失败')}finally{loading.value=false}}
async function review(id:number,decision:'approve'|'reject'){try{await reviewCommunityCard(id,decision);message.success(decision==='approve'?'已发布':'已拒绝');await load()}catch(e:any){message.error(e.response?.data?.detail||'审核失败')}}
onMounted(load)
</script>

<style scoped>
.admin-page{min-height:100vh;padding:40px 24px}.panel{max-width:1080px;margin:auto;border-radius:24px;padding:30px}.panel header{display:grid;grid-template-columns:130px 1fr 100px;align-items:center;gap:20px;margin-bottom:24px}.panel h1{margin:0;color:#111827}.panel p{margin:6px 0 0;color:#667085}.snapshot-facts{font-weight:700;margin-bottom:8px}.snapshot-day{display:grid;grid-template-columns:180px 1fr;gap:12px;margin:5px 0;color:#475467}@media(max-width:700px){.admin-page{padding:12px}.panel header{grid-template-columns:1fr}.snapshot-day{grid-template-columns:1fr}}
</style>
