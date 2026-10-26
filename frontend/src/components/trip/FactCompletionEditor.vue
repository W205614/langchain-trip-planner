<template>
  <section class="fact-completion" aria-label="补齐事实并局部重验" data-testid="fact-completion">
    <h3>补齐事实，再核验这一天</h3>
    <p>由行程所有者按景区、承运方或自己的安排补充。系统记录为用户声明；填写来源不代表系统已验证官方信息。</p>
    <label>处理日期<select v-model.number="dayIndex"><option v-for="day in plan.days" :key="day.day_index" :value="day.day_index">第 {{ day.day_index + 1 }} 天 · {{ day.city || plan.city }}</option></select></label>
    <ul v-if="gaps.length"><li v-for="gap in gaps" :key="gap">{{ gap }}</li></ul>
    <article v-for="finding in findings" :key="finding.activity_id || finding.poi_id" class="finding">
      <strong>{{ finding.status === 'not_found' ? '地点明确不存在，待替换确认' : '本次地点核验失败，可重试' }}</strong><p>{{ finding.message }}</p>
      <a-button v-if="finding.retryable" :disabled="busy" @click="emit('retry')">重试地图核验</a-button>
      <a-button v-if="finding.status === 'not_found'" @click="emit('replace', finding.poi_id)">选择替代景点并确认</a-button>
    </article>
    <div class="fields">
      <label>补充类型<select v-model="kind"><option value="opening">营业与最后入场</option><option value="reservation">预约要求与结果</option><option value="hotel">住宿接驳地点</option><option value="boarding">提前到站要求</option><option value="meal">自理用餐地点</option><option value="relation">父子景点与重复游览</option></select></label>
      <label v-if="['opening', 'reservation', 'relation'].includes(kind)">{{ kind === 'relation' ? '父景点' : '地点' }}<select v-model="poiId"><option value="">请选择地点</option><option v-for="poi in places" :key="poi.poi_id" :value="poi.poi_id">{{ poi.name }}</option></select></label>
      <label v-if="kind === 'relation'">子景点<select v-model="childPoiId"><option value="">请选择子地点</option><option v-for="poi in places.filter(p => p.poi_id !== poiId)" :key="poi.poi_id" :value="poi.poi_id">{{ poi.name }}</option></select></label>
      <label v-if="kind === 'relation'">游览关系<select v-model="relation"><option value="included">子景点已包含在父景点游览内，避免重复安排</option><option value="separate">已核对为独立游览，允许分别安排</option></select></label>
      <template v-if="kind === 'opening'"><label>开放<input v-model="openingStart" type="time" /></label><label>结束<input v-model="openingEnd" type="time" /></label><label>最后入场<input v-model="lastEntry" type="time" /></label><label><input v-model="closed" type="checkbox" />该日闭馆</label></template>
      <label v-if="kind === 'reservation'">预约状态<select v-model="reservationStatus"><option value="not_required">已核对不需要预约</option><option value="confirmed">我已完成预约</option><option value="unavailable">尚未取得可用预约</option></select></label>
      <p v-if="kind === 'reservation'">预约状态不会代替入场时段。有指定入场时间时，请在“预订与费用”关联景点并填写固定时段或入场窗口，再重新核验。</p>
      <label v-if="kind === 'hotel'">住宿名称<input v-model="hotelName" placeholder="我确认的酒店或住宿" /></label>
      <template v-if="kind === 'boarding'"><label>交通事项<select v-model="commitmentId"><option value="">请选择已确认交通</option><option v-for="trip in transports" :key="trip.id" :value="String(trip.id)">{{ trip.title }}</option></select></label><label>提前到站（分钟）<input v-model.number="minutes" type="number" min="0" max="360" /></label></template>
      <label v-if="kind === 'meal'">餐别<select v-model="mealType"><option value="breakfast">早餐</option><option value="lunch">午餐</option><option value="dinner">晚餐</option></select></label>
      <template v-if="kind === 'hotel' || kind === 'meal'"><label>经度<input v-model.number="longitude" type="number" step="any" min="-180" max="180" /></label><label>纬度<input v-model.number="latitude" type="number" step="any" min="-90" max="90" /></label></template>
      <label>来源名称或链接<input v-model="source" maxlength="1000" placeholder="例如景区公告、承运方通知、自己的住宿确认" /></label>
      <label>获取时间<input v-model="observed" type="datetime-local" /></label><label>有效至<input v-model="expiry" type="datetime-local" /></label>
    </div>
    <label class="confirm"><input v-model="confirmed" type="checkbox" />我已核对以上信息，了解这是我的声明，系统不会代订票或确认官方真实性</label>
    <p v-if="error" role="alert" class="error">{{ error }}</p>
    <a-space wrap><a-button :disabled="busy" @click="addFact">加入待保存事实</a-button><a-button type="primary" :loading="busy" @click="emit('recheck', dayIndex)">保存已补充事实并局部重验求解</a-button></a-space>
    <p>已补充 {{ factCount }} 项。重验会生成待确认方案；先查看差异再应用，锁定和已完成安排继续受保护。</p>
    <details v-if="savedFacts.length"><summary>检查已加入的补充事实、来源与有效期</summary><ul><li v-for="entry in savedFacts" :key="`${entry.group}:${entry.index}`"><strong>{{ entry.label }}</strong> · {{ entry.source }} · 有效至 {{ entry.expiry }} · 用户声明 <button type="button" :disabled="busy" @click="removeFact(entry.group, entry.index)">移除待保存事实</button></li></ul></details>
  </section>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { PlanningInput, TripPlan, TravelFact } from '@/types'
const props = defineProps<{ modelValue: PlanningInput; plan: TripPlan; quality?: any; busy?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: PlanningInput]; recheck: [day: number]; retry: []; replace: [poiId: string] }>()
const dayIndex = ref(0), kind = ref('opening'), poiId = ref(''), childPoiId = ref(''), relation = ref<'included' | 'separate'>('included')
const openingStart = ref('09:00'), openingEnd = ref('17:00'), lastEntry = ref(''), closed = ref(false)
const reservationStatus = ref<'confirmed' | 'not_required' | 'unavailable'>('not_required'), hotelName = ref(''), commitmentId = ref(''), minutes = ref(30), mealType = ref('lunch')
const longitude = ref<number | undefined>(), latitude = ref<number | undefined>(), source = ref(''), confirmed = ref(false), error = ref('')
const localDateTime = (value: Date) => new Intl.DateTimeFormat('sv-SE', { timeZone: props.modelValue.timezone || 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(value).replace(' ', 'T')
const observed = ref(localDateTime(new Date())), expiry = ref(localDateTime(new Date(Date.now() + 6 * 3600000)))
const date = computed(() => props.plan.days.find(d => d.day_index === dayIndex.value)?.date || props.plan.start_date)
const places = computed(() => { const day = props.plan.days.find(d => d.day_index === dayIndex.value); return [...(day?.attractions || []), ...(day?.meals || [])].filter(p => p.poi_id) })
const transports = computed(() => props.modelValue.commitments.filter(c => c.category === 'transport' && c.status === 'confirmed'))
const findings = computed(() => (props.quality?.poi_verification || []).filter((f: any) => f.day_index === dayIndex.value && f.status !== 'verified'))
const factCount = computed(() => ['reservation_facts', 'transport_buffers', 'poi_relations', 'meal_resolutions', 'opening_windows', 'hotel_stays'].reduce((n, key) => n + ((props.modelValue as any)[key]?.length || 0), 0))
const savedFacts = computed(() => {
  const labels: Record<string, string> = { reservation_facts: '预约核对', transport_buffers: '提前到站', poi_relations: '父子景点', meal_resolutions: '自理用餐' }
  return Object.entries(labels).flatMap(([group, label]) => ((props.modelValue as any)[group] || []).map((item: any, index: number) => ({ group, index, label: `${label} · ${item.poi_id || item.commitment_id || item.parent_poi_id || `第${item.day_index + 1}天`}`, source: item.source, expiry: item.expires_at })))
})
const gaps = computed(() => {
  const day = props.plan.days.find(d => d.day_index === dayIndex.value), unknown: string[] = day?.feasibility?.unknown || []
  const result: string[] = []
  if (unknown.some(x => x.startsWith('opening_') || x.startsWith('meal_opening'))) result.push('营业资料：按景区或餐厅公告补充本日开放区间、最后入场及有效期。')
  if (unknown.some(x => x.startsWith('reservation_'))) result.push('预约要求：由出行人核对是否需要预约，以及是否已经取得预约。')
  if (unknown.some(x => x.includes('hotel') || x.includes('endpoint'))) result.push('住宿或接驳地点：补充实际住宿位置；站点请在“预订与费用”填写交通起终点。')
  if (unknown.some(x => x.includes('predeparture'))) result.push('提前到站要求：向承运方确认分钟数，系统将把这段时间计入整日预算。')
  if (unknown.some(x => x.includes('meal_')) || (day?.meals || []).length < 3) result.push('餐饮缺口：补充自理用餐的地点，或到行程详情选择可信餐厅；仅标记已知悉不会解决缺口。')
  if (unknown.some(x => x.includes('relationship'))) result.push('父子景点：核对是否属于同一景区、是否已包含游览；不同地图编号不代表可以重复安排。')
  return result
})
watch(dayIndex, () => { poiId.value = ''; childPoiId.value = ''; error.value = '' })
function removeFact(group: string, index: number) { const next = JSON.parse(JSON.stringify(props.modelValue)); next[group].splice(index, 1); emit('update:modelValue', next) }
function addFact() {
  error.value = ''
  const offset = props.modelValue.timezone === 'Asia/Shanghai' ? '+08:00' : ''
  if (!offset) { error.value = '本补充入口目前仅支持国内时区，请在时间与约束中使用 Asia/Shanghai。'; return }
  const stamp = (value: string) => `${value}:00${offset}`
  if (!source.value.trim() || !confirmed.value || !observed.value || !expiry.value || Date.parse(stamp(expiry.value)) <= Date.parse(stamp(observed.value))) { error.value = '请填写来源、有效的获取时间与有效期，并明确核对声明。'; return }
  const next: PlanningInput = JSON.parse(JSON.stringify(props.modelValue)), fact: TravelFact = { source: source.value.trim(), confirmed: true, observed_at: stamp(observed.value), expires_at: stamp(expiry.value), authority: 'user_declared' }
  if (['opening', 'reservation', 'relation'].includes(kind.value) && !poiId.value) { error.value = '请选择要补充的地点。'; return }
  const upsert = (key: string, match: (entry: any) => boolean, value: any) => { const list = (next as any)[key] || []; (next as any)[key] = [...list.filter((entry: any) => !match(entry)), value] }
  if (kind.value === 'opening') {
    if (!closed.value && openingStart.value >= openingEnd.value) { error.value = '营业结束须晚于开始。'; return }
    upsert('opening_windows', x => x.poi_id === poiId.value && x.date === date.value, { ...fact, poi_id: poiId.value, date: date.value, windows: closed.value ? [] : [{ start: openingStart.value, end: openingEnd.value }], last_entry: lastEntry.value || null, closed: closed.value })
  } else if (kind.value === 'reservation') upsert('reservation_facts', x => x.poi_id === poiId.value && x.date === date.value, { ...fact, poi_id: poiId.value, date: date.value, status: reservationStatus.value })
  else if (kind.value === 'boarding') {
    if (!commitmentId.value || !Number.isInteger(minutes.value) || minutes.value < 0 || minutes.value > 360) { error.value = '请选择交通并填写0至360分钟的提前量。'; return }
    upsert('transport_buffers', x => String(x.commitment_id) === commitmentId.value, { ...fact, commitment_id: commitmentId.value, minutes: minutes.value })
  } else if (kind.value === 'relation') {
    if (!childPoiId.value || childPoiId.value === poiId.value) { error.value = '请选择不同的父子地点。'; return }
    upsert('poi_relations', x => [x.parent_poi_id, x.child_poi_id].sort().join(':') === [poiId.value, childPoiId.value].sort().join(':'), { ...fact, parent_poi_id: poiId.value, child_poi_id: childPoiId.value, relation: relation.value })
  } else {
    if (longitude.value == null || latitude.value == null || !Number.isFinite(longitude.value) || !Number.isFinite(latitude.value) || Math.abs(longitude.value) > 180 || Math.abs(latitude.value) > 90) { error.value = '请填写实际地点的有效经纬度。'; return }
    const location = { longitude: longitude.value, latitude: latitude.value }
    if (kind.value === 'hotel') { if (!hotelName.value.trim()) { error.value = '请填写住宿名称。'; return }; upsert('hotel_stays', x => x.day_index === dayIndex.value, { ...fact, day_index: dayIndex.value, name: hotelName.value.trim(), location }) }
    else upsert('meal_resolutions', x => x.day_index === dayIndex.value && x.type === mealType.value, { ...fact, day_index: dayIndex.value, type: mealType.value, mode: 'self_arranged', location })
  }
  emit('update:modelValue', next)
  confirmed.value = false
}
</script>
<style scoped>
.fact-completion{border:1px solid #dbe1ea;border-radius:10px;padding:20px;margin:20px 0;background:#fafbfc}.fact-completion p,.fact-completion li{font-size:13px;line-height:1.7;color:#58677d}.fields{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:14px 0}label{display:flex;flex-direction:column;gap:6px;font-size:13px}select,input{padding:8px;border:1px solid #cbd5e1;border-radius:5px;max-width:100%;box-sizing:border-box}.confirm{flex-direction:row;align-items:center;margin:15px 0}.error{color:#b42318!important}.finding{padding:12px;border-left:3px solid #d69e2e;margin:10px 0}
</style>
