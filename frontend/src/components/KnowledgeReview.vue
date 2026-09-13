<template>
  <a-modal :open="true" title="对照原件复核提取结果" width="90%" :footer="null" @cancel="emit('close')">
    <a-alert v-if="error" :message="error" type="error" />
    <a-alert v-if="selected?.legacy_review" message="旧版已发布资料，尚无新流程复核记录。" type="warning" />
    <div class="review-grid" v-if="selected">
      <iframe v-if="originalUrl" :src="originalUrl" title="原文件" />
      <section>
        <div v-for="(_, i) in pages" :key="i"><b>来源第 {{ i + 1 }} 页</b><a-textarea v-model:value="pages[i]" :rows="10" :readonly="selected.status !== 'awaiting_review'" /></div>
        <p>核对景点归属、表格条件、页码和否定语句。多个景点用三级标题分别标明名称；跨页表格需补齐条件，无法确认的片段请删除或拒绝整份资料。</p>
        <a-space v-if="selected.status === 'awaiting_review'">
          <a-button :loading="busy" @click="save">保存修订</a-button>
          <a-button type="primary" :loading="busy" :disabled="dirty || !originalUrl" @click="publish">已对照原件，确认发布当前版本</a-button>
        </a-space>
        <p v-if="dirty">有未保存修改，请先保存，再确认发布。</p>
      </section>
    </div>
  </a-modal>
</template>
<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import api from '@/services/api'
const props = defineProps<{ id: number }>(), emit = defineEmits(['close'])
const selected = ref<any>(null), pages = ref<string[]>([]), originalUrl = ref(''), error = ref(''), busy = ref(false), saved = ref('')
const dirty = computed(() => JSON.stringify(pages.value) !== saved.value)
async function run(action: () => Promise<void>) {
  busy.value = true; error.value = ''
  try { await action() } catch (e: any) { error.value = e.response?.data?.detail || e.response?.data?.message || '操作失败，请重新打开复核' }
  finally { busy.value = false }
}
function clear() { if (originalUrl.value) URL.revokeObjectURL(originalUrl.value); originalUrl.value = '' }
async function load() {
  clear()
  await run(async () => {
    selected.value = (await api.get(`/api/knowledge/admin/submissions/${props.id}/preview`)).data.data
    pages.value = [...selected.value.pages]; saved.value = JSON.stringify(pages.value)
    const response = await api.get(`/api/knowledge/admin/submissions/${props.id}/original`, { responseType: 'blob' })
    originalUrl.value = URL.createObjectURL(response.data)
  })
}
async function save() {
  await run(async () => {
    const response = await api.put(`/api/knowledge/admin/submissions/${props.id}/extraction`, { version: selected.value.version, pages: pages.value })
    selected.value = { ...selected.value, ...response.data.data }; saved.value = JSON.stringify(pages.value)
  })
}
async function publish() {
  await run(async () => {
    await api.post(`/api/knowledge/admin/submissions/${props.id}/publish`, { version: selected.value.version })
    emit('close')
  })
}
onMounted(load); watch(() => props.id, load); onUnmounted(clear)
</script>
<style scoped>
.review-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }.review-grid iframe { width: 100%; height: 65vh; border: 1px solid #ddd; }
@media(max-width: 700px) { .review-grid { grid-template-columns: 1fr; } }
</style>
