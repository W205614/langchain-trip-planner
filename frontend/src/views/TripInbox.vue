<template>
  <main class="trip-inbox">
    <a-space class="heading"><a-button @click="router.push('/history')">← 我的行程</a-button><h1>同行邀请、通知与用量</h1></a-space>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-card title="同行邀请" class="section">
      <a-list :data-source="invitations" bordered>
        <template #renderItem="{ item }">
          <a-list-item><a-list-item-meta :title="`${item.city} · ${item.title || '行程'}`" :description="`${item.start_date} 至 ${item.end_date} · ${item.role} · ${item.status}`" />
            <template #actions>
              <a-button v-if="item.status === 'pending'" type="primary" @click="accept(item.trip_id)">接受</a-button>
              <a-button v-if="item.status === 'pending'" danger @click="decline(item.trip_id)">拒绝</a-button>
              <a-button v-if="item.status === 'accepted'" @click="router.push(`/trips/${item.trip_id}/operations`)">打开行程</a-button>
            </template>
          </a-list-item>
        </template>
      </a-list>
    </a-card>
    <a-card title="站内通知" class="section">
      <a-list :data-source="notifications" bordered>
        <template #renderItem="{ item }">
          <a-list-item><a-list-item-meta :title="item.title" :description="`${item.message} · ${item.created_at}`" />
            <template #actions><a-button v-if="item.trip_id" @click="router.push(`/trips/${item.trip_id}/operations`)">查看行程</a-button>
              <a-button v-if="!item.read_at" @click="markRead(item.id)">标记已读</a-button></template>
          </a-list-item>
        </template>
      </a-list>
    </a-card>
    <a-card title="本月 AI 行程任务用量" class="section">
      <a-alert type="info" show-icon message="阈值只用于站内提醒，不会中断任务。费用只有在运维明确配置模型单价且上游返回用量时才显示估算。" />
      <a-descriptions v-if="usage" bordered :column="2" class="section">
        <a-descriptions-item label="任务数">{{ usage.tasks }}</a-descriptions-item>
        <a-descriptions-item label="模型调用数">{{ usage.calls }}</a-descriptions-item>
        <a-descriptions-item label="输入 Token">{{ usage.input_tokens }}</a-descriptions-item>
        <a-descriptions-item label="输出 Token">{{ usage.output_tokens }}</a-descriptions-item>
        <a-descriptions-item label="缺失用量的任务">{{ usage.missing_usage }}</a-descriptions-item>
        <a-descriptions-item label="估算费用">{{ usage.estimated_cost_usd == null ? '未配置单价' : `$${usage.estimated_cost_usd}` }}</a-descriptions-item>
      </a-descriptions>
      <a-space class="section"><span>每月 Token 提醒阈值</span><a-input-number v-model:value="limit" :min="1000" :max="1000000000" :step="1000" /><a-button @click="saveLimit">保存</a-button></a-space>
      <p>{{ usage?.cost_note }}</p>
    </a-card>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { tripOperations } from '@/services/api'

const router = useRouter()
const error = ref('')
const invitations = ref<any[]>([])
const notifications = ref<any[]>([])
const usage = ref<any>(null)
const limit = ref<number>(100000)
function explain(e: any) { error.value = e?.response?.data?.detail || e?.message || '操作失败'; message.error(error.value) }
async function load() {
  try {
    [invitations.value, notifications.value, usage.value] = await Promise.all([
      tripOperations.invitations(), tripOperations.notifications(), tripOperations.usage()
    ])
    limit.value = Number(usage.value.warning_limit || 100000)
  } catch (e) { explain(e) }
}
async function accept(id: number) { try { await tripOperations.accept(id); await load(); message.success('已加入行程') } catch (e) { explain(e) } }
async function decline(id: number) { try { await tripOperations.decline(id); await load(); message.success('已拒绝邀请') } catch (e) { explain(e) } }
async function markRead(id: number) { try { await tripOperations.readNotification(id); await load() } catch (e) { explain(e) } }
async function saveLimit() { try { usage.value = await tripOperations.usagePolicy(limit.value); message.success('提醒阈值已保存') } catch (e) { explain(e) } }
onMounted(load)
</script>

<style scoped>
.trip-inbox { max-width: 1000px; margin: 24px auto; padding: 0 20px 40px; }
.heading { margin-bottom: 18px; }
.heading h1 { margin: 0; font-size: 22px; }
.section { margin-top: 20px; }
p { color: #666; margin-top: 12px; }
</style>
