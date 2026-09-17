<template>
  <div class="history-container">
    <!-- 页面头部 -->
    <div class="page-header">
      <a-button class="back-button" size="large" @click="goBack">
        ← 返回首页
      </a-button>
      <div class="header-title">
        <span class="header-icon">✨</span>
        <div><h1>我的 AI 行程</h1><p>列表中查看与问攻略，进入具体行程后再按日修改</p></div>
      </div>
      <div class="header-search">
        <a-input
          v-model:value="cityFilter"
          placeholder="按城市筛选"
          allow-clear
          style="width: 180px"
          @change="handleSearch"
        >
          <template #prefix>🏙️</template>
        </a-input>
      </div>
    </div>

    <!-- 加载中 -->
    <div v-if="loading" class="loading-wrapper">
      <a-spin size="large" tip="加载历史记录中..." />
    </div>

    <!-- 列表 -->
    <div v-else-if="records.length > 0" class="record-list">
      <a-card
        v-for="record in records"
        :key="record.id"
        class="record-card"
        :bordered="false"
      >
        <div class="record-main">
          <div class="record-city">
            <span class="city-name">{{ record.city }}</span>
            <a-tag>行程 #{{ record.id }}</a-tag>
            <a-tag color="blue">{{ record.travel_days }} 天</a-tag>
            <a-tag v-if="record.outcome === 'draft'" color="orange">未完成草稿</a-tag>
          </div>
          <div class="record-meta">
            <span class="meta-item">📅 {{ record.start_date }} ~ {{ record.end_date }}</span>
            <span class="meta-item">🎯 {{ record.attraction_count }} 个景点</span>
            <span class="meta-item" v-if="record.budget_total">💰 ¥{{ record.budget_total.toLocaleString() }}</span>
            <span class="meta-item">🕐 {{ record.created_at }}</span>
          </div>
          <div class="record-prefs" v-if="record.preferences && record.preferences.length">
            <a-tag
              v-for="p in record.preferences"
              :key="p"
              class="pref-tag"
            >{{ p }}</a-tag>
          </div>
        </div>
        <div class="record-actions">
          <a-button @click="openAgent(record)">💬 问攻略</a-button>
          <a-button v-if="record.outcome === 'draft'" :loading="verifyingId===record.id" @click="reverifyRecord(record)">🧭 重新核验路线</a-button>
          <a-button type="primary" @click="viewRecord(record.id)">
            👁️ 查看行程
          </a-button>
          <a-popconfirm title="确定删除这条历史记录吗?" @confirm="removeRecord(record.id)">
            <a-button danger>🗑️ 删除</a-button>
          </a-popconfirm>
        </div>
      </a-card>

      <!-- 分页 -->
      <div class="pagination-wrapper" v-if="total > pageSize">
        <a-pagination
          v-model:current="page"
          :total="total"
          :page-size="pageSize"
          :show-total="(t: number) => `共 ${t} 条记录`"
          @change="loadRecords"
        />
      </div>
    </div>

    <!-- 空状态 -->
    <a-empty v-else class="empty-wrapper">
      <template #image>
        <div style="font-size: 64px;">🗺️</div>
      </template>
      <template #description>
        <span>还没有历史行程记录，快去生成你的第一个旅行计划吧</span>
      </template>
      <a-button type="primary" @click="goBack">返回首页创建行程</a-button>
    </a-empty>

    <a-modal v-model:open="agentOpen" :footer="null" width="720px" @cancel="resetAgent">
      <template #title>{{ activeRecord?.city }}行程 · 攻略问答</template>
      <div v-if="activeRecord" class="agent-panel">
        <div class="agent-context">
          <div><strong>{{ activeRecord.city }}</strong><span>{{ activeRecord.start_date }} 至 {{ activeRecord.end_date }} · {{ activeRecord.travel_days }} 天</span></div>
          <a-tag color="purple">行程 #{{ activeRecord.id }}</a-tag>
        </div>
        <p class="mode-note">Agent 会结合这条行程直接回答，并为外部事实附上当前资料来源。修改安排请先进入具体行程。</p>
        <div class="quick-prompts"><button v-for="prompt in quickPrompts" :key="prompt" @click="agentInput=prompt">{{ prompt }}</button></div>
        <a-textarea v-model:value="agentInput" :rows="5" :maxlength="300" show-count placeholder="例如：这份行程里的故宫如何预约？哪一天安排最合适？" />
        <div class="agent-submit"><span>{{ agentProgress || '本次问答绑定当前历史记录。' }}</span><a-button type="primary" size="large" :loading="agentSending" :disabled="agentInput.trim().length<2" @click="sendToAgent">获取直接回答</a-button></div>
        <a-alert v-if="agentError" type="error" :message="agentError" show-icon />
        <section v-if="agentReply" class="agent-answer"><strong>Agent 回答</strong><div>{{ agentReply }}</div>
          <div v-if="agentSources.length" class="answer-sources"><b>参考来源</b><span v-for="source in agentSources" :key="source.index">[{{ source.index }}] {{ source.source }}<template v-if="source.page"> · 第 {{ source.page }} 页</template></span></div>
        </section>
      </div>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { fetchHistory, fetchHistoryDetail, deleteHistory, createAssistantConversation, streamAssistantMessage, reverifyTrip } from '@/services/api'

const router = useRouter()

const records = ref<any[]>([])
const total = ref(0)
const page = ref(1)
const pageSize = 10
const cityFilter = ref('')
const loading = ref(false)
const agentOpen = ref(false)
const activeRecord = ref<any>()
const conversationId = ref('')
const agentInput = ref('')
const agentReply = ref('')
const agentSources = ref<any[]>([])
const agentError = ref('')
const agentProgress = ref('')
const agentSending = ref(false)
const verifyingId = ref(0)
const quickPrompts = ['这份行程有哪些预约事项？', '怎样减少排队和步行？', '有哪些容易忽略的注意事项？']

onMounted(() => {
  loadRecords()
})

const goBack = () => {
  router.push('/')
}

// 城市筛选 (防抖由 input change 触发)
let searchTimer: ReturnType<typeof setTimeout> | null = null
const handleSearch = () => {
  if (searchTimer) clearTimeout(searchTimer)
  searchTimer = setTimeout(() => {
    page.value = 1
    loadRecords()
  }, 400)
}

// 加载列表
const loadRecords = async () => {
  loading.value = true
  try {
    const resp = await fetchHistory(page.value, pageSize, cityFilter.value.trim())
    if (resp.success) {
      records.value = resp.data
      total.value = resp.total
    }
  } catch (error: any) {
    message.error(error.message || '加载历史记录失败')
  } finally {
    loading.value = false
  }
}

// 查看行程: 拉详情后跳转结果页渲染 (记录 id, 供结果页编辑保存写回数据库)
const viewRecord = async (id: number) => {
  try {
    const resp = await fetchHistoryDetail(id)
    if (resp.success && resp.data.plan) {
      sessionStorage.setItem('tripPlan', JSON.stringify(resp.data.plan))
      sessionStorage.setItem('tripPlanId', String(id))
        sessionStorage.setItem('tripPlanVersion', String(resp.data.version))
        sessionStorage.setItem('tripQuality', JSON.stringify(resp.data.quality || {}))
        sessionStorage.removeItem('tripUnsaved')
      router.push({ path: '/result', query: { from: 'history' } })
    } else {
      message.error('记录数据异常')
    }
  } catch (error: any) {
    message.error(error.message || '加载行程详情失败')
  }
}

const resetAgent = () => {
  activeRecord.value = undefined
  conversationId.value = ''
  agentInput.value = ''
  agentReply.value = ''
  agentSources.value = []
  agentError.value = ''
  agentProgress.value = ''
}

const openAgent = async (record: any) => {
  activeRecord.value = record
  agentInput.value = ''
  agentReply.value = ''
  agentSources.value = []
  agentError.value = ''
  agentProgress.value = ''
  agentOpen.value = true
  try {
    const response = await createAssistantConversation({ active_trip_id: record.id, title: `${record.city}行程` })
    conversationId.value = response.id
  } catch (error: any) {
    agentError.value = error?.response?.data?.message || 'Agent 会话创建失败，请稍后重试'
  }
}

const sendToAgent = async () => {
  if (!activeRecord.value || !conversationId.value || agentInput.value.trim().length < 2) return
  agentSending.value = true
  agentError.value = ''
  agentReply.value = ''
  agentSources.value = []
  try {
    const payload = { mode: 'research', content: agentInput.value.trim(), city: activeRecord.value.city }
    const result = await streamAssistantMessage(conversationId.value, payload, (event, data) => {
      if (event === 'progress') agentProgress.value = data.message || '正在检索资料'
      if (event === 'token') {
        agentProgress.value = '正在生成回答'
        agentReply.value += data.delta || ''
      }
      if (event === 'result') {
        agentReply.value = data.answer || agentReply.value || 'Agent 已处理'
        agentSources.value = data.sources || []
        agentProgress.value = ''
      }
    })
    agentReply.value = result.answer || agentReply.value || 'Agent 已处理'
    agentSources.value = result.sources || []
  } catch (error: any) {
    agentError.value = error?.response?.data?.message || error?.response?.data?.detail || 'Agent 暂不可用，请稍后重试'
  } finally {
    agentProgress.value = ''
    agentSending.value = false
  }
}

const reverifyRecord = async (record: any) => {
  verifyingId.value = record.id
  try {
    const response = await reverifyTrip(record.id, record.version)
    const routeMissing = (response.quality?.issues || []).some((issue: any) => issue.code === 'TIME_LIMIT_UNVERIFIED')
    if (routeMissing) message.warning('仍有路线无法核验，请进入具体行程后使用 AI 重新安排')
    else message.success('路线已重新核验，时间统计已更新')
    await loadRecords()
  } catch (error: any) {
    message.error(error?.response?.data?.message || '路线重新核验失败')
  } finally {
    verifyingId.value = 0
  }
}

// 删除记录
const removeRecord = async (id: number) => {
  try {
    const resp = await deleteHistory(id)
    if (resp.success) {
      message.success('删除成功')
      // 当前页删空后回退一页
      if (records.value.length === 1 && page.value > 1) {
        page.value -= 1
      }
      loadRecords()
    }
  } catch (error: any) {
    message.error(error.message || '删除失败')
  }
}
</script>

<style scoped>
.history-container {
  min-height: 100vh;
  background: transparent;
  padding: 40px 20px;
}

.page-header {
  max-width: 900px;
  margin: 0 auto 30px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  animation: fadeInDown 0.6s ease-out;
}

.back-button {
  border-radius: 8px;
  font-weight: 500;
}

.header-title {
  display: flex;
  align-items: center;
  gap: 8px;
}

.header-title h1 {
  margin: 0;
  font-size: 28px;
  color: #fff;
  text-shadow: 0 2px 12px rgba(139, 92, 246, 0.4);
}

.header-title p { margin: 4px 0 0; color: #dbeafe; font-size: 13px; }

.header-icon {
  font-size: 28px;
}

.record-list {
  max-width: 900px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.record-card {
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.93) !important;
  backdrop-filter: blur(16px) saturate(140%);
  border: 1px solid rgba(255, 255, 255, 0.5) !important;
  box-shadow: 0 12px 36px rgba(2, 6, 23, 0.35);
  transition: all 0.3s ease;
  animation: fadeInUp 0.5s ease-out;
}

.record-card:hover {
  transform: translateY(-4px);
  box-shadow: 0 20px 48px rgba(2, 6, 23, 0.5);
}

.record-main {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.record-city {
  display: flex;
  align-items: center;
  gap: 10px;
}

.city-name {
  font-size: 22px;
  font-weight: 700;
  color: #333;
}

.record-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 16px;
}

.meta-item {
  font-size: 14px;
  color: #666;
}

.record-prefs {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.pref-tag {
  border-radius: 12px;
}

.record-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 6px;
}

.pagination-wrapper {
  display: flex;
  justify-content: center;
  padding: 20px 0;
}

.loading-wrapper {
  display: flex;
  justify-content: center;
  padding: 80px 0;
}

.empty-wrapper {
  padding: 80px 0;
}

.agent-panel { display: flex; flex-direction: column; gap: 18px; padding-top: 8px; }
.agent-context { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 15px 17px; border-radius: 14px; background: #f5f3ff; }
.agent-context div { display: flex; flex-direction: column; gap: 3px; }
.agent-context strong { color: #1d2939; font-size: 18px; }
.agent-context span { color: #667085; font-size: 13px; }
.mode-note { margin: 0; color: #667085; line-height: 1.7; }
.quick-prompts { display: flex; flex-wrap: wrap; gap: 8px; margin-top: -6px; }
.quick-prompts button { padding: 6px 10px; border: 1px solid #d0d5dd; border-radius: 999px; background: #fff; color: #475467; cursor: pointer; }
.agent-submit { display: flex; align-items: center; justify-content: space-between; gap: 18px; }
.agent-submit > span { color: #667085; font-size: 13px; }
.agent-answer { padding: 18px; border: 1px solid #bbf7d0; border-radius: 14px; background: #f0fdf4; }
.agent-answer > strong { color: #15803d; }
.agent-answer > div { margin-top: 9px; color: #1f2937; white-space: pre-wrap; line-height: 1.8; }
.answer-sources { display: flex; flex-direction: column; gap: 5px; padding-top: 12px; border-top: 1px solid #dcfce7; font-size: 13px; }

@media (max-width: 720px) {
  .page-header { align-items: flex-start; flex-direction: column; }
  .record-actions { justify-content: flex-start; flex-wrap: wrap; }
  .agent-submit { align-items: stretch; flex-direction: column; }
}

@keyframes fadeInDown {
  from {
    opacity: 0;
    transform: translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}

@keyframes fadeInUp {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
</style>
