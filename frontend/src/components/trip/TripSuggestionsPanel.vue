<template>
  <section class="suggestions" aria-labelledby="trip-suggestions">
    <h3 id="trip-suggestions">同行建议</h3>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-list :loading="loading" :data-source="items" :locale="{ emptyText: '暂无同行建议' }">
      <template #renderItem="{ item }">
        <a-list-item>
          <div class="suggestion">
            <a-space wrap>
              <strong>{{ item.kind === 'shift_time' ? item.details.title : item.details.location }}</strong>
              <a-tag>{{ states[item.status as TripSuggestion['status']] }}</a-tag>
            </a-space>
            <div>{{ item.trip_date }} · {{ item.author }}</div>
            <div v-if="item.kind === 'shift_time'">
              {{ interval(item.details.old_start, item.details.old_end) }} → {{ interval(item.details.new_start, item.details.new_end) }}
            </div>
            <div v-else>{{ item.status === 'applied' ? '已列为当日备选' : '建议列为当日备选' }}</div>
            <p v-if="item.reason">{{ item.reason }}</p>
            <a-space v-if="item.status === 'pending'">
              <a-button type="primary" size="small" :disabled="busy || !canApply" @click="decide(item, 'applied')">
                {{ item.kind === 'shift_time' ? '应用时间修改' : '加入备选' }}
              </a-button>
              <a-button size="small" :disabled="busy" @click="decide(item, 'rejected')">拒绝</a-button>
            </a-space>
          </div>
        </a-list-item>
      </template>
    </a-list>
    <a-space>
      <a-button v-if="page > 1" size="small" :disabled="busy" @click="load(page - 1)">上一页</a-button>
      <a-button v-if="more" size="small" :disabled="busy" @click="load(page + 1)">下一页</a-button>
    </a-space>
  </section>
</template>

<script setup lang="ts">
import { Alert as AAlert, Button as AButton, List as AList, ListItem as AListItem,
  Space as ASpace, Tag as ATag } from 'ant-design-vue'
import { computed, onMounted, ref } from 'vue'
import { decideTripSuggestion, listTripSuggestions, type TripSuggestion } from '@/services/api'

const props = defineProps<{ tripId: number; canApply: boolean }>()
const emit = defineEmits<{ scheduleChanged: [] }>()
const items = ref<TripSuggestion[]>([])
const page = ref(1)
const more = ref(false)
const loading = ref(false)
const saving = ref(false)
const busy = computed(() => loading.value || saving.value)
const error = ref('')
const states = { pending: '待处理', applied: '已应用', rejected: '已拒绝' }

const interval = (start?: string, end?: string) => (start?.slice(11,16) || '—') + '–' + (end?.slice(11,16) || '—')

async function load(pageNumber = 1) {
  loading.value = true
  error.value = ''
  try {
    const result = await listTripSuggestions(props.tripId, pageNumber)
    items.value = result.data
    page.value = pageNumber
    more.value = result.has_more
  } catch {
    error.value = '无法读取同行建议，请重新打开分享窗口。'
  } finally {
    loading.value = false
  }
}

async function decide(item: TripSuggestion, decision: 'applied' | 'rejected') {
  if (busy.value) return
  saving.value = true
  error.value = ''
  try {
    const result = await decideTripSuggestion(props.tripId, item.id, decision)
    if (result.version) emit('scheduleChanged')
    await load()
  } catch (failure: any) {
    error.value = failure.response?.data?.message || '暂时无法确认操作结果，请刷新后重试。'
  } finally {
    saving.value = false
  }
}

onMounted(() => load())
</script>

<style scoped>
.suggestions { margin-top: 20px; border-top: 1px solid #d9d9d9; padding-top: 16px; }
.suggestion { display: grid; gap: 6px; overflow-wrap: anywhere; }
.suggestion p { margin: 0; }
</style>
