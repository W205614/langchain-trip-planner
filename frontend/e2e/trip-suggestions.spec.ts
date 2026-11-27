import { expect, test } from '@playwright/test'
import { installOfflineBrowserBoundary } from './offline-provider-boundary'

installOfflineBrowserBoundary()

test('a friend proposes a backup place and the owner approves it', async ({ page, browser, request }) => {
  const fixture = await request.get('/api/validation/fixture')
  expect(fixture.ok()).toBe(true)
  expect((await fixture.json()).offline_fixture).toBe(true)

  async function register() {
    const response = await request.post('/api/auth/register', {
      data: { username: 'companion_' + crypto.randomUUID().slice(0, 12), password: 'travel-fixture-only' }
    })
    expect(response.ok()).toBe(true)
    return (await response.json()).access_token as string
  }

  const owner = await register()
  const friend = await register()
  const date = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai' }).format(Date.now())
  const created = await request.post('/api/trips', {
    headers: { Authorization: 'Bearer ' + owner },
    data: { city: '北京', title: '同行建议验收', start_date: date, end_date: date, travel_days: 1,
      transportation: '步行', accommodation: '经济型酒店', days: [{ poi_ids: [] }] }
  })
  expect(created.ok(), await created.text()).toBe(true)
  const trip = await created.json()

  await page.goto('/')
  await page.evaluate(({ token, id, plan }) => {
    sessionStorage.setItem('access_token', token)
    sessionStorage.setItem('tripPlanId', String(id))
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
  }, { token: owner, id: trip.id, plan: trip.data })
  await page.goto('/result')
  await page.getByRole('button', { name: '🔗 分享', exact: true }).click()
  await page.getByRole('checkbox', { name: '允许登录的朋友提交建议' }).check()
  await page.getByRole('button', { name: '创建七天分享链接' }).click()
  const link = page.getByRole('textbox', { name: '已创建的分享链接' })
  await expect(link).toBeVisible()
  const url = await link.inputValue()

  const friendPage = await browser.newPage()
  try {
    await friendPage.goto(url)
    await expect(friendPage.getByRole('link', { name: '登录后提交同行建议' })).toBeVisible()
    await friendPage.evaluate(token => sessionStorage.setItem('access_token', token), friend)
    await friendPage.reload()
    await friendPage.getByRole('button', { name: '建议修改这一天' }).click()
    await friendPage.getByLabel('备选地点', { exact: true }).fill('室内博物馆')
    await friendPage.getByRole('button', { name: '提交建议' }).click()
    await expect(friendPage.getByText('建议已提交，等待行程所有者处理')).toBeVisible()
  } finally {
    await friendPage.close()
  }

  await page.reload()
  await page.getByRole('button', { name: '🔗 分享', exact: true }).click()
  await expect(page.getByText('室内博物馆')).toBeVisible()
  await page.getByRole('button', { name: '加入备选' }).click()
  await expect(page.getByText('已列为当日备选')).toBeVisible()
})
