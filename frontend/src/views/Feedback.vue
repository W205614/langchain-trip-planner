<template>
  <main class="operations-page">
    <a-button type="link" @click="router.push('/')">← 返回首页</a-button>
    <h1>问题反馈</h1>
    <p>描述遇到的问题，可附上本人行程、任务或页面显示的请求编号。</p>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-form layout="vertical" :model="{ category, subject, description, trip, task, request }" @finish="submit">
      <a-form-item name="category" label="问题类别"><a-select v-model:value="category" :options="categories" /></a-form-item>
      <a-form-item name="subject" label="主题"><a-input v-model:value="subject" :maxlength="160" required aria-label="反馈主题" /></a-form-item>
      <a-form-item name="description" label="问题描述"><a-textarea v-model:value="description" :maxlength="3000" :rows="5" required aria-label="反馈内容" /></a-form-item>
      <a-form-item name="trip" label="行程编号（可选）"><a-input-number v-model:value="trip" :min="1" /></a-form-item>
      <a-form-item name="task" label="任务编号（可选）"><a-input v-model:value="task" :maxlength="36" /></a-form-item>
      <a-form-item name="request" label="请求编号（可选）"><a-input v-model:value="request" :maxlength="64" /></a-form-item>
      <a-button type="primary" html-type="submit" :loading="busy">提交反馈</a-button>
    </a-form>
    <h2>我的反馈</h2>
    <a-empty v-if="!items.length" description="暂无反馈" />
    <article v-for="item in items" :key="item.id">
      <a-tag>{{ statuses[item.status] || item.status }}</a-tag><strong>{{ item.subject }}</strong>
      <p class="prewrap">{{ item.message }}</p>
      <p v-if="item.reply" class="prewrap"><strong>处理回复：</strong>{{ item.reply }}</p>
      <small>{{ item.created_at }}</small>
    </article>
  </main>
</template>
<script setup lang="ts">
import { Alert as AAlert, Button as AButton, Empty as AEmpty, Form as AForm, FormItem as AFormItem, Input as AInput, InputNumber as AInputNumber, Select as ASelect, Tag as ATag, Textarea as ATextarea, message } from 'ant-design-vue'

import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { publicOperations, type FeedbackItem } from '@/services/publicOperations'
const router = useRouter(), busy = ref(false), error = ref('')
const category = ref('planning'), subject = ref(''), description = ref(''), trip = ref<number>(), task = ref(''), request = ref('')
const items = ref<FeedbackItem[]>([])
const categories = [{ value: 'planning', label: '行程规划' }, { value: 'account', label: '账号' }, { value: 'knowledge', label: '知识资料' }, { value: 'other', label: '其他' }]
const statuses: Record<string, string> = { open: '待处理', processing: '处理中', closed: '已关闭' }
async function load() { items.value = (await publicOperations.feedback()).data }
async function submit() {
  busy.value = true; error.value = ''
  try {
    await publicOperations.submitFeedback({ category: category.value, subject: subject.value, message: description.value,
      trip_id: trip.value, task_id: task.value || undefined, request_id: request.value || undefined })
    subject.value = ''; description.value = ''; await load(); message.success('反馈已提交')
  } catch (failure: any) { error.value = failure.response?.data?.detail || failure.message }
  finally { busy.value = false }
}
onMounted(() => load().catch(failure => { error.value = failure.response?.data?.detail || failure.message }))
</script>
<style scoped>
.operations-page{max-width:900px;margin:32px auto;padding:28px;background:#fff;border-radius:12px;color:#202b40}.operations-page article{padding:20px 0;border-bottom:1px solid #ddd}.prewrap{white-space:pre-wrap}.operations-page .ant-form{max-width:650px}@media(max-width:600px){.operations-page{margin:12px;padding:18px}}
</style>
