import { expect, test, type Page } from '@playwright/test'

const plan = {
  city: '北京', start_date: '2026-10-01', end_date: '2026-10-01', overall_suggestions: '核对开放与预约信息',
  constraints: { must_visit: [], avoid: [] }, weather_info: [], enrichment_notices: [],
  days: [{ date: '2026-10-01', day_index: 0, description: '新的单日安排', transportation: '步行',
    accommodation: '经济型酒店', attractions: [{ poi_id: 'B0001', name: '故宫博物院',
      location: { longitude: 116.4, latitude: 39.9 }, visit_duration: 180, photos: [] }], meals: [] }]
}

function seed(page: Page) {
  return page.addInitScript(({ plan }) => {
    sessionStorage.setItem('access_token', 'fixture-browser-token')
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
    sessionStorage.setItem('tripPlanId', '71')
    sessionStorage.setItem('tripPlanVersion', '1')
    sessionStorage.setItem('tripQuality', JSON.stringify({ policy_version: 'constraints-v2', outcome: 'draft',
      validated_outcome: 'degraded', assistant_confirmation_required: true,
      assistant_proposal_status: 'pending', assistant_conversation_id: 'conversation-1',
      revision_parent: { record_id: 41, version: 3 }, issues: [] }))
  }, { plan })
}

test('pending assistant revision is previewed and confirmed with its proposal version', async ({ page }) => {
  await seed(page)
  await page.route('**/api/poi/photo/image**', route => route.fulfill({ status: 404 }))
  let confirmed = false
  await page.route('**/api/assistant/conversations/conversation-1/proposals/71/confirm', route => {
    expect(route.request().headers()['if-match']).toBe('1')
    confirmed = true
    return route.fulfill({ json: { success: true, id: 41, version: 4, data: plan,
      quality: { policy_version: 'constraints-v2', outcome: 'degraded', issues: [] }, message: '已确认并保存助手方案' } })
  })
  await page.goto('/result')
  await expect(page.getByText(/待确认的助手改排方案；原行程 #41 尚未更新/)).toBeVisible()
  await expect(page.getByRole('button', { name: '编辑行程' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '保存修改' })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '🔗 分享' })).toHaveCount(0)
  await page.getByRole('button', { name: '确认应用到原行程' }).click()
  await expect.poll(() => confirmed).toBeTruthy()
  await expect(page.getByRole('button', { name: '确认应用到原行程' })).toHaveCount(0)
  await expect.poll(() => page.evaluate(() => sessionStorage.getItem('tripPlanId'))).toBe('41')
})

test('pending assistant revision can be discarded and returns to refreshed history', async ({ page }) => {
  await seed(page)
  await page.route('**/api/poi/photo/image**', route => route.fulfill({ status: 404 }))
  await page.route('**/api/history**', route => route.fulfill({ json: { success: true, data: [], total: 0 } }))
  let discarded = false
  await page.route('**/api/assistant/conversations/conversation-1/proposals/71/discard', route => {
    expect(route.request().headers()['if-match']).toBe('1')
    discarded = true
    return route.fulfill({ json: { success: true, id: 71, version: 2,
      quality: { assistant_proposal_status: 'discarded' }, message: '已放弃方案，原行程未改变' } })
  })
  await page.goto('/result')
  await page.getByRole('button', { name: '放弃方案' }).click()
  await expect.poll(() => discarded).toBeTruthy()
  await expect(page).toHaveURL(/\/history$/)
})
