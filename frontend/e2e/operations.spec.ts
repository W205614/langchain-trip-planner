import { expect, test } from '@playwright/test'

test('saved trip supports checks, expense ledger and accepted editor changes', async ({ page, request }) => {
  test.setTimeout(120_000)
  const suffix = `${Date.now()}_${Math.floor(Math.random() * 10000)}`
  const ownerName = `ops_owner_${suffix}`
  const editorName = `ops_editor_${suffix}`
  const ownerResponse = await request.post('/api/auth/register', {
    data: { username: ownerName, password: 'browser123' }
  })
  const editorResponse = await request.post('/api/auth/register', {
    data: { username: editorName, password: 'browser123' }
  })
  expect(ownerResponse.status()).toBe(200)
  expect(editorResponse.status()).toBe(200)
  const owner = (await ownerResponse.json()).access_token as string
  const editor = (await editorResponse.json()).access_token as string
  const submitted = await request.post('/api/trip/tasks', {
    headers: { Authorization: `Bearer ${owner}`, 'Idempotency-Key': `operations-${suffix}` },
    data: {
      city: '北京', start_date: '2026-10-01', end_date: '2026-10-01', travel_days: 1,
      transportation: '步行', accommodation: '酒店', preferences: [], free_text_input: 'fixture:invalid-json'
    }
  })
  expect(submitted.status()).toBe(202)
  const taskId = (await submitted.json()).data.id
  let savedTripId = 0
  await expect.poll(async () => {
    const response = await request.get(`/api/trip/tasks/${taskId}`, { headers: { Authorization: `Bearer ${owner}` } })
    const task = (await response.json()).data
    savedTripId = Number(task.result?.id || 0)
    return task.status
  }).toBe('succeeded')
  expect(savedTripId).toBeGreaterThan(0)

  await page.goto('/')
  await page.evaluate(token => sessionStorage.setItem('access_token', token), owner)
  await page.goto(`/trips/${savedTripId}/operations`)
  await expect(page.getByRole('heading', { name: '北京 · 执行工作台' })).toBeVisible()
  await page.getByRole('button', { name: '核验当天信息' }).click()
  await expect(page.getByText('核验结果已保存')).toBeVisible()

  await page.getByRole('tab', { name: '预订与费用' }).click()
  await page.getByPlaceholder('门票或住宿事项').fill('自动化验收事项，未实际预订')
  await page.getByPlaceholder('金额（可留空）').fill('12.50')
  await page.getByRole('button', { name: '添加待确认事项' }).click()
  await expect(page.getByRole('heading', { name: /自动化验收事项/ })).toBeVisible()
  await page.getByPlaceholder('实际金额').fill('9.80')
  await page.getByPlaceholder('支出说明').fill('自动化验收，未实际付款')
  await page.getByRole('button', { name: '记一笔' }).click()
  await expect(page.getByText('自动化验收，未实际付款')).toBeVisible()

  await page.getByRole('tab', { name: '同行协作' }).click()
  await page.getByPlaceholder('已注册用户名').fill(editorName)
  const role = page.getByRole('tabpanel', { name: '同行协作' }).getByRole('combobox').first()
  await page.getByRole('tabpanel', { name: '同行协作' }).getByText('查看者', { exact: true }).click()
  await role.press('ArrowDown')
  await role.press('Enter')
  await page.getByRole('button', { name: /邀\s*请/ }).click()
  await expect(page.getByText('编辑者 · 待接受')).toBeVisible()

  await page.evaluate(token => sessionStorage.setItem('access_token', token), editor)
  await page.goto('/inbox')
  await expect(page.getByText(/编辑者 · 待接受/)).toBeVisible()
  await page.getByRole('button', { name: /接\s*受/ }).click()
  await expect(page.getByText(/编辑者 · 已接受/)).toBeVisible()
  await page.getByRole('button', { name: '打开行程' }).click()
  await expect(page.getByText('可编辑同行人')).toBeVisible()
  await expect(page.getByRole('button', { name: '核验当天信息' })).toHaveCount(0)
  await page.getByRole('tab', { name: '同行协作' }).click()
  await page.getByPlaceholder('当天主题或安排说明').fill('同行人调整的主题')
  await page.getByRole('button', { name: '保存顺序' }).click()
  await expect(page.getByText('版本 2', { exact: true })).toBeVisible()
  await expect(page.getByText('可编辑同行人', { exact: true })).toBeVisible()
  await expect(page.getByPlaceholder('当天主题或安排说明')).toHaveValue('同行人调整的主题')
})
