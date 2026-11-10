<template>
  <main class="email-page">
    <a-button type="link" @click="router.push('/login')">← 返回登录</a-button>
    <h1>{{ mode === 'verify' ? '验证邮箱' : '通过邮箱重设密码' }}</h1>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-alert v-if="success" type="success" :message="success" show-icon />
    <template v-if="mode === 'verify'">
      <p>确认将邮件中的地址绑定到发起验证的账号。链接仅能使用一次，30分钟内有效。</p>
      <a-button type="primary" :disabled="!token || !!success" :loading="busy" @click="confirm">确认验证邮箱</a-button>
    </template>
    <a-form v-else-if="token" :model="{ password, confirmation, code }" layout="vertical" @finish="reset">
      <p>设置新密码后，所有旧登录立即失效。已开启验证器时仍需要二次验证码。</p>
      <a-form-item label="新密码（8至64字符）" name="password" :rules="[{ required: true, min: 8, max: 64, message: '密码须8至64字符' }]">
        <a-input-password v-model:value="password" aria-label="邮件找回新密码" autocomplete="new-password" :maxlength="64" />
      </a-form-item>
      <a-form-item label="确认新密码" name="confirmation" :rules="[{ required: true, message: '请确认新密码' }]"><a-input-password v-model:value="confirmation" aria-label="确认邮件找回新密码" autocomplete="new-password" :maxlength="64" /></a-form-item>
      <a-form-item label="二次验证码（已开启验证器时填写）"><a-input v-model:value="code" aria-label="邮件找回二次验证码" :maxlength="6" inputmode="numeric" autocomplete="one-time-code" /></a-form-item>
      <a-button type="primary" html-type="submit" :disabled="!!success" :loading="busy">重设密码</a-button>
    </a-form>
    <a-form v-else :model="{ email }" layout="vertical" @finish="requestReset">
      <p>输入账号已经验证的邮箱。若账号符合条件，你会收到15分钟内有效的重置链接。</p>
      <a-form-item label="已验证邮箱" name="email" :rules="[{ required: true, type: 'email', message: '请填写有效邮箱' }]"><a-input v-model:value="email" aria-label="已验证邮箱" type="email" autocomplete="email" /></a-form-item>
      <a-button type="primary" html-type="submit" :loading="busy">发送重置邮件</a-button>
    </a-form>
    <p><a-button type="link" @click="router.push('/account/security')">前往账号安全</a-button></p>
  </main>
</template>

<script setup lang="ts">
import { Alert as AAlert, Button as AButton, Form as AForm, FormItem as AFormItem, Input as AInput, InputPassword as AInputPassword } from 'ant-design-vue'

import { ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { accountEmail } from '@/services/api'
import { clearAuth } from '@/services/auth'

const router = useRouter(), route = useRoute()
const mode = ref<'verify' | 'reset'>('reset'), token = ref('')
const email = ref(''), password = ref(''), confirmation = ref(''), code = ref('')
const busy = ref(false), error = ref(''), success = ref('')
watch(() => route.hash, hash => {
  const fragment = new URLSearchParams(hash.replace(/^#/, ''))
  mode.value = fragment.get('mode') === 'verify' ? 'verify' : 'reset'
  token.value = fragment.get('token') || ''
  error.value = ''; success.value = ''; password.value = ''; confirmation.value = ''; code.value = ''
  if (hash) window.history.replaceState(window.history.state, '', window.location.pathname)
}, { immediate: true })
const action = async (work: () => Promise<void>) => {
  if (busy.value) return
  busy.value = true; error.value = ''; success.value = ''
  try { await work() }
  catch (failure: any) { error.value = failure.response?.data?.detail || failure.message || '操作失败' }
  finally { busy.value = false; code.value = '' }
}
const confirm = () => action(async () => { const result = await accountEmail.confirm(token.value); token.value = ''; success.value = result.message })
const requestReset = () => action(async () => { const result = await accountEmail.requestReset(email.value); success.value = result.message })
const reset = () => action(async () => {
  if (password.value !== confirmation.value) throw new Error('两次密码不一致')
  const result = await accountEmail.reset({ token: token.value, new_password: password.value, totp_code: code.value })
  token.value = ''; password.value = ''; confirmation.value = ''; clearAuth(); success.value = result.message
})
</script>

<style scoped>
.email-page { max-width: 560px; margin: 48px auto; padding: 28px; background: #fff; color: #202b40; border-radius: 12px; }
.email-page h1 { font-size: 24px; }
.email-page p { line-height: 1.7; }
.email-page .ant-alert { margin-bottom: 16px; }
@media (max-width: 640px) { .email-page { margin: 20px 12px; padding: 20px; } }
</style>
