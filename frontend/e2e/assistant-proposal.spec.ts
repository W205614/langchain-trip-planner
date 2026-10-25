import { expect, test, type Page } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('https://*.amap.com/**', route => route.abort())
})

const plan = {
  city: '北京', start_date: '2026-10-01', end_date: '2026-10-01', overall_suggestions: '核对开放与预约信息',
  constraints: { must_visit: [], avoid: [] }, weather_info: [], enrichment_notices: [],
  days: [{ date: '2026-10-01', day_index: 0, description: '新的单日安排', transportation: '步行',
    accommodation: '经济型酒店', attractions: [{ poi_id: 'B0001', name: '故宫博物院',
      location: { longitude: 116.4, latitude: 39.9 }, visit_duration: 180, photos: [] }], meals: [] }]
}

async function seed(page: Page) {
  await page.route('**/api/history/71', route => route.fulfill({ json: { success: true, data: { plan, version: 1, quality: { policy_version: 'constraints-v2', outcome: 'draft', validated_outcome: 'degraded', assistant_confirmation_required: true, assistant_proposal_status: 'pending', assistant_conversation_id: 'conversation-1', revision_parent: { record_id: 41, version: 3 }, issues: [] } } } }))
  await page.route('**/api/assistant/conversations/conversation-1/proposals/71', route => route.fulfill({ json: { success: true, data: { diff: { added: [], removed: [] }, dependencies: { version: 3 }, stale: false } } }))
  await page.route('**/api/trips/*/planning-input', route => route.fulfill({ status: 503, json: { message: '旧方案兼容测试不加载新规划输入' } }))
  await page.addInitScript(({ plan }) => {
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
  await page.route('**/api/history**', route => /\/api\/history\/71(?:\?|$)/.test(route.request().url()) ? route.fallback() : route.fulfill({ json: { success: true, data: [], total: 0 } }))
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

for (const [code, reason] of [
  ['FACTS_EXPIRED', '方案依赖的事实已过期，请返回原行程重新核验，再生成提案。'],
  ['PLANNING_INPUT_CONFLICT', '规划约束或预订事项已变化，请重新加载原行程并生成提案。'],
  ['VERSION_CONFLICT', '方案或原行程版本已变化，请重新加载后检查差异并生成提案。'],
  ['REVISION_SCOPE_VIOLATION', '方案修改了授权范围外的内容，请检查改排范围后重新生成提案。'],
  ['PROTECTED_ACTIVITY_CHANGED', '锁定、已完成或预约安排发生变化，请检查受保护安排后重新生成提案。'],
  ['COMMITMENT_TIME_VIOLATION', '方案未保留当前已确认的预约时间，请检查预约后重新生成提案。'],
] as const) {
  test(`confirmation explains ${code} and preserves the proposal for review`, async ({ page }) => {
    await seed(page)
    await page.route('**/api/poi/photo/image**', route => route.fulfill({ status: 404 }))
    await page.route('**/api/assistant/conversations/conversation-1/proposals/71/confirm', route => route.fulfill({ status: 409, json: { code, message: '确认条件已变化' } }))
    await page.goto('/result')
    await page.getByRole('button', { name: '确认应用到原行程' }).click()
    await expect(page.getByTestId('proposal-confirm-error')).toHaveText(reason)
    await expect(page).toHaveURL(/\/result$/)
    await expect(page.getByTestId('proposal-diff')).toBeVisible()
    await expect(page.getByRole('button', { name: '放弃方案' })).toBeVisible()
    await expect(page.getByText(code, { exact: true })).toHaveCount(0)
    expect(await page.evaluate(() => [sessionStorage.getItem('tripPlanId'), sessionStorage.getItem('tripPlanVersion')])).toEqual(['71', '1'])
  })
}

test('unknown confirmation conflict keeps a safe explanation and does not navigate away', async ({ page }) => {
  await seed(page)
  await page.route('**/api/poi/photo/image**', route => route.fulfill({ status: 404 }))
  await page.route('**/api/assistant/conversations/conversation-1/proposals/71/confirm', route => route.fulfill({ status: 409, json: { code: 'FUTURE_CONFLICT', message: 'FUTURE_CONFLICT' } }))
  await page.goto('/result')
  await page.getByRole('button', { name: '确认应用到原行程' }).click()
  await expect(page.getByTestId('proposal-confirm-error')).toContainText('请刷新提案依赖并检查原行程')
  await expect(page.getByText('FUTURE_CONFLICT', { exact: true })).toHaveCount(0)
  await expect(page).toHaveURL(/\/result$/)
})

test('stale preview translates every dependency reason and disables confirmation', async ({ page }) => {
  await seed(page)
  await page.route('**/api/poi/photo/image**', route => route.fulfill({ status: 404 }))
  await page.route('**/api/assistant/conversations/conversation-1/proposals/71', route => route.fulfill({ json: { success: true, data: { diff: {}, stale: true, invalidation_reason: 'FACTS_EXPIRED,PLANNING_INPUT_CONFLICT' } } }))
  await page.goto('/result')
  await expect(page.getByTestId('proposal-stale')).toContainText('事实已过期')
  await expect(page.getByTestId('proposal-stale')).toContainText('规划约束或预订事项已变化')
  await expect(page.getByText('FACTS_EXPIRED,PLANNING_INPUT_CONFLICT', { exact: true })).toHaveCount(0)
  await expect(page.getByRole('button', { name: '确认应用到原行程' })).toBeDisabled()
})
