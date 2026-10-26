import { test, expect } from '@playwright/test'
import { createHmac } from 'node:crypto'
import { installOfflineBrowserBoundary } from './offline-provider-boundary'
installOfflineBrowserBoundary()

function totp(secret: string, step: number) {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = '', value = ''
  for (const letter of secret) bits += alphabet.indexOf(letter).toString(2).padStart(5, '0')
  for (let index = 0; index + 8 <= bits.length; index += 8) value += String.fromCharCode(parseInt(bits.slice(index, index + 8), 2))
  const counter = Buffer.alloc(8); counter.writeBigUInt64BE(BigInt(step))
  const digest = createHmac('sha1', Buffer.from(value, 'latin1')).update(counter).digest()
  const offset = digest[digest.length - 1] & 15
  return String((digest.readUInt32BE(offset) & 0x7fffffff) % 1000000).padStart(6, '0')
}

test('browser Cookie login, CSRF denial, password change and one-time recovery work without stored JWT', async ({ page, request, context }) => {
  const username = `security_${Date.now()}`, password = 'browser-security-123', changed = 'changed-browser-456'
  const registration = await request.post('/api/auth/register', { data: { username, password } })
  expect(registration.ok()).toBeTruthy()
  const bearer = (await registration.json()).access_token
  await page.goto('/login')
  await page.getByPlaceholder('用户名', { exact: true }).fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  expect(await page.evaluate(() => sessionStorage.getItem('access_token'))).toBeNull()
  const cookies = await context.cookies(), cookie = cookies.find(item => item.name === 'trip_session')
  expect(cookie?.httpOnly).toBe(true); expect(cookie?.sameSite).toBe('Strict')
  expect(await page.evaluate(() => document.cookie.includes('trip_session='))).toBe(false)
  expect((await page.request.post('/api/auth/logout', { data: {} })).status()).toBe(403)
  expect((await page.request.get('/api/auth/me')).status()).toBe(200)
  await page.getByRole('button', { name: '账号安全', exact: true }).click()
  await page.getByLabel('当前密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '生成新恢复码', exact: true }).click()
  const recovery = await page.getByRole('textbox', { name: '一次性恢复码', exact: true }).inputValue()
  expect(recovery.length).toBeGreaterThan(40)
  await page.getByLabel('新密码', { exact: true }).fill(changed)
  await page.getByLabel('确认新密码', { exact: true }).fill(changed)
  await page.getByRole('button', { name: '修改并重新登录', exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
  expect((await request.get('/api/auth/me', { headers: { Authorization: `Bearer ${bearer}` } })).status()).toBe(401)
  await page.getByRole('button', { name: '使用离线恢复码重设密码', exact: true }).click()
  // Both forms share the typed username; the recovery form makes its purpose explicit.
  await page.getByPlaceholder('用户名', { exact: true }).fill(username)
  await page.getByLabel('离线恢复码', { exact: true }).fill(recovery)
  await page.getByLabel('恢复时的新密码', { exact: true }).fill('recovered-browser-789')
  const recovered = page.waitForResponse(response => response.url().endsWith('/api/auth/recover') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '重设密码', exact: true }).click()
  expect((await recovered).status()).toBe(200)
  expect((await request.post('/api/auth/recover', { data: { username, recovery_code: recovery, new_password: 'cannot-reuse-123' } })).status()).toBe(401)
  await page.getByPlaceholder('密码', { exact: true }).fill('recovered-browser-789')
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
})

test('TOTP enrollment requires confirmation and login enforces the next non-replayed code', async ({ page, request }) => {
  const username = `mfa_${Date.now()}`, password = 'browser-mfa-123'
  expect((await request.post('/api/auth/register', { data: { username, password } })).ok()).toBeTruthy()
  await page.goto('/login')
  await page.getByPlaceholder('用户名', { exact: true }).fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.getByRole('button', { name: '账号安全', exact: true }).click()
  const status = await (await page.request.get('/api/auth/security')).json()
  expect(status.mfa_available, 'Candidate must configure its isolated account encryption key to exercise MFA UI').toBe(true)
  await page.getByLabel('当前密码', { exact: true }).fill(password)
  await page.getByRole('button', { name: '设置验证器', exact: true }).click()
  const secret = await page.getByRole('textbox', { name: '验证器设置密钥', exact: true }).inputValue()
  const step = Math.floor(Date.now() / 30000)
  await page.getByRole('textbox', { name: '新验证器显示的6位数字', exact: true }).fill(totp(secret, step))
  await page.getByRole('button', { name: '确认启用并重新登录', exact: true }).click()
  await expect(page).toHaveURL(/\/login$/)
  const failed = await request.post('/api/auth/login', { data: { username, password } })
  expect(failed.status()).toBe(401)
  await page.getByPlaceholder('用户名', { exact: true }).fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill(password)
  await page.getByPlaceholder('验证器中的6位数字', { exact: true }).fill(totp(secret, step + 1))
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  expect((await request.post('/api/auth/login', { data: { username, password, totp_code: totp(secret, step + 1) } })).status()).toBe(401)
})
