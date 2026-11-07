import { test, expect, type APIRequestContext } from '@playwright/test'
import { guardOfflineResources } from './offline-provider-boundary'

const origin = process.env.PUBLIC_E2E_BASE_URL
const inbox = process.env.TEST_MAILPIT_URL
test.use({ baseURL: origin || 'http://127.0.0.1:18881' })
test.skip(!origin || !inbox, 'Requires the dedicated public-mode stack and isolated Mailpit')
test.setTimeout(120_000)
test.beforeEach(async ({ context, baseURL }) => { await guardOfflineResources(context, baseURL!) })

function identity() {
  const username = `public_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`
  return { username, email: `${username}@example.test`, password: 'public-browser-password-123' }
}

async function mailToken(request: APIRequestContext, email: string, mode: 'verify' | 'reset') {
  let token = ''
  await expect.poll(async () => {
    const search = await request.get(`${inbox}/api/v1/search`, { params: { query: `to:${email}` } })
    expect(search.ok()).toBeTruthy()
    for (const item of (await search.json()).messages || []) {
      const response = await request.get(`${inbox}/api/v1/message/${item.ID}`)
      const text = (await response.json()).Text as string
      if (!text.includes(`mode=${mode}`)) continue
      const match = text.match(/token=([A-Za-z0-9_-]{43})/)
      if (match) { token = match[1]; return true }
    }
    return false
  }, { timeout: 30_000, message: 'A real SMTP message must reach isolated Mailpit' }).toBe(true)
  return token
}

test('public registration, verification, notifications, feedback, export and erasure use real services', async ({ page, request, context }) => {
  const user = identity()
  const options = await request.get('/api/auth/options')
  expect(await options.json()).toMatchObject({ registration_mode: 'email_verified', email_available: true })
  await page.goto('/login')
  await page.getByRole('button', { name: '去注册', exact: true }).click()
  await page.getByPlaceholder('用户名', { exact: true }).fill(user.username)
  await page.getByPlaceholder('密码', { exact: true }).fill(user.password)
  await page.getByPlaceholder('确认密码', { exact: true }).fill(user.password)
  await page.locator('input[type="email"]').fill(user.email)
  const registered = page.waitForResponse(response => response.url().endsWith('/api/auth/register') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '注册并登录', exact: true }).click()
  expect((await registered).status()).toBe(200)
  await expect(page).toHaveURL(/\/account\/security$/)
  await expect(page.getByText('待验证邮箱：', { exact: false })).toBeVisible()
  const csrf = (await context.cookies()).find(cookie => cookie.name === 'trip_csrf')!.value
  const blocked = await page.request.post('/api/trip/tasks', { headers: { 'X-CSRF-Token': csrf }, data: {} })
  expect(blocked.status()).toBe(403)
  expect(await blocked.json()).toMatchObject({ code: 'EMAIL_VERIFICATION_REQUIRED' })

  const token = await mailToken(request, user.email, 'verify')
  await page.goto(`/account/email#mode=verify&token=${token}`)
  await expect(page).toHaveURL(/\/account\/email$/)
  await page.getByRole('button', { name: '确认验证邮箱', exact: true }).click()
  await expect(page.getByText('邮箱已验证', { exact: true })).toBeVisible()
  expect(await (await page.request.get('/api/auth/me')).json()).toMatchObject({ email: user.email, email_verified: true })

  expect((await page.request.post('/api/auth/logout', { headers: { 'X-CSRF-Token': csrf }, data: {} })).status()).toBe(200)
  await page.goto('/login')
  await page.getByPlaceholder('用户名', { exact: true }).fill(user.username)
  await page.getByPlaceholder('密码', { exact: true }).fill(user.password)
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  await page.goto('/account/security')
  const rebound = `${user.username}-bound@example.test`
  await page.getByLabel('当前密码', { exact: true }).fill(user.password)
  await page.getByLabel('绑定或更换邮箱', { exact: true }).fill(rebound)
  const binding = page.waitForResponse(response => response.url().endsWith('/api/account/email') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '发送验证邮件', exact: true }).click()
  const limited = await binding
  expect(limited.status()).toBe(429)
  expect(await limited.json()).toMatchObject({ code: 'EMAIL_RATE_LIMITED' })
  await expect(page.getByText('邮件请求过于频繁，请稍后重试', { exact: true })).toBeVisible()
  expect(await (await page.request.get('/api/auth/me')).json()).toMatchObject({ email: user.email, email_verified: true })
  const subscription = page.getByRole('checkbox', { name: '将新的行程站内通知同时通过邮件提醒', exact: true })
  const subscribed = page.waitForResponse(response => response.url().endsWith('/api/account/notification-preferences') && response.request().method() === 'PUT')
  await subscription.click()
  expect((await subscribed).status()).toBe(200)
  await expect(subscription).toBeChecked()
  await page.reload()
  await expect(subscription).toBeChecked()
  const unsubscribed = page.waitForResponse(response => response.url().endsWith('/api/account/notification-preferences') && response.request().method() === 'PUT')
  await subscription.click()
  expect((await unsubscribed).status()).toBe(200)
  await expect(subscription).not.toBeChecked()

  await page.goto('/feedback')
  const subject = `浏览器反馈 ${user.username}`
  await page.getByLabel('反馈主题', { exact: true }).fill(subject)
  await page.getByLabel('反馈内容', { exact: true }).fill('隔离验收：验证邮箱与个人数据导出流程。')
  const feedback = page.waitForResponse(response => response.url().endsWith('/api/feedback') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '提交反馈', exact: true }).click()
  expect((await feedback).ok()).toBeTruthy()
  await expect(page.locator('article').filter({ hasText: subject })).toContainText('待处理')

  await page.goto('/account/data')
  const queued = page.waitForResponse(response => response.url().endsWith('/api/account/exports') && response.request().method() === 'POST')
  await page.getByRole('button', { name: '申请导出', exact: true }).click()
  const response = await queued
  expect(response.status()).toBe(202)
  const job = (await response.json()).id as string
  await expect.poll(async () => {
    const result = await page.request.get('/api/account/data-jobs')
    return (await result.json()).data.find((item: { id: string }) => item.id === job)?.status
  }, { timeout: 45_000, message: 'The actual account-data worker must complete the export' }).toBe('succeeded')
  await page.getByRole('button', { name: '刷新状态', exact: true }).click()
  const downloaded = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 ZIP', exact: true }).click()
  const file = await downloaded
  expect(file.suggestedFilename()).toBe(`trip-account-${job}.zip`)
  const stream = await file.createReadStream()
  const chunks: Buffer[] = []
  for await (const chunk of stream!) chunks.push(Buffer.from(chunk))
  const zip = Buffer.concat(chunks)
  expect(zip.subarray(0, 2).toString()).toBe('PK')
  expect(zip.length).toBeGreaterThan(100)

  const priorCredential = (await context.cookies()).find(cookie => cookie.name === 'trip_session')!.value
  await page.getByLabel('当前密码', { exact: true }).fill(user.password)
  await page.getByLabel('输入 DELETE MY ACCOUNT 确认', { exact: true }).fill('DELETE MY ACCOUNT')
  const erasing = page.waitForResponse(response => response.url().endsWith('/api/account/erasure') && response.request().method() === 'POST')
  const receiptDownload = page.waitForEvent('download')
  await page.getByRole('button', { name: '确认注销并撤销登录', exact: true }).click()
  expect((await erasing).status()).toBe(202)
  const receiptFile = await receiptDownload
  expect(receiptFile.suggestedFilename()).toBe('trip-account-erasure-receipt.json')
  const receiptStream = await receiptFile.createReadStream(), receiptChunks: Buffer[] = []
  for await (const chunk of receiptStream!) receiptChunks.push(Buffer.from(chunk))
  const proof = JSON.parse(Buffer.concat(receiptChunks).toString('utf-8')) as { id: string; receipt: string }
  expect(proof.id).toMatch(/^[a-f0-9-]{36}$/)
  expect(proof.receipt).toMatch(/^[A-Za-z0-9_-]{43}$/)
  await expect(page).toHaveURL(/\/account\/erasure-status$/)
  await expect(page.getByRole('heading', { name: '注销清理进度', exact: true })).toBeVisible()
  expect((await request.get('/api/auth/me', { headers: { Authorization: `Bearer ${priorCredential}` } })).status()).toBe(401)
  await expect.poll(async () => {
    const status = await request.get(`/api/auth/erasure/${proof.id}`, { headers: { 'X-Erasure-Receipt': proof.receipt } })
    expect(status.status()).toBe(200)
    return (await status.json()).status
  }, { timeout: 30_000, message: 'The real account-data worker must finish erasing this fresh test user' }).toBe('succeeded')
  await page.reload()
  await page.getByLabel('注销作业编号', { exact: true }).fill(proof.id)
  await page.getByLabel('注销凭据', { exact: true }).fill(proof.receipt)
  await page.getByRole('button', { name: '查询进度', exact: true }).click()
  await expect(page.getByText('在线数据与索引已完成清理', { exact: true })).toBeVisible()
})

test('email password recovery UI consumes a real delivered reset link', async ({ page, request }) => {
  const user = identity()
  const registration = await request.post('/api/auth/register', { data: user })
  expect(registration.status()).toBe(200)
  const prior = (await registration.json()).access_token as string
  const verify = await mailToken(request, user.email, 'verify')
  expect((await request.post('/api/auth/email/confirm', { data: { token: verify } })).status()).toBe(200)
  await page.goto('/account/email')
  await page.getByLabel('已验证邮箱', { exact: true }).fill(user.email)
  await page.getByRole('button', { name: '发送重置邮件', exact: true }).click()
  await expect(page.getByText('如果该邮箱已验证且账号可用，重置邮件将发送至该邮箱', { exact: true })).toBeVisible()
  const reset = await mailToken(request, user.email, 'reset')
  await page.goto(`/account/email#mode=reset&token=${reset}`)
  await page.getByLabel('邮件找回新密码', { exact: true }).fill('public-browser-recovered-456')
  await page.getByLabel('确认邮件找回新密码', { exact: true }).fill('public-browser-recovered-456')
  await page.getByRole('button', { name: '重设密码', exact: true }).click()
  await expect(page.getByText('密码已重设，请重新登录；二次验证设置保持有效', { exact: true })).toBeVisible()
  expect((await request.get('/api/auth/me', { headers: { Authorization: `Bearer ${prior}` } })).status()).toBe(401)
  expect((await request.post('/api/auth/email/reset', { data: { token: reset, new_password: 'reuse-denied-password-789' } })).status()).toBe(409)
  await page.goto('/login')
  await page.getByPlaceholder('用户名', { exact: true }).fill(user.username)
  await page.getByPlaceholder('密码', { exact: true }).fill('public-browser-recovered-456')
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
})
