<template>
  <div class="quota">
    <p v-if="status">已用 {{ mib(status.used_bytes) }} MB，待核对预留 {{ mib(status.reserved_bytes) }} MB / {{ mib(status.user_limit_bytes) }} MB；待审核最多 {{ status.pending_limit }} 份，每分钟最多 {{ status.uploads_per_minute }} 次。</p>
    <a-alert v-if="error" type="warning" :message="error" show-icon />
    <template v-if="admin && status">
      <p>待核对上传 {{ status.reservations?.length || 0 }} 条。核对只处理超过10分钟且未在写入的预留；完整文件恢复入库，缺失文件释放额度，异常文件保留待人工核查。</p>
      <a-button :loading="busy" @click="reconcile">核对上传存储</a-button>
      <a-button :loading="busy" @click="load">刷新额度</a-button>
      <p v-if="outcome">{{ outcome }}</p>
      <a-table v-if="status.reservations?.length" size="small" :data-source="status.reservations" :columns="columns" row-key="id" :pagination="false" :scroll="{ x: 600 }" />
    </template>
  </div>
</template>
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { accountSecurity } from '@/services/api'
const props = defineProps<{ admin?: boolean }>()
const status = ref<any>(null), error = ref(''), busy = ref(false), outcome = ref('')
const columns = [{ title: '预留编号', dataIndex: 'id' }, { title: '用户', dataIndex: 'user_id' }, { title: '字节', dataIndex: 'size_bytes' }, { title: '状态', dataIndex: 'status' }]
const mib = (value: number) => ((value || 0) / 1024 / 1024).toFixed(2)
const load = async () => { try { error.value = ''; status.value = await (props.admin ? accountSecurity.adminUploads() : accountSecurity.uploadStatus()) } catch (failure: any) { error.value = failure.response?.data?.detail || '上传额度读取失败' } }
const reconcile = async () => { busy.value = true; try { const result = await accountSecurity.reconcileUploads(); outcome.value = `恢复 ${result.recovered} 条，释放缺失文件预留 ${result.released_missing} 条，保留异常 ${result.uncertain_retained} 条`; await load() } catch (failure: any) { error.value = failure.response?.data?.detail || '核对失败，现有预留仍保留' } finally { busy.value = false } }
onMounted(load)
defineExpose({ load })
</script>
<style scoped>.quota { margin: 16px 0; color: #475569; line-height: 1.6; }</style>
