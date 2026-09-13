<template>
  <main class="tasks">
    <a-card title="我的规划任务">
      <template #extra><router-link to="/">返回规划</router-link></template>
      <p>刷新页面后仍可查看任务。取消会阻止结果保存，已发出的模型调用可能仍产生费用。失败的改排如遇版本冲突，请从历史行程重新发起。</p>
      <a-alert v-if="error" type="error" :message="error" show-icon />
      <a-button :loading="loading" @click="refresh">刷新</a-button>
      <a-list :data-source="tasks">
        <template #renderItem="{ item }">
          <a-list-item>
            <a-list-item-meta :title="`${item.city} · ${labels[item.status] || item.status}`" :description="`${item.message} ${item.error_code || ''}`" />
            <a-space wrap>
              <a-button v-if="['queued', 'running'].includes(item.status)" :disabled="busy === item.id" @click="cancel(item.id)">取消任务</a-button>
              <a-button v-if="['failed', 'cancelled'].includes(item.status)" :disabled="busy === item.id" @click="retry(item.id)">重新提交</a-button>
              <a-button v-if="['succeeded', 'needs_attention'].includes(item.status)" @click="open(item.id)">查看结果</a-button>
            </a-space>
          </a-list-item>
        </template>
      </a-list>
      <a-pagination v-model:current="page" :total="total" :page-size="20" :show-size-changer="false" @change="refresh" />
    </a-card>
  </main>
</template>

<script setup lang="ts">
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { cancelTask, fetchTask, fetchTasks, retryTask, storeTripResult } from '@/services/api'
const router = useRouter()
const tasks = ref<any[]>([]), total = ref(0), page = ref(1), loading = ref(false), error = ref(''), busy = ref('')
const labels: Record<string, string> = { queued: '排队中', running: '生成中', succeeded: '已完成', needs_attention: '未完成草稿', failed: '失败', cancelled: '已取消' }
const keys = new Map<string, string>()
let timer: ReturnType<typeof setInterval> | undefined
async function refresh() {
  if (loading.value) return
  loading.value = true
  try { const result = await fetchTasks(page.value); tasks.value = result.data; total.value = result.total }
  catch (e: any) { error.value = e.response?.data?.message || '读取任务失败' }
  finally { loading.value = false }
}
async function cancel(id: string) {
  busy.value = id; error.value = ''
  try { await cancelTask(id); await refresh() } catch { error.value = '取消失败，请重试' }
  finally { busy.value = '' }
}
async function retry(id: string) {
  busy.value = id; error.value = ''
  if (!keys.has(id)) keys.set(id, crypto.randomUUID())
  try { await retryTask(id, keys.get(id)!); await refresh() }
  catch (e: any) { error.value = e.response?.data?.message || '提交失败，请重试' }
  finally { busy.value = '' }
}
async function open(id: string) {
  try {
    const state = await fetchTask(id)
    if (!state.result) throw new Error('结果已删除或不可用')
    storeTripResult(state.result); await router.push('/result')
  } catch (e: any) { error.value = e.message || '读取结果失败' }
}
onMounted(() => { refresh(); timer = setInterval(refresh, 5000) })
onUnmounted(() => { if (timer) clearInterval(timer) })
</script>

<style scoped>
.tasks { max-width: 1000px; margin: 32px auto; padding: 16px; }
</style>
