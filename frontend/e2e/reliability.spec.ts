import { test, expect } from '@playwright/test'

test('login and task recovery preserve the saved result and quality', async ({ page, request }) => {
  const username = `browser_${Date.now()}`
  const account = await request.post('/api/auth/register', { data: { username, password: 'browser123' } })
  expect(account.ok()).toBeTruthy()
  await page.goto('/login')
  await page.getByPlaceholder('用户名', { exact: true }).fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill('browser123')
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  await expect(page).toHaveURL(/\/$/)
  const token = await page.evaluate(() => sessionStorage.getItem('access_token'))
  const body = { city: '北京', start_date: '2026-09-11', end_date: '2026-09-11', travel_days: 1,
    transportation: '公共交通', accommodation: '经济型酒店', preferences: [], free_text_input: 'fixture:invalid-json' }
  const key = `browser-${Date.now()}`
  const response = await request.post('/api/trip/tasks', { data: body,
    headers: { Authorization: `Bearer ${token}`, 'Idempotency-Key': key } })
  expect(response.status()).toBe(202)
  const id = (await response.json()).data.id
  await page.evaluate(({ body, id, key }) => {
    sessionStorage.setItem('pendingTripTask', JSON.stringify({ body, id, key, fingerprint: JSON.stringify(body) }))
  }, { body, id, key })
  await page.reload()
  await expect(page).toHaveURL(/\/result$/)
  await expect(page.getByText(/使用规则兜底/)).toBeVisible()
  await expect(page.getByText(/预算估算（非实时报价/)).toBeVisible()
  await expect(page.getByText(/当前规则检查|部分旅行要求尚未满足/)).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  const saved = await page.evaluate(() => ({ id: sessionStorage.getItem('tripPlanId'), quality: sessionStorage.getItem('tripQuality') }))
  expect(Number(saved.id)).toBeGreaterThan(0)
  expect(JSON.parse(saved.quality || '{}').degraded_days).toEqual([0])
  await page.reload()
  await expect(page.getByText(/使用规则兜底/)).toBeVisible()
  await page.route('**/api/history/*', async route => {
    if (route.request().method() === 'PUT') {
      await route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ message: '版本冲突' }) })
    } else await route.continue()
  })
  await page.getByRole('button', { name: /编辑行程/ }).click()
  await page.getByRole('button', { name: /保存修改/ }).click()
  await expect(page.getByText('当前修改尚未保存到服务器，请重新保存或从历史记录加载。')).toBeVisible()
  await page.reload()
  await expect(page.getByText('当前修改尚未保存到服务器，请重新保存或从历史记录加载。')).toBeVisible()
})

test('task list survives reload and logout revokes the server session', async ({ page, request }) => {
  const username = `tasks_${Date.now()}`
  const registration = await request.post('/api/auth/register', { data: { username, password: 'browser123' } })
  const token = (await registration.json()).access_token
  await page.goto('/')
  await page.evaluate(({ token, username }) => {
    sessionStorage.setItem('access_token', token)
    sessionStorage.setItem('username', username)
  }, { token, username })
  const created = await request.post('/api/trip/tasks', { headers: { Authorization: `Bearer ${token}` }, data: {
    city: '北京', start_date: '2026-09-11', end_date: '2026-09-11', travel_days: 1,
    transportation: '步行', accommodation: '酒店', preferences: [], free_text_input: '',
    constraints: { must_visit: ['不可满足的测试景点'], daily_minutes: 400 } } })
  expect(created.status()).toBe(202)
  await page.goto('/tasks')
  await expect(page.getByText('我的规划任务', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '查看结果' })).toBeVisible({ timeout: 30000 })
  await page.reload()
  await page.getByRole('button', { name: '查看结果' }).click()
  await expect(page.getByText(/未满足必去景点：不可满足的测试景点/)).toBeVisible()
  await page.reload()
  await expect(page.getByText(/未满足必去景点：不可满足的测试景点/)).toBeVisible()
  await page.goto('/')
  await page.getByRole('button', { name: /退\s*出/, exact: true }).click()
  await expect(page.getByText('已退出登录', { exact: true })).toBeVisible()
  expect((await request.get('/api/auth/me', { headers: { Authorization: `Bearer ${token}` } })).status()).toBe(401)
})

test('a second account cannot subscribe to someone else’s task', async ({ request }) => {
  const first = await request.post('/api/auth/register', { data: { username: `owner_${Date.now()}`, password: 'browser123' } })
  const second = await request.post('/api/auth/register', { data: { username: `other_${Date.now()}`, password: 'browser123' } })
  const a = (await first.json()).access_token
  const b = (await second.json()).access_token
  const created = await request.post('/api/trip/tasks', { headers: { Authorization: `Bearer ${a}` }, data: {
    city: '北京', start_date: '2026-09-11', end_date: '2026-09-11', travel_days: 1,
    transportation: '步行', accommodation: '酒店', preferences: [], free_text_input: '' } })
  const id = (await created.json()).data.id
  expect((await request.get(`/api/trip/tasks/${id}`, { headers: { Authorization: `Bearer ${b}` } })).status()).toBe(404)
  expect((await request.get(`/api/trip/tasks/${id}/events`, { headers: { Authorization: `Bearer ${b}` } })).status()).toBe(404)
})
