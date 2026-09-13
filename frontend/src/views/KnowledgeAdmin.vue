<template>
  <main class="page-shell"><section class="panel">
    <a-button type="link" @click="router.push('/')">← 返回首页</a-button><h1>公共知识审核</h1>
    <a-alert show-icon type="info" message="先解析，再对照原件复核后确认发布。" />
    <a-list :data-source="items" bordered class="list"><template #renderItem="{ item }"><a-list-item>
      <div><b>{{ item.title }}</b> · {{ item.city }}<br><small>{{ item.original_filename }} · 状态：{{ item.status }} · 来源等级：{{ item.source_tier }} {{ item.review_note }}</small></div>
      <template #actions><a-button @click="reviewId = item.id">查看与复核</a-button><a-select v-if="item.status !== 'published'" v-model:value="item.source_tier" size="small" style="width: 112px"><a-select-option value="community">投稿资料</a-select-option><a-select-option value="reviewed">人工核验</a-select-option><a-select-option value="official">官方资料</a-select-option></a-select><a-button v-if="['pending','rejected','failed'].includes(item.status)" type="primary" size="small" @click="approve(item)">开始解析</a-button><a-button v-if="item.status !== 'published'" danger size="small" @click="reject(item.id)">拒绝</a-button><a-button danger size="small" @click="remove(item.id)">删除</a-button></template>
    </a-list-item></template></a-list>
  <KnowledgeReview v-if="reviewId" :id="reviewId" @close="reviewId = 0; load()" /></section></main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import { approveKnowledge, deleteKnowledge, fetchAdminKnowledge, rejectKnowledge } from '@/services/api'
import KnowledgeReview from '@/components/KnowledgeReview.vue'
const reviewId = ref(0)
const router = useRouter(); const items = ref<any[]>([])
const load = async () => { try { items.value = (await fetchAdminKnowledge()).data || [] } catch (error: any) { message.error(error.response?.data?.detail || '需要管理员权限') } }
const approve = async (item: any) => { await approveKnowledge(item.id, '', item.source_tier || 'community'); message.success('已进入解析队列'); await load() }
const reject = async (id: number) => { await rejectKnowledge(id); message.success('已拒绝'); await load() }
const remove = async (id: number) => { await deleteKnowledge(id); message.success('已删除'); await load() }
onMounted(load)
</script>

<style scoped>
.page-shell { max-width: 1000px; margin: 0 auto; padding: 48px 20px; }.panel { background: rgba(255,255,255,.96); border-radius: 20px; padding: 28px; box-shadow: 0 20px 45px rgba(2,6,23,.25); }.list { margin-top: 20px; }
</style>
