<template>
  <a-modal :open="open" title="搜索并添加景点" :footer="null" @cancel="emit('close')">
    <p>搜索 {{ city }} 的真实地点；门票未知，保存后由后端重新计算费用预留。</p>
    <a-input-search v-model:value="keyword" placeholder="输入景点名称" enter-button="搜索" :loading="loading" @search="search" />
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-empty v-if="searched && !loading && !error && !results.length" description="没有找到可用景点，请换个名称" />
    <a-list :data-source="results" :loading="loading">
      <template #renderItem="{ item }">
        <a-list-item>
          <a-list-item-meta :title="item.name" :description="item.address" />
          <template #actions>
            <a-button :disabled="usedIds.includes(item.id)" @click="emit('select', item)">{{ usedIds.includes(item.id) ? '已在行程中' : '添加' }}</a-button>
          </template>
        </a-list-item>
      </template>
    </a-list>
  </a-modal>
</template>

<script setup lang="ts">
import { ref, watch } from 'vue'
import { searchAttractionPOIs } from '@/services/map'
import type { POIInfo } from '@/types'

const props = defineProps<{ open: boolean; city: string; usedIds: string[] }>()
const emit = defineEmits<{ close: []; select: [poi: POIInfo] }>()
const keyword = ref('')
const results = ref<POIInfo[]>([])
const loading = ref(false)
const searched = ref(false)
const error = ref('')
let generation = 0

watch(() => [props.open, props.city], () => {
  generation++
  keyword.value = ''
  results.value = []
  error.value = ''
  loading.value = false
  searched.value = false
})

async function search() {
  const query = keyword.value.trim()
  if (!query) return
  const current = ++generation
  loading.value = true
  error.value = ''
  results.value = []
  try {
    const pois = await searchAttractionPOIs(props.city, query)
    if (current !== generation) return
    results.value = pois
    searched.value = true
  } catch {
    if (current === generation) error.value = '景点搜索失败，请稍后重试'
  } finally {
    if (current === generation) loading.value = false
  }
}
</script>
