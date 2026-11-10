<template>
  <main class="operations-page">
    <a-button type="link" @click="router.push('/')">← 返回首页</a-button>
    <h1>个人数据与用量</h1>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <section v-if="limits">
      <h2>今日用量</h2>
      <p>UTC 日期 {{ limits.usage_date }}：已创建 {{ limits.tasks_today }} / {{ limits.limits.daily_tasks }} 个规划任务，当前活动任务 {{ limits.active_tasks }} / {{ limits.limits.active_tasks }}。</p>
      <p v-for="item in limits.reserved_calls" :key="item.kind">{{ kinds[item.kind] }}：已预留 {{ item.reserved_calls }} 次，上限 {{ limits.limits[`daily_${item.kind}`] }} 次。</p>
      <p>{{ limits.note }}</p>
      <template v-if="limits.supplier_usage"><p>供应商已报告 {{ limits.supplier_usage.reported_calls }} 次调用：输入 {{ limits.supplier_usage.input_tokens }} Token，输出 {{ limits.supplier_usage.output_tokens }} Token；尚未报告 {{ limits.supplier_usage.unreported_calls }} 次。</p><p>{{ limits.supplier_usage.note }}</p></template>
    </section>
    <section>
      <h2>导出个人数据</h2>
      <p>包含本人账号资料、行程、对话、投稿及原文件，不包含密码、密钥和他人私有数据。导出文件完成后24小时内可下载。</p>
      <a-button :loading="busy" @click="requestExport">申请导出</a-button>
      <a-button :loading="busy" @click="refresh">刷新状态</a-button>
      <article v-for="job in jobs.filter(j => j.kind === 'export')" :key="job.id">
        <a-tag>{{ job.status }}</a-tag>{{ job.created_at }} <span v-if="job.error_code">{{ job.error_code }}</span>
        <a-button v-if="job.status === 'succeeded'" type="link" @click="download(job)">下载 ZIP</a-button>
      </article>
    </section>
    <section class="erasure">
      <h2>注销账号</h2>
      <p>{{ preview?.policy }}</p>
      <p v-if="preview">将撤回 {{ preview.owned_trips }} 个本人行程、{{ preview.submissions }} 份投稿和 {{ preview.public_cards }} 个公开卡片。</p>
      <a-form layout="vertical" :model="{ password, totp, confirmation }" @finish="erase">
        <a-form-item name="password" label="当前密码"><a-input-password v-model:value="password" autocomplete="current-password" /></a-form-item>
        <a-form-item name="totp" label="二次验证码（已启用时必填）"><a-input v-model:value="totp" :maxlength="6" inputmode="numeric" /></a-form-item>
        <a-form-item name="confirmation" label="输入 DELETE MY ACCOUNT 确认"><a-input v-model:value="confirmation" autocomplete="off" /></a-form-item>
        <a-button danger html-type="submit" :disabled="confirmation !== 'DELETE MY ACCOUNT'" :loading="busy">确认注销并撤销登录</a-button>
      </a-form>
    </section>
  </main>
</template>
<script setup lang="ts">
import { Alert as AAlert, Button as AButton, Form as AForm, FormItem as AFormItem, Input as AInput, InputPassword as AInputPassword, Tag as ATag, message } from 'ant-design-vue'

import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { clearAuth } from '@/services/auth'
import { publicOperations, type DataJob } from '@/services/publicOperations'
const router = useRouter(), error = ref(''), busy = ref(false), limits = ref<any>(), preview = ref<any>(), jobs = ref<DataJob[]>([])
const password = ref(''), totp = ref(''), confirmation = ref('')
const kinds: Record<string, string> = { text: '文本／视觉', embedding: '向量化', amap: '地图' }
const save = (blob: Blob, name: string) => { const url = URL.createObjectURL(blob), anchor = document.createElement('a'); anchor.href = url; anchor.download = name; anchor.click(); setTimeout(() => URL.revokeObjectURL(url), 1000) }
const action = async (work: () => Promise<void>) => { if (busy.value) return; busy.value = true; error.value = ''; try { await work() } catch (failure: any) { error.value = failure.response?.data?.detail || failure.message } finally { busy.value = false } }
async function load() { const [l, p, j] = await Promise.all([publicOperations.limits(), publicOperations.erasurePreview(), publicOperations.jobs()]); limits.value = l; preview.value = p; jobs.value = j.data }
const refresh = () => action(load)
const requestExport = () => action(async () => { await publicOperations.requestExport(); await load(); message.success('导出已排队') })
const download = (job: DataJob) => action(async () => { save(await publicOperations.downloadExport(job.id), `trip-account-${job.id}.zip`) })
const erase = () => action(async () => {
  const result = await publicOperations.erase({ password: password.value, totp_code: totp.value, confirmation: confirmation.value })
  save(new Blob([JSON.stringify({ id: result.id, receipt: result.receipt }, null, 2)], { type: 'application/json' }), 'trip-account-erasure-receipt.json')
  password.value = ''; totp.value = ''; clearAuth()
  await router.push({ path: '/account/erasure-status', state: { erasureId: result.id, erasureReceipt: result.receipt } })
})
onMounted(() => action(load))
</script>
<style scoped>
.operations-page{max-width:900px;margin:32px auto;padding:28px;background:#fff;border-radius:12px;color:#202b40}section{padding:16px 0;border-bottom:1px solid #ddd}article{padding:12px 0}.erasure .ant-form{max-width:500px}@media(max-width:600px){.operations-page{margin:12px;padding:18px}}
</style>
