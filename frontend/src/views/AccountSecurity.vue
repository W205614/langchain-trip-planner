<template>
  <main class="security-page">
    <a-button type="link" @click="router.push('/')">← 返回首页</a-button>
    <h1>账号安全</h1>
    <a-alert v-if="error" type="error" :message="error" show-icon />
    <a-alert v-if="state.admin_mfa_required && admin && !state.mfa_enabled" type="warning" message="管理员必须先设置二次验证，才能访问审核与管理功能。" show-icon />
    <section>
      <h2>验证当前身份</h2>
      <p>修改密码、生成恢复码及设置验证器前，都需要核验当前密码。已启用验证器时填写当前6位数字，每个验证码只能使用一次。</p>
      <a-form layout="vertical">
        <a-form-item label="当前密码"><a-input-password v-model:value="password" aria-label="当前密码" autocomplete="current-password" /></a-form-item>
        <a-form-item v-if="state.mfa_enabled" label="当前二次验证码"><a-input v-model:value="code" :maxlength="6" inputmode="numeric" autocomplete="one-time-code" /></a-form-item>
      </a-form>
    </section>
    <section>
      <h2>修改密码</h2>
      <p>新密码8至64字符，最多72个UTF-8字节，不能使用用户名或常见弱密码。修改成功后所有旧登录立即失效。</p>
      <a-form layout="vertical" :model="{ nextPassword, confirmation }" @finish="changePassword">
        <a-form-item label="新密码" name="nextPassword"><a-input-password v-model:value="nextPassword" aria-label="新密码" autocomplete="new-password" /></a-form-item>
        <a-form-item label="确认新密码" name="confirmation"><a-input-password v-model:value="confirmation" aria-label="确认新密码" autocomplete="new-password" /></a-form-item>
        <a-button html-type="submit" :loading="busy">修改并重新登录</a-button>
      </a-form>
    </section>
    <section>
      <h2>一次性离线恢复码</h2>
      <p>{{ state.recovery_configured ? '已设置恢复码；重新生成会使旧码失效。' : '尚未设置。丢失密码前请生成并离线保存。' }} 使用恢复码会重设密码、撤销所有旧登录并关闭验证器，随后应重新设置验证器。</p>
      <a-button :loading="busy" @click="generateRecovery">生成新恢复码</a-button>
      <div v-if="recovery" class="secret">
        <strong>仅显示本次，请离线保存，勿上传或分享：</strong>
        <a-input :value="recovery" readonly aria-label="一次性恢复码" />
        <a-button @click="saveRecovery">下载恢复码</a-button>
        <a-button @click="recovery = ''">我已保存，隐藏</a-button>
      </div>
      <p>当前未接入邮件或短信恢复；未预留恢复码且已丢失密码时，需要联系部署管理员核验身份。</p>
    </section>
    <section>
      <h2>验证器二次验证</h2>
      <p>状态：{{ state.mfa_enabled ? '已启用' : '未启用' }}。支持标准TOTP验证器，可手动录入下面的密钥。设备时间应自动同步。</p>
      <a-alert v-if="!state.mfa_available" type="warning" message="部署尚未配置账号安全加密密钥；请联系部署管理员配置后再设置验证器。" show-icon />
      <a-space>
        <a-button :disabled="!state.mfa_available" :loading="busy" @click="enroll">{{ state.mfa_enabled ? '更换验证器' : '设置验证器' }}</a-button>
        <a-popconfirm v-if="state.mfa_enabled" title="关闭二次验证并撤销所有旧登录？" @confirm="disable">
          <a-button danger :disabled="admin && state.admin_mfa_required" :loading="busy">关闭验证器</a-button>
        </a-popconfirm>
      </a-space>
      <div v-if="enrollment" class="secret">
        <p>在10分钟内完成。手动录入：账号 {{ username }}，密钥如下，SHA1 / 6位 / 30秒。</p>
        <a-input :value="enrollment.secret" readonly aria-label="验证器设置密钥" />
        <a-form layout="vertical" :model="{ confirmationCode }" @finish="confirmMfa">
          <a-form-item label="新验证器显示的6位数字" name="confirmationCode"><a-input v-model:value="confirmationCode" aria-label="新验证器显示的6位数字" :maxlength="6" inputmode="numeric" /></a-form-item>
          <a-button html-type="submit" :loading="busy">确认启用并重新登录</a-button>
          <a-button @click="enrollment = null">取消显示</a-button>
        </a-form>
      </div>
      <p>启用后Cookie和Bearer登录都会校验二次验证码。启用完成后请等待验证器显示下一组数字，再重新登录。</p>
    </section>
  </main>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { accountSecurity } from '@/services/api'
import { clearAuth, getUsername, isAdmin } from '@/services/auth'
const router = useRouter()
const username = getUsername() || ''
const admin = isAdmin()
const state = ref<any>({})
const password = ref(''), code = ref(''), nextPassword = ref(''), confirmation = ref('')
const recovery = ref(''), confirmationCode = ref(''), error = ref(''), busy = ref(false)
const enrollment = ref<any>(null)
const credentials = () => ({ password: password.value, totp_code: code.value })
const load = async () => { state.value = await accountSecurity.status() }
const action = async (operation: () => Promise<void>) => {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { await operation() }
  catch (failure: any) { error.value = failure.response?.data?.detail || failure.message || '操作失败' }
  finally { busy.value = false; code.value = '' }
}
const reauthenticate = async (text: string) => { password.value = ''; nextPassword.value = ''; confirmation.value = ''; recovery.value = ''; enrollment.value = null; clearAuth(); message.success(text); await router.push('/login') }
const changePassword = () => action(async () => {
  if (nextPassword.value !== confirmation.value) throw new Error('两次新密码不一致')
  await accountSecurity.changePassword({ current_password: password.value, new_password: nextPassword.value, totp_code: code.value })
  await reauthenticate('密码已更新，请重新登录')
})
const generateRecovery = () => action(async () => { const result = await accountSecurity.recoveryCode(credentials()); recovery.value = result.recovery_code; await load() })
const enroll = () => action(async () => { enrollment.value = await accountSecurity.enroll(credentials()); confirmationCode.value = '' })
const confirmMfa = () => action(async () => { await accountSecurity.confirm(confirmationCode.value); confirmationCode.value = ''; await reauthenticate('验证器已启用，请等待下一组验证码后重新登录') })
const disable = () => action(async () => { await accountSecurity.disable(credentials()); await reauthenticate('验证器已关闭，请重新登录') })
const saveRecovery = () => {
  const blob = new Blob([`TripPlanner ${username}\n一次性离线恢复码：${recovery.value}\n请离线妥善保存，恢复后本码失效。\n`], { type: 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob), link = document.createElement('a')
  link.href = url; link.download = 'tripplanner-recovery-code.txt'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
onMounted(() => action(load))
</script>

<style scoped>
.security-page { max-width: 760px; margin: 36px auto; padding: 24px; border-radius: 16px; background: #fff; }
section { margin: 24px 0; padding-top: 16px; border-top: 1px solid #e2e8f0; }
p { line-height: 1.7; color: #475569; }
.secret { margin-top: 16px; padding: 16px; background: #f1f5f9; border-radius: 8px; }
.secret .ant-input { margin: 8px 0; font-family: monospace; }
</style>
