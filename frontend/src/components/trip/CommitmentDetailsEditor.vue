<template>
  <div class="commitment-details">
    <p>已确认的时间与位置参与规划校验。这里记录你提供的信息，不完成预订或购票。</p>
    <div class="fields">
      <label v-if="modelValue.category !== 'transport'">时间含义<select :value="modelValue.time_mode || 'fixed'" @change="field('time_mode', $event)"><option value="fixed">固定活动时段</option><option value="entry_window">允许入场的时间段</option></select></label>
      <label v-if="modelValue.time_mode === 'entry_window'">入场后活动时长（分钟）<input :value="modelValue.duration_minutes ?? ''" type="number" min="1" max="1440" placeholder="未关联活动时默认 60" @input="duration($event)" /></label>
      <label>开始／发车时间<input :value="local(modelValue.start_at)" type="datetime-local" @input="date('start_at', $event)" /></label>
      <label>结束／抵达时间<input :value="local(modelValue.end_at)" type="datetime-local" @input="date('end_at', $event)" /></label>
      <label>关联景点<select :value="modelValue.activity_id || ''" @change="field('activity_id', $event)"><option value="">不关联</option><option v-for="a in activities" :key="a.id" :value="a.id">{{ a.name }}</option></select></label>
      <label>地点 POI 编号<input :value="modelValue.poi_id" @input="field('poi_id', $event)" /></label>
      <label>地点经度<input :value="modelValue.location?.longitude ?? ''" type="number" min="-180" max="180" step="any" @input="coordinate('location', 'longitude', $event)" /></label>
      <label>地点纬度<input :value="modelValue.location?.latitude ?? ''" type="number" min="-90" max="90" step="any" @input="coordinate('location', 'latitude', $event)" /></label>
    </div>
    <p v-if="modelValue.time_mode === 'entry_window'">开始／结束是允许入场的时间范围，活动可在窗口结束后继续。关联景点时使用该景点的游览时长。</p>
    <template v-if="modelValue.category === 'transport'">
      <h4>已确认跨城交通的起终点</h4>
      <div class="fields">
        <label>班次<input :value="modelValue.service_number" @input="field('service_number', $event)" /></label>
        <label>出发城市<input :value="modelValue.origin_city" @input="field('origin_city', $event)" /></label>
        <label>出发站／地点<input :value="modelValue.origin_name" @input="field('origin_name', $event)" /></label>
        <label>出发经度<input :value="modelValue.origin_location?.longitude ?? ''" type="number" step="any" @input="coordinate('origin_location', 'longitude', $event)" /></label>
        <label>出发纬度<input :value="modelValue.origin_location?.latitude ?? ''" type="number" step="any" @input="coordinate('origin_location', 'latitude', $event)" /></label>
        <label>到达城市<input :value="modelValue.destination_city" @input="field('destination_city', $event)" /></label>
        <label>到达站／地点<input :value="modelValue.destination_name" @input="field('destination_name', $event)" /></label>
        <label>到达经度<input :value="modelValue.destination_location?.longitude ?? ''" type="number" step="any" @input="coordinate('destination_location', 'longitude', $event)" /></label>
        <label>到达纬度<input :value="modelValue.destination_location?.latitude ?? ''" type="number" step="any" @input="coordinate('destination_location', 'latitude', $event)" /></label>
      </div>
    </template>
  </div>
</template>
<script setup lang="ts">
const props = withDefaults(defineProps<{ modelValue: any; activities?: { id: string; name: string }[] }>(), { activities: () => [] })
const emit = defineEmits<{ 'update:modelValue': [value: any] }>()
const local = (value?: string | null) => value && Number.isFinite(Date.parse(value)) ? new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).format(new Date(value)).replace(' ', 'T') : ''
function field(key: string, event: Event) { emit('update:modelValue', { ...props.modelValue, [key]: (event.target as HTMLInputElement).value }) }
function date(key: string, event: Event) { const value = (event.target as HTMLInputElement).value; emit('update:modelValue', { ...props.modelValue, [key]: value ? `${value}:00+08:00` : null }) }
function duration(event: Event) { const value = (event.target as HTMLInputElement).value; emit('update:modelValue', { ...props.modelValue, duration_minutes: value === '' ? undefined : Number(value) }) }
function coordinate(key: string, axis: string, event: Event) { const value = (event.target as HTMLInputElement).value; const point = { ...props.modelValue[key], [axis]: value === '' ? undefined : Number(value) }; emit('update:modelValue', { ...props.modelValue, [key]: point.longitude == null && point.latitude == null ? null : point }) }
</script>
<style scoped>
.commitment-details{margin:14px 0;padding:15px;border:1px solid #dfe5ed;border-radius:8px;background:#fafbfc;}.commitment-details p{font-size:13px;color:#667085;line-height:1.65;}h4{margin:16px 0 10px;}.fields{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;}label{display:flex;flex-direction:column;gap:6px;font-size:13px;color:#344054;}input,select{padding:6px 8px;min-height:35px;border:1px solid #d6dce6;border-radius:6px;box-sizing:border-box;width:100%;color:#26334a;background:#fff;}@media(max-width:720px){.fields{grid-template-columns:1fr;}}
</style>
