import { test, expect } from '@playwright/test'

test('sparse candidates fail without saving an empty day and remain retryable', async ({ page, request }) => {
  const username = `draft_${Date.now()}`
  const registration = await request.post('/api/auth/register', { data: { username, password: 'browser123' } })
  const token = (await registration.json()).access_token
  const response = await request.post('/api/trip/tasks', {
    headers: { Authorization: `Bearer ${token}`, 'Idempotency-Key': username },
    data: { city: '稀疏城市', start_date: '2026-09-14', end_date: '2026-09-15', travel_days: 2,
      transportation: '公共交通', accommodation: '经济型酒店', preferences: [], free_text_input: 'fixture:invalid-json' }
  })
  expect(response.status()).toBe(202)
  const id = (await response.json()).data.id
  let state: any
  await expect.poll(async () => {
    state = (await (await request.get(`/api/trip/tasks/${id}`, { headers: { Authorization: `Bearer ${token}` } })).json()).data
    return state.status
  }).toBe('failed')
  expect(state.error_code).toBe('TRUSTED_POI_UNAVAILABLE')
  await page.goto('/login')
  await page.evaluate(({ token, username }) => {
    sessionStorage.setItem('access_token', token)
    sessionStorage.setItem('user', JSON.stringify({ username }))
  }, { token, username })
  await page.goto('/history?tab=tasks')
  await expect(page.getByText('稀疏城市', { exact: true })).toBeVisible()
  await expect(page.getByText('失败', { exact: true })).toBeVisible()
  await expect(page.getByText(/TRUSTED_POI_UNAVAILABLE/)).toBeVisible()
  await expect(page.getByRole('button', { name: '重新提交' })).toBeVisible()
  await page.reload()
  await expect(page.getByText('稀疏城市', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: '行程记录' }).click()
  await expect(page.getByText('还没有行程记录，快去生成你的第一个旅行计划吧')).toBeVisible()
  expect(state.result).toBeUndefined()
})

test('research dependency failure clears stale evidence and is not shown as no-match', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  await page.route('**/api/auth/me', route => route.fulfill({ json: { username: 'fixture', id: 1 } }))
  let fail = false
  await page.route('**/api/research', route => route.fulfill(fail
    ? { status: 503, json: { message: '资料检索暂不可用，请稍后重试' } }
    : { json: { data: { evidence: [{ content: '旧证据内容', source: '资料' }] } } }))
  await page.goto('/research')
  await page.getByPlaceholder('例如：北京').fill('北京')
  await page.getByPlaceholder(/例如：带孩子/).fill('博物馆预约')
  await page.getByRole('button', { name: '检索资料' }).click()
  await expect(page.getByText('旧证据内容')).toBeVisible()
  fail = true
  await page.getByRole('button', { name: '检索资料' }).click()
  await expect(page.getByText('资料检索暂不可用，请稍后重试')).toBeVisible()
  await expect(page.getByText('旧证据内容')).toHaveCount(0)
  await expect(page.getByText(/没有找到匹配资料/)).toHaveCount(0)
})

test('knowledge review must save revisions before publishing the exact version', async ({ page }) => {
  const item = { id: 91, title: '复核样本', city: '北京', status: 'awaiting_review', version: 2,
    original_filename: 'test.png', source_tier: 'community', pages: ['### 故宫\n预约说明'] }
  let publishedVersion = 0
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  await page.route('**/api/knowledge/admin/submissions**', async route => {
    const url = new URL(route.request().url()).pathname
    if (url.endsWith('/original')) return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64') })
    if (url.endsWith('/preview')) return route.fulfill({ json: { data: item } })
    if (url.endsWith('/extraction')) {
      expect(route.request().postDataJSON().version).toBe(2)
      item.version = 3
      return route.fulfill({ json: { data: item } })
    }
    if (url.endsWith('/publish')) {
      publishedVersion = route.request().postDataJSON().version
      return route.fulfill({ json: { success: true } })
    }
    return route.fulfill({ json: { data: [item] } })
  })
  await page.goto('/knowledge/admin')
  await page.getByRole('button', { name: '查看与复核' }).click()
  await expect(page.getByTitle('原文件')).toBeVisible()
  await page.locator('textarea').fill('### 故宫\n无预约不得入园')
  const publish = page.getByRole('button', { name: '已对照原件，确认发布当前版本' })
  await expect(publish).toBeDisabled()
  await page.getByRole('button', { name: '保存修订' }).click()
  await expect(publish).toBeEnabled()
  await publish.click()
  await expect.poll(() => publishedVersion).toBe(3)
})
