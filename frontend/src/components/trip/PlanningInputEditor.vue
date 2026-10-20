<template>
  <section class="planning-editor" data-testid="planning-editor" aria-label="时间与固定安排">
    <p class="intro">填写已知的时间与位置。预约和跨城交通以你确认的信息为准；未知费用和营业信息会保留为待核实。</p>
    <fieldset :disabled="disabled">
      <div class="field-grid">
        <label>每日最早出发<input v-model="draft.day_start" type="time" required aria-label="每日最早出发" /></label>
        <label>每日最晚结束<input v-model="draft.day_end" type="time" required aria-label="每日最晚结束" /></label>
        <label>首日抵达时间<input :value="localTime(draft.arrival_at)" type="datetime-local" @input="draft.arrival_at = timestamp($event)" /></label>
        <label>末日离开时间<input :value="localTime(draft.departure_at)" type="datetime-local" @input="draft.departure_at = timestamp($event)" /></label>
        <label>预算口径<select v-model="draft.budget_mode"><option value="estimate">允许估算，未知单列</option><option value="confirmed">按确认费用校验</option></select></label>
        <label>总预算（元，可留空）<input :value="draft.budget_cents == null ? '' : draft.budget_cents / 100" type="number" min="0" step="0.01" @input="setBudget" /></label>
      </div>
      <p class="hint">时间使用 {{ draft.timezone }}。抵达／离开时间不代表已预订交通。</p>
      <a-collapse ghost>
        <a-collapse-panel key="days" header="每日特殊时间与城市分段">
          <div v-for="(item, i) in draft.day_windows" :key="i" class="row-fields">
            <label>日期<select v-model.number="item.day_index"><option v-for="d in days" :key="d" :value="d - 1">第 {{ d }} 天</option></select></label>
            <label>出发<input v-model="item.start" type="time" required /></label><label>结束<input v-model="item.end" type="time" required /></label>
            <button type="button" class="remove" @click="draft.day_windows.splice(i, 1)">移除时间例外</button>
          </div>
          <a-button size="small" @click="draft.day_windows.push({ day_index: 0, start: draft.day_start, end: draft.day_end })">添加每日时间例外</a-button>
          <div v-for="(item, i) in draft.city_segments" :key="i" class="row-fields">
            <label>城市<input v-model="item.city" placeholder="例如：杭州" required /></label>
            <label>开始日<select v-model.number="item.start_day"><option v-for="d in days" :key="d" :value="d - 1">第 {{ d }} 天</option></select></label>
            <label>结束日<select v-model.number="item.end_day"><option v-for="d in days" :key="d" :value="d - 1">第 {{ d }} 天</option></select></label>
            <button type="button" class="remove" @click="draft.city_segments.splice(i, 1)">移除城市分段</button>
          </div>
          <a-button size="small" @click="draft.city_segments.push({ city: '', start_day: 0, end_day: days - 1 })">添加城市分段</a-button>
        </a-collapse-panel>
        <a-collapse-panel key="fixed" header="固定预约与已确认跨城交通">
          <a-alert v-if="commitmentsReadOnly" type="info" message="已保存行程的固定事项请到“预订与费用”管理；此处用于查看规划依赖。" />
          <fieldset :disabled="commitmentsReadOnly">
          <p class="hint">只记录你提供的安排，不查询班次或完成购票。交通请在标题写明班次和出发／到达站；起终点位置分别填写。取消的事项不占用时间。</p>
          <article v-for="(item, i) in draft.commitments" :key="item.id" class="entry" :aria-label="`固定事项 ${i + 1}`">
            <div class="row-fields">
              <label>名称／班次<input v-model="item.title" placeholder="例如 G123 杭州东 → 上海虹桥" required /></label>
              <label>类型<select v-model="item.category"><option value="ticket">景点预约</option><option value="transport">跨城交通</option><option value="meal">餐饮预订</option><option value="lodging">住宿预订</option><option value="other">其他</option></select></label>
              <label>日期<select v-model.number="item.day_index"><option v-for="d in days" :key="d" :value="d - 1">第 {{ d }} 天</option></select></label>
              <label>状态<select v-model="item.status"><option value="confirmed">我已确认</option><option value="planned">待确认</option><option value="cancelled">已取消</option></select></label>
            </div>
            <div class="row-fields">
              <label v-if="item.category !== 'transport'">时间含义<select :value="item.time_mode || 'fixed'" @change="setTimeMode(item, $event)"><option value="fixed">固定活动时段</option><option value="entry_window">允许入场的时间段</option></select></label>
              <label v-if="item.time_mode === 'entry_window'">入场后活动时长（分钟）<input v-model.number="item.duration_minutes" type="number" min="1" max="1440" placeholder="未关联活动时默认 60 分钟" /></label>
            </div>
            <p v-if="item.time_mode === 'entry_window'" class="hint">开始／结束限定允许入场的时间，游览可以在入场窗口结束后继续；关联景点时使用景点游览时长。</p>
            <div class="row-fields">
              <label>开始／发车<input :value="localTime(item.start_at)" type="datetime-local" @input="item.start_at = timestamp($event)" /></label>
              <label>结束／抵达<input :value="localTime(item.end_at)" type="datetime-local" @input="item.end_at = timestamp($event)" /></label>
              <label>费用（元，空为未知）<input :value="item.amount_cents == null ? '' : item.amount_cents / 100" type="number" min="0" step="0.01" @input="item.amount_cents = money($event)" /></label>
            </div>
            <div class="row-fields">
              <label>关联活动<select v-model="item.activity_id"><option value="">不关联</option><option v-for="a in activities" :key="a.id" :value="a.id">{{ a.name }}</option></select></label>
              <label>地点 POI 编号<input v-model="item.poi_id" placeholder="可留空，保留待核实" /></label>
              <label>经度<input :value="item.location?.longitude ?? ''" type="number" min="-180" max="180" step="any" @input="coordinate(item, 'longitude', $event)" /></label>
              <label>纬度<input :value="item.location?.latitude ?? ''" type="number" min="-90" max="90" step="any" @input="coordinate(item, 'latitude', $event)" /></label>
            </div>
            <div v-if="item.category === 'transport'" class="entry">
              <p class="hint">跨城起终点分别参与接驳计算；未知位置不会被视为零分钟交通。</p>
              <div class="row-fields"><label>出发城市<input v-model="item.origin_city" /></label><label>出发站／地点<input v-model="item.origin_name" /></label><label>出发经度<input :value="item.origin_location?.longitude ?? ''" type="number" step="any" @input="endpoint(item, 'origin_location', 'longitude', $event)" /></label><label>出发纬度<input :value="item.origin_location?.latitude ?? ''" type="number" step="any" @input="endpoint(item, 'origin_location', 'latitude', $event)" /></label></div>
              <div class="row-fields"><label>到达城市<input v-model="item.destination_city" /></label><label>到达站／地点<input v-model="item.destination_name" /></label><label>到达经度<input :value="item.destination_location?.longitude ?? ''" type="number" step="any" @input="endpoint(item, 'destination_location', 'longitude', $event)" /></label><label>到达纬度<input :value="item.destination_location?.latitude ?? ''" type="number" step="any" @input="endpoint(item, 'destination_location', 'latitude', $event)" /></label></div>
            </div>
            <button type="button" class="remove" @click="draft.commitments.splice(i, 1)">移除事项</button>
          </article>
          <a-button @click="addCommitment">添加固定事项</a-button>
          </fieldset>
        </a-collapse-panel>
        <a-collapse-panel key="hotels" header="酒店起终点与用餐窗口">
          <div v-for="(hotel, i) in draft.hotel_stays" :key="i" class="entry">
            <div class="row-fields">
              <label>住宿日<select v-model.number="hotel.day_index"><option v-for="d in days" :key="d" :value="d - 1">第 {{ d }} 天</option></select></label>
              <label>酒店名称<input v-model="hotel.name" required /></label><label>POI 编号<input v-model="hotel.poi_id" /></label>
              <label>经度<input :value="hotel.location?.longitude ?? ''" type="number" min="-180" max="180" step="any" @input="coordinate(hotel, 'longitude', $event)" /></label>
              <label>纬度<input :value="hotel.location?.latitude ?? ''" type="number" min="-90" max="90" step="any" @input="coordinate(hotel, 'latitude', $event)" /></label>
              <button type="button" class="remove" @click="draft.hotel_stays.splice(i, 1)">移除酒店</button>
            </div>
          </div>
          <a-button size="small" @click="draft.hotel_stays.push({ day_index: 0, name: '' })">添加住宿位置</a-button>
          <div v-for="(meal, i) in draft.meal_windows" :key="i" class="row-fields">
            <label>用餐<select v-model="meal.type"><option value="breakfast">早餐</option><option value="lunch">午餐</option><option value="dinner">晚餐</option></select></label>
            <label>开始<input v-model="meal.start" type="time" /></label><label>结束<input v-model="meal.end" type="time" /></label>
            <label>用时（分钟）<input v-model.number="meal.duration_minutes" type="number" min="10" max="240" /></label>
            <button type="button" class="remove" @click="draft.meal_windows.splice(i, 1)">移除用餐窗口</button>
          </div>
          <a-button size="small" @click="draft.meal_windows.push({ type: 'lunch', start: '11:30', end: '14:00', duration_minutes: 60 })">添加用餐窗口</a-button>
        </a-collapse-panel>
        <a-collapse-panel key="facts" header="有来源的营业时间（可选）">
          <p class="hint">未填写不代表全天营业。特殊日期请参考景区官方公告；多个开放区间可逐项添加。</p>
          <article v-for="(fact, i) in draft.opening_windows" :key="i" class="entry">
            <div class="row-fields">
              <label>景点 POI<input v-model="fact.poi_id" required /></label><label>适用日期<input v-model="fact.date" type="date" required /></label>
              <label>来源链接／名称<input v-model="fact.source" required /></label>
              <label>最后入场<input v-model="fact.last_entry" type="time" /></label>
              <label class="check"><input v-model="fact.closed" type="checkbox" /> 当日关闭</label><label class="check"><input v-model="fact.confirmed" type="checkbox" /> 已人工核对</label>
            </div>
            <div class="row-fields"><label>获取时间<input :value="localTime(fact.observed_at)" type="datetime-local" @input="fact.observed_at = timestamp($event) || undefined" /></label><label>有效至<input :value="localTime(fact.expires_at)" type="datetime-local" @input="fact.expires_at = timestamp($event) || undefined" /></label></div>
            <div v-for="(window, j) in fact.windows" :key="j" class="row-fields"><label>开放<input v-model="window.start" type="time" /></label><label>闭馆<input v-model="window.end" type="time" /></label><button type="button" class="remove" @click="fact.windows.splice(j, 1)">移除区间</button></div>
            <a-space><a-button size="small" @click="fact.windows.push({ start: '09:00', end: '17:00' })">增加开放区间</a-button><a-button size="small" danger @click="draft.opening_windows.splice(i, 1)">移除营业事实</a-button></a-space>
          </article>
          <a-button size="small" @click="draft.opening_windows.push({ poi_id: '', date: startDate || '', windows: [{ start: '09:00', end: '17:00' }], closed: false, source: '', confirmed: false, observed_at: new Date().toISOString() })">添加营业事实</a-button>
        </a-collapse-panel>
      </a-collapse>
    </fieldset>
    <a-alert v-if="validationMessage" type="warning" :message="validationMessage" show-icon />
  </section>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PlanningInput, Location, PlanningCommitment } from '@/types'
const props = withDefaults(defineProps<{ modelValue?: PlanningInput; days?: number; startDate?: string; disabled?: boolean; commitmentsReadOnly?: boolean; activities?: { id: string; name: string }[] }>(), { days: 1, disabled: false, commitmentsReadOnly: false, activities: () => [] })
const emit = defineEmits<{ 'update:modelValue': [value: PlanningInput] }>()
const normalize = (value?: PlanningInput): PlanningInput => ({ timezone: 'Asia/Shanghai', day_start: '09:00', day_end: '20:00', day_windows: [], city_segments: [], commitments: [], hotel_stays: [], meal_windows: [], opening_windows: [], locks: [], completed_activity_ids: [], budget_mode: 'estimate', ...JSON.parse(JSON.stringify(value || {})) })
const draft = ref(normalize(props.modelValue))
watch(() => props.modelValue, value => { const next = normalize(value); if (JSON.stringify(next) !== JSON.stringify(draft.value)) draft.value = next }, { deep: true })
watch(draft, value => emit('update:modelValue', JSON.parse(JSON.stringify(value))), { deep: true, immediate: true })
const localTime = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)).replace(' ', 'T') : ''
const timestamp = (event: Event) => { const value = (event.target as HTMLInputElement).value; return value ? `${value}:00+08:00` : null }
const money = (event: Event) => { const value = (event.target as HTMLInputElement).value; return value === '' ? null : Math.round(Number(value) * 100) }
const setBudget = (event: Event) => { draft.value.budget_cents = money(event) }
function coordinate(item: { location?: Location | null }, key: keyof Location, event: Event) {
  const raw = (event.target as HTMLInputElement).value
  const next = { ...(item.location || {}), [key]: raw === '' ? undefined : Number(raw) }
  item.location = next.longitude == null && next.latitude == null ? null : next as Location
}
function endpoint(item: PlanningCommitment, field: 'origin_location' | 'destination_location', key: keyof Location, event: Event) { const holder = { location: item[field] }; coordinate(holder, key, event); item[field] = holder.location }
function setTimeMode(item: PlanningCommitment, event: Event) { item.time_mode = (event.target as HTMLSelectElement).value as 'fixed' | 'entry_window' }
function addCommitment() { draft.value.commitments.push({ id: crypto.randomUUID(), version: 1, activity_id: '', day_index: 0, category: 'ticket', status: 'confirmed', title: '', amount_cents: null, time_mode: 'fixed' }) }
const validationMessage = computed(() => {
  const value = draft.value
  if (value.day_start >= value.day_end) return '每日结束时间须晚于出发时间。'
  if (value.arrival_at && value.departure_at && Date.parse(value.arrival_at) >= Date.parse(value.departure_at)) return '离开时间须晚于抵达时间。'
  if (value.budget_cents != null && (!Number.isSafeInteger(value.budget_cents) || value.budget_cents < 0)) return '预算须为非负金额。'
  if (value.day_windows.some(w => w.day_index >= props.days || w.start >= w.end)) return '请检查每日时间例外的日期和起止时间。'
  if (value.city_segments.some(s => !s.city.trim() || s.start_day > s.end_day || s.end_day >= props.days)) return '请完善城市分段，日期必须处于行程范围内。'
  if (value.commitments.some(c => !c.title.trim() || (c.start_at && c.end_at && Date.parse(c.start_at) >= Date.parse(c.end_at)))) return '请完善固定事项名称，并检查开始／结束时间。'
  if (value.commitments.some(c => c.day_index >= props.days || (!!c.start_at !== !!c.end_at))) return '请检查固定事项日期，并成对填写开始／结束时间。'
  if (value.hotel_stays.some(h => !h.name.trim() || h.day_index >= props.days)) return '请完善住宿名称，日期须处于行程范围内。'
  if (value.meal_windows.some(w => w.start >= w.end || !Number.isFinite(w.duration_minutes) || w.duration_minutes <= 0)) return '请检查用餐窗口的起止时间和时长。'
  const locations = [...value.hotel_stays.map(h => h.location), ...value.commitments.flatMap(c => [c.location, c.origin_location, c.destination_location])]
  if (locations.some(l => l != null && (!Number.isFinite(l.longitude) || !Number.isFinite(l.latitude) || Math.abs(l.longitude) > 180 || Math.abs(l.latitude) > 90))) return '地点须成对填写有效经纬度，未知位置请将两个字段都留空。'
  if (value.opening_windows.some(f => !f.poi_id || !f.date || !f.source)) return '营业事实须填写景点、适用日期及来源。'
  return ''
})
defineExpose({ validate: () => validationMessage.value })
</script>

<style scoped>
.planning-editor { color: #26334a; }
.intro,.hint { color: #606e80; line-height: 1.65; margin: 0 0 14px; }
.hint { font-size: 12px; margin-top: 10px; }
fieldset { min-width: 0; border: 0; padding: 0; margin: 0; }
.field-grid { display: grid; grid-template-columns: repeat(3,minmax(0,1fr)); gap: 16px; }
.row-fields { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0; align-items: end; }
label { display: flex; flex-direction: column; gap: 6px; font-size: 13px; font-weight: 500; flex: 1; min-width: 130px; }
input,select { width: 100%; min-height: 35px; box-sizing: border-box; border: 1px solid #d6dce6; border-radius: 6px; background: #fff; padding: 6px 9px; color: #26334a; font: inherit; }
input:focus,select:focus { outline: 2px solid #b6c8ee; outline-offset: 1px; }
.entry { padding: 12px 16px; margin: 12px 0; border: 1px solid #e0e5ee; border-radius: 8px; background: #fafbfc; }
.remove { background: transparent; border: 0; color: #a83c43; cursor: pointer; padding: 8px 0; }
.check { flex-direction: row; align-items: center; }.check input { width: auto; min-height: 0; }
@media(max-width:720px) { .field-grid { grid-template-columns: 1fr; }.entry{padding:10px;}.row-fields label { min-width: 115px; } }
</style>
