<template>
  <main class="trip-operations">
    <a-space class="heading">
      <a-button @click="router.push('/history')">← 我的行程</a-button>
      <h1>{{ trip?.city || '行程' }} · 执行工作台</h1>
      <a-tag v-if="trip">版本 {{ trip.version }} · {{ roleLabel }}</a-tag>
    </a-space>
    <a-alert v-if="error" type="error" :message="error" show-icon closable @close="error = ''" />
    <a-spin :spinning="loading">
      <a-tabs v-if="trip" v-model:activeKey="tab">
        <a-tab-pane key="check" tab="行前核验">
          <a-alert type="info" show-icon message="营业信息仅供参考，具体出行日期的开放、预约及天气以官方信息为准。核验不会自动更改行程。" />
          <a-space class="toolbar">
            <a-select v-model:value="checkDay" style="width: 130px" @change="loadCheck"><a-select-option v-for="d in trip.travel_days" :key="d" :value="d - 1">第{{ d }}天</a-select-option></a-select>
            <a-button v-if="trip.role === 'owner'" type="primary" :loading="busy" @click="runCheck">核验景点营业信息</a-button>
            <a-button v-if="trip.role === 'owner'" @click="openPlan">打开行程并按日改排</a-button>
            <a-button @click="loadCheck">刷新结果</a-button>
            <a-tag v-if="check?.status" :color="check.stale ? 'orange' : check.status === 'current' ? 'green' : 'gold'">
              {{ check.stale ? '已过期，请重新核验' : check.status === 'current' ? '当前核验无新增风险' : '有待核验事项' }}
            </a-tag>
          </a-space>
          <a-list :data-source="check?.risks || []" bordered>
            <template #renderItem="{ item }">
              <a-list-item>
                <a-list-item-meta :title="`第 ${item.day_index + 1} 天 · ${item.code}`" :description="item.message" />
                <template #actions>
                  <a-tag v-if="item.acknowledged_at">已知悉</a-tag>
                  <a-button v-else :disabled="check?.stale || trip.role === 'viewer'" @click="acknowledge(item.id)">标记已知悉</a-button>
                </template>
              </a-list-item>
            </template>
          </a-list>
          <p>发现风险后可在行程详情中选择目标日期发起局部改排；“已知悉”不代表该地点已确认营业。</p>
        </a-tab-pane>

        <a-tab-pane key="money" tab="预订与费用">
          <a-row :gutter="16" class="toolbar">
            <a-col :span="6"><a-statistic title="原计划估算" :value="yuan(expenseResult?.summary?.plan_estimate_cents)" prefix="¥" /></a-col>
            <a-col :span="6"><a-statistic title="预算上限" :value="expenseResult?.summary?.budget_limit_cents == null ? '未设置' : yuan(expenseResult.summary.budget_limit_cents)" :prefix="expenseResult?.summary?.budget_limit_cents == null ? '' : '¥'" /></a-col>
            <a-col :span="6"><a-statistic title="已确认预订" :value="yuan(expenseResult?.summary?.confirmed_cents)" prefix="¥" /></a-col>
            <a-col :span="6"><a-statistic title="已记录实际支出" :value="yuan(expenseResult?.summary?.actual_cents)" prefix="¥" /></a-col>
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
            <a-list :data-source="commitments" bordered>
              <template #renderItem="{ item }">
                <a-list-item>
                  <a-list-item-meta :title="`第 ${item.day_index + 1} 天 · ${item.title}`" :description="`${label(item.category)} · ${item.amount_cents == null ? '金额未知' : '¥' + yuan(item.amount_cents)} · ${item.status}`" />
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
            <a-list :data-source="expenseResult?.data || []" bordered>
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
          <a-alert type="info" show-icon message="受邀人接受后才能查看行程；编辑者仅可调整当天已有景点的顺序，保存时校验行程版本。" />
          <a-space v-if="trip.role === 'owner'" wrap class="toolbar">
            <a-input v-model:value="inviteName" placeholder="已注册用户名" style="width: 180px" />
            <a-select v-model:value="inviteRole" style="width: 110px"><a-select-option value="viewer">查看者</a-select-option><a-select-option value="editor">编辑者</a-select-option></a-select>
            <a-button :loading="busy" @click="invite">邀请</a-button>
          </a-space>
          <a-list v-if="trip.role === 'owner'" :data-source="members" bordered>
            <template #renderItem="{ item }">
              <a-list-item><a-list-item-meta :title="item.username" :description="`${item.role} · ${item.status}`" />
                <template #actions><a-button danger @click="removeMember(item.user_id)">移除</a-button></template>
              </a-list-item>
            </template>
          </a-list>
          <a-card v-if="canEdit" title="调整当天景点顺序" class="section">
            <a-select v-model:value="orderDay" style="width: 130px" @change="resetOrder"><a-select-option v-for="d in trip.travel_days" :key="d" :value="d - 1">第{{ d }}天</a-select-option></a-select>
            <a-textarea v-model:value="orderDescription" :maxlength="500" :rows="2" placeholder="当天主题或安排说明" class="section" />
            <a-list :data-source="orderItems" bordered class="section">
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
.trip-operations { max-width: 1100px; margin: 24px auto; padding: 0 20px 40px; }
.heading, .toolbar { margin-bottom: 18px; }
.heading h1 { margin: 0; font-size: 22px; }
.section { margin-top: 20px; }
p { color: #666; margin-top: 12px; }
</style>
