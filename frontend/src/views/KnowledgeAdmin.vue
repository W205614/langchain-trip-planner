<template>
  <main class="page-shell"><section class="panel">
    <a-button type="link" @click="router.push('/')">← 返回首页</a-button><h1>公共知识审核</h1>
    <a-alert show-icon type="info" message="批准后会由后台解析并写入公共知识库；失败资料可再次批准重试。" />
    <a-list :data-source="items" bordered class="list"><template #renderItem="{ item }"><a-list-item>
      <div><b>{{ item.title }}</b> · {{ item.city }}<br><small>{{ item.original_filename }} · 状态：{{ item.status }} {{ item.review_note }}</small></div>
      <template #actions><a-button v-if="item.status !== 'published'" type="primary" size="small" @click="approve(item.id)">批准</a-button><a-button v-if="item.status !== 'published'" danger size="small" @click="reject(item.id)">拒绝</a-button><a-button danger size="small" @click="remove(item.id)">删除</a-button></template>
    </a-list-item></template></a-list>
  </section></main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { message } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import { approveKnowledge, deleteKnowledge, fetchAdminKnowledge, rejectKnowledge } from '@/services/api'
const router = useRouter(); const items = ref<any[]>([])
const load = async () => { try { items.value = (await fetchAdminKnowledge()).data || [] } catch (error: any) { message.error(error.response?.data?.detail || '需要管理员权限') } }
const approve = async (id: number) => { await approveKnowledge(id); message.success('已进入解析队列'); await load() }
const reject = async (id: number) => { await rejectKnowledge(id); message.success('已拒绝'); await load() }
const remove = async (id: number) => { await deleteKnowledge(id); message.success('已删除'); await load() }
onMounted(load)
</script>

<style scoped>
.page-shell { max-width: 1000px; margin: 0 auto; padding: 48px 20px; }.panel { background: rgba(255,255,255,.96); border-radius: 20px; padding: 28px; box-shadow: 0 20px 45px rgba(2,6,23,.25); }.list { margin-top: 20px; }
</style>
