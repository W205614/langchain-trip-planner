<template>
  <main class="trip-operations">
    <header class="page-heading">
      <a-button class="back-button" @click="router.push('/history')">← 我的行程</a-button>
      <div class="heading-copy">
        <span class="eyebrow">TRIP WORKSPACE</span>
        <h1>{{ trip?.city || '行程' }} · 执行工作台</h1>
        <p>出行前核对信息，和同行人一起管理安排与花费。</p>
      </div>
      <div v-if="trip" class="heading-meta">
        <a-tag color="purple">版本 {{ trip.version }}</a-tag>
        <a-tag color="blue">{{ roleLabel }}</a-tag>
      </div>
    </header>
    <a-alert v-if="error" class="error-alert" type="error" :message="error" show-icon closable @close="error = ''" />
    <a-spin :spinning="loading" class="workspace-spin">
      <a-tabs v-if="trip" v-model:activeKey="tab" class="workspace-panel">
        <a-tab-pane key="check" tab="行前核验">
          <div class="section-intro">
            <h2>行前核验</h2>
            <p>选择出行日期，查看天气和景点营业信息的待确认事项。</p>
          </div>
          <a-alert class="context-alert" type="info" show-icon message="地图信息仅供参考。具体日期的开放、预约及天气请以官方信息为准；核验不会自动更改行程。" />
          <section class="check-controls" aria-label="核验操作">
            <div class="day-field">
              <label for="check-day">核验日期</label>
              <a-select id="check-day" v-model:value="checkDay" @change="loadCheck"><a-select-option v-for="d in trip.travel_days" :key="d" :value="d - 1">第{{ d }}天</a-select-option></a-select>
            </div>
            <div class="check-actions">
              <a-button v-if="trip.role === 'owner'" type="primary" :loading="busy" @click="runCheck">核验当天信息</a-button>
              <a-button v-if="trip.role === 'owner'" @click="openPlan">打开行程并按日改排</a-button>
              <a-button @click="loadCheck">刷新结果</a-button>
            </div>
          </section>
          <section class="results-section" aria-label="核验结果">
            <div class="results-heading">
              <div>
                <h3>第 {{ checkDay + 1 }} 天的核验结果</h3>
                <p>{{ check?.status === 'never_checked' ? '尚未核验这一天。' : check?.stale ? '行程版本已变化或核验超过 6 小时，请重新核验。' : '核验记录只提示需进一步确认的信息。' }}</p>
              </div>
              <a-tag :color="check?.stale ? 'orange' : check?.status === 'current' ? 'green' : check?.status === 'never_checked' ? 'default' : 'gold'">
                {{ check?.stale ? '结果已过期' : check?.status === 'current' ? '暂无新增风险' : check?.status === 'never_checked' ? '尚未核验' : '有待确认事项' }}
              </a-tag>
            </div>
            <div v-if="check?.risks?.length" class="risk-list">
              <article v-for="item in check.risks" :key="item.id" class="risk-item">
                <div class="risk-copy">
                  <div class="risk-title-row">
                    <a-tag color="gold">第 {{ item.day_index + 1 }} 天</a-tag>
                    <h4>{{ riskLabel(item.code) }}</h4>
                  </div>
                  <p>{{ item.message }}</p>
                </div>
                <div class="risk-action">
                  <a-tag v-if="item.acknowledged_at" color="blue">已知悉</a-tag>
                  <a-button v-else :disabled="check?.stale || trip.role === 'viewer'" @click="acknowledge(item.id)">标记已知悉</a-button>
                </div>
              </article>
            </div>
            <a-empty v-else :description="check?.status === 'never_checked' ? (trip.role === 'owner' ? '选择日期后点击“核验当天信息”' : '行程所有者尚未核验这一天') : check?.stale ? '历史结果已过期，请重新核验后查看当前状态' : '本次核验没有发现待确认事项'" class="check-empty" />
          </section>
          <p class="footnote">需要调整行程时，请到行程详情选择目标日期发起局部改排。“已知悉”仅表示你看过提醒。</p>
        </a-tab-pane>

        <a-tab-pane key="money" tab="预订与费用">
          <div class="section-intro">
            <h2>预订与费用</h2>
            <p>分别查看计划估算、已确认事项和你们记录的实际支出；这里不会完成真实订票或付款。</p>
          </div>
          <a-row :gutter="[14, 14]" class="summary-grid">
            <a-col :xs="24" :sm="12" :lg="6"><div class="stat-tile"><a-statistic title="原计划估算" :value="yuan(expenseResult?.summary?.plan_estimate_cents)" prefix="¥" /></div></a-col>
            <a-col :xs="24" :sm="12" :lg="6"><div class="stat-tile"><a-statistic title="预算上限" :value="expenseResult?.summary?.budget_limit_cents == null ? '未设置' : yuan(expenseResult.summary.budget_limit_cents)" :prefix="expenseResult?.summary?.budget_limit_cents == null ? '' : '¥'" /></div></a-col>
            <a-col :xs="24" :sm="12" :lg="6"><div class="stat-tile"><a-statistic title="已确认预订" :value="yuan(expenseResult?.summary?.confirmed_cents)" prefix="¥" /></div></a-col>
            <a-col :xs="24" :sm="12" :lg="6"><div class="stat-tile"><a-statistic title="已记录实际支出" :value="yuan(expenseResult?.summary?.actual_cents)" prefix="¥" /></div></a-col>
          </a-row>
          <a-alert v-if="expenseResult?.summary?.budget_limit_cents != null && expenseResult.summary.actual_cents > expenseResult.summary.budget_limit_cents" type="warning" message="已记录实际支出超过预算上限" />
          <a-alert v-if="expenseResult?.summary?.confirmed_unknown_count" type="warning" :message="`${expenseResult.summary.confirmed_unknown_count} 项已确认预订未填写金额`" />
          <a-card title="预订事项" class="section">
            <a-space v-if="canEdit" wrap class="toolbar">
              <a-select v-model:value="commitmentForm.day_index" style="width: 110px"><a-select-option v-for="d in trip.travel_days" :key="d" :value="d - 1">第{{ d }}天</a-select-option></a-select>
              <a-select v-model:value="commitmentForm.category" style="width: 110px"><a-select-option v-for="c in categories" :key="c.value" :value="c.value">{{ c.label }}</a-select-option></a-select>
              <a-input v-model:value="commitmentForm.title" placeholder="门票或住宿事项" style="width: 190px" />
              <a-input-number v-model:value="commitmentForm.amount" :min="0" :max="10000000" :precision="2" placeholder="金额（可留空）" />
              <a-button :loading="busy" @click="addCommitment">添加待确认事项</a-button>
            </a-space>
            <a-list :data-source="commitments" :locale="{ emptyText: '还没有预订事项' }" bordered>
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-list-item-meta :title="`第 ${item.day_index + 1} 天 · ${item.title}`" :description="`${label(item.category)} · ${item.amount_cents == null ? '金额未知' : '¥' + yuan(item.amount_cents)} · ${commitmentStatusLabel(item.status)}`" />
                  <template #actions>
                    <a-button v-if="canEdit && item.status === 'planned'" @click="setCommitment(item, 'confirmed')">确认</a-button>
                    <a-button v-if="canEdit && item.status !== 'cancelled'" danger @click="setCommitment(item, 'cancelled')">取消</a-button>
                  </template>
                </a-list-item>
              </template>
            </a-list>
          </a-card>
          <a-card title="实际支出" class="section">
            <a-space v-if="canEdit" wrap class="toolbar">
              <a-select v-model:value="expenseForm.day_index" style="width: 110px"><a-select-option v-for="d in trip.travel_days" :key="d" :value="d - 1">第{{ d }}天</a-select-option></a-select>
              <a-select v-model:value="expenseForm.category" style="width: 110px"><a-select-option v-for="c in categories" :key="c.value" :value="c.value">{{ c.label }}</a-select-option></a-select>
              <a-input-number v-model:value="expenseForm.amount" :min="0.01" :max="10000000" :precision="2" placeholder="实际金额" />
              <a-select v-model:value="expenseForm.commitment_id" allow-clear placeholder="关联预订（可选）" style="width: 190px"><a-select-option v-for="item in commitments.filter((c: any) => c.status === 'confirmed')" :key="item.id" :value="item.id">{{ item.title }}</a-select-option></a-select>
              <a-input v-model:value="expenseForm.note" placeholder="支出说明" style="width: 180px" />
              <a-button :loading="busy" @click="addExpense">记一笔</a-button>
            </a-space>
            <a-list :data-source="expenseResult?.data || []" :locale="{ emptyText: '还没有实际支出记录' }" bordered>
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-list-item-meta :title="`第 ${item.day_index + 1} 天 · ${label(item.category)} · ¥${yuan(item.amount_cents)}`" :description="item.voided_at ? `已撤销：${item.void_reason}` : item.note" />
                  <template #actions><a-button v-if="canEdit && !item.voided_at && (trip.role === 'owner' || item.created_by === trip.viewer_id)" danger @click="voidExpense(item.id)">撤销</a-button></template>
                </a-list-item>
              </template>
            </a-list>
          </a-card>
        </a-tab-pane>

        <a-tab-pane key="members" tab="同行协作">
          <div class="section-intro">
            <h2>同行协作</h2>
            <p>邀请同行人查看或编辑这趟行程，每次角色变更都需要对方重新接受。</p>
          </div>
          <a-alert class="context-alert" type="info" show-icon message="受邀人接受后才能查看行程；编辑者仅可调整当天已有景点的顺序，保存时校验行程版本。" />
          <a-space v-if="trip.role === 'owner'" wrap class="toolbar">
            <a-input v-model:value="inviteName" placeholder="已注册用户名" style="width: 180px" />
            <a-select v-model:value="inviteRole" style="width: 110px"><a-select-option value="viewer">查看者</a-select-option><a-select-option value="editor">编辑者</a-select-option></a-select>
            <a-button :loading="busy" @click="invite">邀请</a-button>
          </a-space>
          <a-list v-if="trip.role === 'owner'" :data-source="members" :locale="{ emptyText: '还没有邀请同行人' }" bordered>
            <template #renderItem="{ item }">
              <a-list-item><a-list-item-meta :title="item.username" :description="`${memberRoleLabel(item.role)} · ${memberStatusLabel(item.status)}`" />
                <template #actions><a-button danger @click="removeMember(item.user_id)">移除</a-button></template>
              </a-list-item>
            </template>
          </a-list>
          <a-card v-if="canEdit" title="调整当天景点顺序" class="section">
            <a-select v-model:value="orderDay" style="width: 130px" @change="resetOrder"><a-select-option v-for="d in trip.travel_days" :key="d" :value="d - 1">第{{ d }}天</a-select-option></a-select>
            <a-textarea v-model:value="orderDescription" :maxlength="500" :rows="2" placeholder="当天主题或安排说明" class="section" />
            <a-list :data-source="orderItems" :locale="{ emptyText: '当天暂无可调整顺序的景点' }" bordered class="section">
              <template #renderItem="{ item, index }">
                <a-list-item><a-list-item-meta :title="item.name" :description="item.poi_id" />
                  <template #actions><a-button :disabled="index === 0" @click="move(index, -1)">上移</a-button><a-button :disabled="index === orderItems.length - 1" @click="move(index, 1)">下移</a-button></template>
                </a-list-item>
              </template>
            </a-list>
            <a-button type="primary" :disabled="!orderItems.length" :loading="busy" @click="saveOrder">保存顺序</a-button>
          </a-card>
        </a-tab-pane>
      </a-tabs>
    </a-spin>
  </main>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message, Modal } from 'ant-design-vue'
import { tripOperations } from '@/services/api'

const route = useRoute()
const router = useRouter()
const id = Number(route.params.id)
const loading = ref(false)
const busy = ref(false)
const error = ref('')
const tab = ref('check')
const trip = ref<any>(null)
const check = ref<any>(null)
const checkDay = ref(0)
const commitments = ref<any[]>([])
const expenseResult = ref<any>(null)
const members = ref<any[]>([])
const inviteName = ref('')
const inviteRole = ref<'viewer' | 'editor'>('viewer')
const orderDay = ref(0)
const orderItems = ref<any[]>([])
const orderDescription = ref('')
const commitmentForm = reactive<any>({ day_index: 0, category: 'ticket', title: '', status: 'planned', amount: null, note: '' })
const expenseForm = reactive<any>({ day_index: 0, category: 'ticket', amount: null, note: '', commitment_id: null })
const categories = [
  { value: 'ticket', label: '门票' }, { value: 'lodging', label: '住宿' },
  { value: 'transport', label: '交通' }, { value: 'meal', label: '餐饮' }, { value: 'other', label: '其他' }
]
const canEdit = computed(() => trip.value?.role === 'owner' || trip.value?.role === 'editor')
const roleLabel = computed(() => ({ owner: '行程所有者', editor: '可编辑同行人', viewer: '查看者' } as any)[trip.value?.role] || '')
const label = (value: string) => categories.find(c => c.value === value)?.label || value
const riskLabels: Record<string, string> = {
  forecast_unavailable: '暂无该日期天气预报', weather_attention: '天气需要留意',
  weather_changed: '天气预报已变化', weather_unavailable: '天气暂时无法核验',
  check_time_budget: '景点尚未完成核验', poi_unknown: '景点缺少地图编号',
  poi_mismatch: '景点编号不一致', hours_unknown: '营业时间待确认',
  hours_changed: '营业时间有变化', poi_unavailable: '景点暂时无法核验'
}
const riskLabel = (code: string) => riskLabels[code] || '需要人工确认'
const commitmentStatusLabel = (status: string) => ({ planned: '待确认', confirmed: '已确认', cancelled: '已取消' } as Record<string, string>)[status] || status
const memberRoleLabel = (role: string) => ({ viewer: '查看者', editor: '编辑者' } as Record<string, string>)[role] || role
const memberStatusLabel = (status: string) => ({ pending: '待接受', accepted: '已接受', declined: '已拒绝' } as Record<string, string>)[status] || status
const yuan = (value: number | null | undefined) => ((value || 0) / 100).toFixed(2)
const explain = (e: any) => { error.value = e?.response?.data?.detail || e?.message || '操作失败'; message.error(error.value) }

async function load() {
  loading.value = true
  try {
    trip.value = await tripOperations.workspace(id)
    await Promise.all([loadCheck(), loadMoney(), loadMembers()])
    resetOrder()
  } catch (e) { explain(e) } finally { loading.value = false }
}
async function loadCheck() { try { check.value = await tripOperations.latestCheck(id, checkDay.value) } catch (e) { explain(e) } }
async function loadMoney() { try { [commitments.value, expenseResult.value] = await Promise.all([tripOperations.commitments(id), tripOperations.expenses(id)]) } catch (e) { explain(e) } }
async function loadMembers() { if (trip.value?.role === 'owner') try { members.value = await tripOperations.members(id) } catch (e) { explain(e) } }
async function runCheck() { busy.value = true; try { await tripOperations.check(id, trip.value.version, checkDay.value); await loadCheck(); message.success('核验结果已保存') } catch (e) { explain(e) } finally { busy.value = false } }
async function acknowledge(riskId: number) { try { await tripOperations.acknowledge(id, riskId, trip.value.version); await loadCheck() } catch (e) { explain(e) } }
async function addCommitment() {
  busy.value = true
  try { await tripOperations.addCommitment(id, { ...commitmentForm }); commitmentForm.title = ''; commitmentForm.amount = null; await loadMoney(); message.success('事项已记录') }
  catch (e) { explain(e) } finally { busy.value = false }
}
async function setCommitment(item: any, status: string) {
  try { await tripOperations.updateCommitment(id, item.id, item.version, { status, amount: item.amount_cents == null ? null : yuan(item.amount_cents), note: item.note }); await loadMoney() }
  catch (e) { explain(e) }
}
async function addExpense() {
  busy.value = true
  try { await tripOperations.addExpense(id, { ...expenseForm }); expenseForm.amount = null; expenseForm.note = ''; expenseForm.commitment_id = null; await loadMoney(); message.success('支出已记录') }
  catch (e) { explain(e) } finally { busy.value = false }
}
function voidExpense(expenseId: number) {
  Modal.confirm({ title: '撤销这笔支出？', content: '记录会保留审计痕迹，撤销后不计入实际支出。',
    onOk: async () => { try { await tripOperations.voidExpense(id, expenseId, '用户从工作台撤销'); await loadMoney() } catch (e) { explain(e) } } })
}
async function invite() {
  busy.value = true
  try { await tripOperations.invite(id, inviteName.value.trim(), inviteRole.value); inviteName.value = ''; await loadMembers(); message.success('邀请已发送到站内通知') }
  catch (e) { explain(e) } finally { busy.value = false }
}
function removeMember(userId: number) {
  Modal.confirm({ title: '移除这位同行人？', onOk: async () => {
    try { await tripOperations.removeMember(id, userId); await loadMembers() } catch (e) { explain(e) }
  } })
}
function resetOrder() {
  const day = trip.value?.plan?.days?.[orderDay.value]
  orderItems.value = [...(day?.attractions || [])]
  orderDescription.value = day?.description || ''
}
function openPlan() {
  sessionStorage.setItem('tripPlan', JSON.stringify(trip.value.plan))
  sessionStorage.setItem('tripPlanId', String(id))
  sessionStorage.setItem('tripPlanVersion', String(trip.value.version))
  sessionStorage.setItem('tripQuality', JSON.stringify(trip.value.quality || {}))
  sessionStorage.removeItem('tripUnsaved')
  router.push({ path: '/result', query: { from: 'history' } })
}
function move(index: number, direction: number) { const next = [...orderItems.value]; [next[index], next[index + direction]] = [next[index + direction], next[index]]; orderItems.value = next }
async function saveOrder() {
  busy.value = true
  try { await tripOperations.reorder(id, trip.value.version, orderDay.value, orderItems.value.map(a => a.poi_id), orderDescription.value); await load(); message.success('已保存；路线仍需重新核验') }
  catch (e) { explain(e) } finally { busy.value = false }
}
onMounted(load)
</script>

<style scoped>
.trip-operations { box-sizing: border-box; max-width: 1200px; margin: 0 auto; padding: 30px 20px 64px; color: #1f2937; }
.page-heading { display: flex; align-items: center; gap: 22px; margin-bottom: 18px; padding: 25px 30px; border: 1px solid #e5e7eb; border-radius: 20px; background: #fff; box-shadow: 0 16px 40px rgba(10, 17, 48, .18); }
.back-button { flex: none; }
.heading-copy { min-width: 0; flex: 1; }
.eyebrow { color: #6d5bd0; font-size: 11px; font-weight: 800; letter-spacing: .15em; }
.heading-copy h1 { margin: 5px 0 4px; color: #18243a; font-size: clamp(24px, 3vw, 32px); line-height: 1.3; }
.heading-copy p { margin: 0; color: #58677d; font-size: 14px; }
.heading-meta { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.heading-meta :deep(.ant-tag) { margin-inline-end: 0; }
.error-alert { margin-bottom: 18px; }
.workspace-spin { display: block; }
.workspace-panel { padding: 8px 28px 30px; border: 1px solid #e5e7eb; border-radius: 20px; background: #fff; box-shadow: 0 16px 40px rgba(10, 17, 48, .18); }
.workspace-panel :deep(.ant-tabs-nav) { margin-bottom: 25px; }
.workspace-panel :deep(.ant-tabs-tab) { padding: 15px 5px; font-size: 15px; font-weight: 600; }
.workspace-panel :deep(.ant-tabs-tab-btn) { color: #4b5870; }
.workspace-panel :deep(.ant-tabs-tab-active .ant-tabs-tab-btn) { color: #5146c4; }
.section-intro { margin: 0 0 18px; }
.section-intro h2 { margin: 0 0 5px; color: #18243a; font-size: 21px; line-height: 1.35; }
.section-intro p { margin: 0; color: #58677d; line-height: 1.65; }
.context-alert { margin-bottom: 22px; border-radius: 10px; }
.check-controls { display: flex; flex-wrap: wrap; align-items: end; gap: 14px 20px; margin-bottom: 26px; padding: 20px; border: 1px solid #dfe4f3; border-radius: 14px; background: #f7f8ff; }
.day-field { display: flex; flex-direction: column; gap: 7px; min-width: 150px; }
.day-field label { color: #344054; font-size: 13px; font-weight: 700; }
.day-field :deep(.ant-select) { width: 150px; }
.check-actions { display: flex; flex-wrap: wrap; gap: 10px; }
.results-section { overflow: hidden; border: 1px solid #e0e5ee; border-radius: 14px; background: #f9fafc; }
.results-heading { display: flex; align-items: center; justify-content: space-between; gap: 14px; padding: 18px 22px; border-bottom: 1px solid #e0e5ee; }
.results-heading h3 { margin: 0 0 4px; color: #1f2937; font-size: 17px; }
.results-heading p { margin: 0; color: #667085; font-size: 13px; line-height: 1.5; }
.results-heading :deep(.ant-tag) { flex: none; margin-inline-end: 0; }
.risk-list { display: grid; gap: 12px; padding: 16px; }
.risk-item { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 18px 20px; border: 1px solid #e5e8ef; border-left: 4px solid #e9a23b; border-radius: 10px; background: #fff; }
.risk-copy { min-width: 0; }
.risk-title-row { display: flex; align-items: center; flex-wrap: wrap; gap: 8px; }
.risk-title-row h4 { margin: 0; color: #243047; font-size: 16px; line-height: 1.45; }
.risk-title-row :deep(.ant-tag) { margin-inline-end: 0; }
.risk-copy p { margin: 8px 0 0; color: #475467; line-height: 1.6; }
.risk-action { flex: none; }
.risk-action :deep(.ant-tag) { margin-inline-end: 0; }
.check-empty { padding: 25px 0; }
.footnote { margin: 15px 2px 0; color: #5f6b7e; font-size: 13px; line-height: 1.65; }
.summary-grid { margin-bottom: 22px; }
.stat-tile { min-height: 102px; padding: 18px; border: 1px solid #e1e7f0; border-radius: 12px; background: #f7f9ff; }
.stat-tile :deep(.ant-statistic-title) { color: #596579; }
.stat-tile :deep(.ant-statistic-content) { color: #243047; font-size: 23px; }
/* Ant Space wrap writes margin-bottom: -8px inline; keep forms clear of the following list. */
.toolbar { display: flex; width: 100%; flex-wrap: wrap; align-items: center; box-sizing: border-box; margin-top: 18px; margin-bottom: 24px !important; }
.section { margin-top: 20px; }
.workspace-panel :deep(.ant-card) { border-color: #e1e6ef; border-radius: 14px; }
.workspace-panel :deep(.ant-card-head-title) { color: #26334a; font-size: 17px; }
.workspace-panel :deep(.ant-list) { border-color: #e2e7ef; border-radius: 10px; }
.workspace-panel :deep(.ant-list-item-meta-title) { color: #25324a; }
.workspace-panel :deep(.ant-list-item-meta-description) { color: #606e80; }
@media (max-width: 720px) {
  .trip-operations { padding: 14px 12px 40px; }
  .page-heading { align-items: flex-start; flex-direction: column; gap: 12px; padding: 20px; }
  .heading-meta { justify-content: flex-start; }
  .workspace-panel { padding: 4px 16px 22px; }
  .check-controls { padding: 16px; }
  .check-actions { width: 100%; }
  .check-actions :deep(.ant-btn) { white-space: normal; height: auto; min-height: 34px; }
  .results-heading, .risk-item { align-items: flex-start; flex-direction: column; }
  .risk-action { align-self: flex-start; }
}
</style>
