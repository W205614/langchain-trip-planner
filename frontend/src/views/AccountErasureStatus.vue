<template>
  <main class="operations-page">
    <h1>注销清理进度</h1>
    <p>登录已撤销。使用提交注销时下载的凭据查看进度，凭据有效期为30天。</p>
    <a-form layout="vertical" :model="{ id, receipt }" @finish="load">
      <a-form-item name="id" label="注销作业编号"><a-input v-model:value="id" autocomplete="off" aria-label="注销作业编号" /></a-form-item>
      <a-form-item name="receipt" label="注销凭据"><a-input-password v-model:value="receipt" autocomplete="off" aria-label="注销凭据" /></a-form-item>
      <a-button html-type="submit" type="primary" :loading="busy">查询进度</a-button>
    </a-form>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-alert v-if="job" :type="job.status === 'succeeded' ? 'success' : job.status === 'failed' ? 'error' : 'info'" :message="job.status === 'succeeded' ? '在线数据与索引已完成清理' : job.status === 'failed' ? '清理需要管理员处理，账号访问已撤销' : '清理进行中，尚未完成'" :description="`阶段：${job.stage}${job.error_code ? '；编号：' + job.error_code : ''}`" show-icon />
    <p>他人行程中的必要协作记录以匿名身份保留；历史备份按其保留策略到期。</p>
    <a-button type="link" @click="router.push('/')">返回首页</a-button>
  </main>
</template>
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { publicOperations, type DataJob } from '@/services/publicOperations'
const router = useRouter(), id = ref(''), receipt = ref(''), error = ref(''), busy = ref(false), job = ref<DataJob>()
async function load() {
  busy.value = true; error.value = ''; job.value = undefined
  try { job.value = await publicOperations.erasureStatus(id.value.trim(), receipt.value.trim()) }
  catch (failure: any) { error.value = failure.message }
  finally { busy.value = false }
}
onMounted(() => {
  id.value = String(window.history.state.erasureId || ''); receipt.value = String(window.history.state.erasureReceipt || '')
  if (id.value && receipt.value) { window.history.replaceState({ ...window.history.state, erasureId: undefined, erasureReceipt: undefined }, ''); void load() }
})
</script>
<style scoped>
.operations-page{max-width:720px;margin:32px auto;padding:28px;background:#fff;border-radius:12px;color:#202b40}.ant-alert{margin:20px 0}@media(max-width:600px){.operations-page{margin:12px;padding:18px}}
</style>
