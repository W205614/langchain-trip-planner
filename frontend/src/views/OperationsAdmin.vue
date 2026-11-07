<template>
  <main class="operations-page">
    <a-button type="link" @click="router.push('/')">← 返回首页</a-button>
    <h1>运营管理</h1>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-tabs v-model:activeKey="tab" @change="refresh">
      <a-tab-pane key="users" tab="用户与配额">
        <a-space><a-input-search v-model:value="query" placeholder="搜索用户名" @search="search" /><a-button @click="refresh">刷新</a-button></a-space>
        <a-table :data-source="users" :columns="userColumns" row-key="id" :pagination="false" :scroll="{ x: 680 }">
          <template #bodyCell="{ column, record }">
            <template v-if="column.key === 'email'">{{ record.email || '未绑定' }}<a-tag v-if="record.email_verified_at">已验证</a-tag></template>
            <template v-if="column.key === 'actions'"><a-button type="link" @click="editQuota(record)">配额</a-button><a-button v-if="['active', 'disabled'].includes(record.account_state)" type="link" :danger="record.account_state === 'active'" @click="editState(record)">{{ record.account_state === 'active' ? '停用' : '启用' }}</a-button></template>
          </template>
        </a-table>
        <a-pagination v-model:current="page" :page-size="50" :total="total" :show-size-changer="false" @change="refresh" />
      </a-tab-pane>
      <a-tab-pane key="feedback" tab="问题反馈">
        <a-space><a-select v-model:value="feedbackStatus" :options="statusOptions" style="width:140px" @change="refresh" /><a-button @click="refresh">刷新</a-button></a-space>
        <article v-for="item in feedback" :key="item.id"><a-tag>{{ item.status }}</a-tag><strong>#{{ item.id }} {{ item.subject }}</strong><p class="prewrap">{{ item.message }}</p><p v-if="item.trip_id || item.task_id || item.request_id">行程 {{ item.trip_id || '—' }} / 任务 {{ item.task_id || '—' }} / 请求 {{ item.request_id || '—' }}</p><p v-if="item.reply" class="prewrap">回复：{{ item.reply }}</p><a-button @click="editFeedback(item)">回复与处理</a-button></article>
        <a-empty v-if="!feedback.length" description="暂无反馈" />
      </a-tab-pane>
      <a-tab-pane key="mail" tab="邮件投递">
        <p>{{ mailNote }}</p><a-button @click="refresh">刷新</a-button>
        <a-table :data-source="deliveries" :columns="mailColumns" row-key="id" :scroll="{ x: 700 }">
          <template #bodyCell="{ column, record }"><a-button v-if="column.key === 'actions' && record.status === 'failed'" type="link" @click="replayMail(record.id)">重放</a-button></template>
        </a-table>
      </a-tab-pane>
      <a-tab-pane key="jobs" tab="数据作业">
        <p>注销等待索引和文件删除确认后才完成。处理外部依赖问题后，可重放失败作业。</p><a-button @click="refresh">刷新</a-button>
        <article v-for="job in jobs" :key="job.id"><strong>{{ job.kind === 'erasure' ? '注销' : '导出' }} / 用户 {{ job.user_id }}</strong><p>{{ job.id }}</p><a-tag>{{ job.status }}</a-tag>{{ job.stage }} {{ job.error_code }}<a-button v-if="job.status === 'failed'" type="link" @click="replayJob(job.id)">重放</a-button></article>
      </a-tab-pane>
    </a-tabs>
    <a-modal v-model:open="stateOpen" :title="`修改 ${selectedUser?.username || ''} 的账号状态`" :confirm-loading="busy" @ok="saveState">
      <p>操作会撤销旧登录并记录审计；停用后已发出的上游调用可能仍产生费用。</p><a-textarea v-model:value="stateReason" placeholder="处理原因" :maxlength="300" />
    </a-modal>
    <a-modal v-model:open="quotaOpen" :title="`${selectedUser?.username || ''} 的配额`" :confirm-loading="busy" @ok="saveQuota">
      <p>清空字段表示继承部署默认值；0表示拒绝新的对应操作。日期按 UTC 计算。</p>
      <a-form layout="vertical"><a-form-item v-for="field in quotaFields" :key="field.key" :label="`${field.label}（当前有效值 ${effective[field.key] ?? '—'}）`"><a-input-number v-model:value="quota[field.key]" :min="0" :max="field.maximum" style="width:100%" /></a-form-item></a-form>
    </a-modal>
    <a-modal v-model:open="feedbackOpen" title="处理反馈" :confirm-loading="busy" @ok="saveFeedback">
      <a-form layout="vertical"><a-form-item label="处理状态"><a-select v-model:value="replyStatus" :options="statusOptions.filter(o => o.value)" /></a-form-item><a-form-item label="回复（关闭时必填）"><a-textarea v-model:value="reply" :rows="5" :maxlength="3000" /></a-form-item></a-form>
    </a-modal>
  </main>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { isAdmin } from '@/services/auth'
import { publicOperations, type OperationalUser, type FeedbackItem, type DataJob, type QuotaValues } from '@/services/publicOperations'
const router = useRouter(), tab = ref('users'), error = ref(''), busy = ref(false)
const users = ref<OperationalUser[]>([]), query = ref(''), page = ref(1), total = ref(0)
const feedback = ref<FeedbackItem[]>([]), feedbackStatus = ref(''), deliveries = ref<any[]>([]), mailNote = ref(''), jobs = ref<DataJob[]>([])
const selectedUser = ref<OperationalUser>(), stateOpen = ref(false), stateReason = ref(''), quotaOpen = ref(false)
const quota = ref<QuotaValues>({ daily_tasks: null, active_tasks: null, daily_text: null, daily_embedding: null, daily_amap: null }), effective = ref<Record<string, number>>({})
const selectedFeedback = ref<FeedbackItem>(), feedbackOpen = ref(false), reply = ref(''), replyStatus = ref('processing')
const quotaFields: { key: keyof QuotaValues; label: string; maximum: number }[] = [{ key: 'daily_tasks', label: '每日规划任务', maximum: 100000 }, { key: 'active_tasks', label: '同时活动任务', maximum: 1000 }, { key: 'daily_text', label: '每日文本／视觉调用', maximum: 1000000 }, { key: 'daily_embedding', label: '每日向量化调用', maximum: 1000000 }, { key: 'daily_amap', label: '每日地图调用', maximum: 1000000 }]
const statusOptions = [{ value: '', label: '全部' }, { value: 'open', label: '待处理' }, { value: 'processing', label: '处理中' }, { value: 'closed', label: '已关闭' }]
const userColumns = [{ title: '用户', dataIndex: 'username' }, { title: '邮箱', key: 'email' }, { title: '状态', dataIndex: 'account_state' }, { title: '操作', key: 'actions' }]
const mailColumns = [{ title: '编号', dataIndex: 'id' }, { title: '用户', dataIndex: 'user_id' }, { title: '类别', dataIndex: 'kind' }, { title: '状态', dataIndex: 'status' }, { title: '尝试', dataIndex: 'attempts' }, { title: '错误', dataIndex: 'last_error' }, { title: '操作', key: 'actions' }]
const action = async (work: () => Promise<void>) => { if (busy.value) return; busy.value = true; error.value = ''; try { await work() } catch (failure: any) { error.value = failure.response?.data?.detail || failure.message } finally { busy.value = false } }
async function load() {
  if (!isAdmin()) throw new Error('此页面需要管理员权限及部署要求的二次验证')
  if (tab.value === 'users') { const result = await publicOperations.users(query.value, page.value); users.value = result.data; total.value = result.total }
  else if (tab.value === 'feedback') feedback.value = (await publicOperations.adminFeedback(feedbackStatus.value)).data
  else if (tab.value === 'mail') { const result = await publicOperations.mail(); deliveries.value = result.data; mailNote.value = result.acceptance_note }
  else jobs.value = (await publicOperations.adminJobs()).data
}
const refresh = () => action(load)
const search = () => { page.value = 1; refresh() }
const editState = (user: OperationalUser) => { selectedUser.value = user; stateReason.value = ''; stateOpen.value = true }
const saveState = () => action(async () => { if (!selectedUser.value) return; await publicOperations.userState(selectedUser.value.id, selectedUser.value.account_state === 'active' ? 'disabled' : 'active', stateReason.value); stateOpen.value = false; await load() })
const editQuota = (user: OperationalUser) => action(async () => { selectedUser.value = user; const result = await publicOperations.quotas(user.id); quota.value = result.overrides; effective.value = result.limits; quotaOpen.value = true })
const saveQuota = () => action(async () => { if (!selectedUser.value) return; await publicOperations.saveQuotas(selectedUser.value.id, quota.value); quotaOpen.value = false; message.success('配额已更新') })
const editFeedback = (item: FeedbackItem) => { selectedFeedback.value = item; reply.value = item.reply; replyStatus.value = item.status; feedbackOpen.value = true }
const saveFeedback = () => action(async () => { if (!selectedFeedback.value) return; await publicOperations.reply(selectedFeedback.value, replyStatus.value, reply.value); feedbackOpen.value = false; await load() })
const replayMail = (id: number) => action(async () => { await publicOperations.replayMail(id); await load() })
const replayJob = (id: string) => action(async () => { await publicOperations.replayJob(id); await load() })
onMounted(() => action(load))
</script>
<style scoped>
.operations-page{max-width:1120px;margin:32px auto;padding:28px;background:#fff;border-radius:12px;color:#202b40}article{padding:18px 0;border-bottom:1px solid #ddd;overflow-wrap:anywhere}.prewrap{white-space:pre-wrap}.ant-pagination{margin-top:20px}@media(max-width:600px){.operations-page{margin:12px;padding:18px}}
</style>
