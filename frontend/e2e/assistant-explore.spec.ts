import { expect, test } from '@playwright/test'

test('history record opens contextual Agent Q&A with a direct sourced answer', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  await page.route('**/api/history**', route => route.fulfill({ json: { success: true, data: [{
    id: 41, version: 2, city: '北京', start_date: '2026-10-01', end_date: '2026-10-03', travel_days: 3,
    attraction_count: 4, preferences: []
  }], total: 1 } }))
  await page.route('**/api/assistant/conversations', async route => {
    expect(route.request().postDataJSON()).toMatchObject({ active_trip_id: 41 })
    return route.fulfill({ json: { success: true, id: 'conversation-1' } })
  })
  await page.route('**/api/assistant/conversations/conversation-1/messages', async route => {
    expect(route.request().postDataJSON()).toMatchObject({ mode: 'auto', city: '北京' })
    return route.fulfill({ json: { success: true, status: 'completed', data: {
      answer: '故宫建议提前预约，并预留半天参观。[1]',
      sources: [{ index: 1, source: '北京旅行资料' }]
    } } })
  })

  await page.goto('/history')
  await page.getByRole('button', { name: '💬 问攻略' }).click()
  await page.getByPlaceholder(/这份行程里的故宫/).fill('故宫如何预约？')
  await page.getByRole('button', { name: '获取直接回答' }).click()
  await expect(page.getByText('故宫建议提前预约，并预留半天参观。[1]')).toBeVisible()
  await expect(page.getByText('[1] 北京旅行资料')).toBeVisible()
  await expect(page.getByText('matched')).toHaveCount(0)
})

test('history list keeps contextual Q&A but removes the duplicate revision entry', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  await page.route('**/api/history**', route => route.fulfill({ json: { success: true, data: [{
    id: 42, version: 3, city: '北京', start_date: '2026-10-01', end_date: '2026-10-03', travel_days: 3
  }], total: 1 } }))
  await page.route('**/api/assistant/conversations', route => route.fulfill({ json: { success: true, id: 'conversation-2' } }))

  await page.goto('/history')
  await expect(page.getByRole('button', { name: '✨ AI 修改' })).toHaveCount(0)
  await page.getByRole('button', { name: '💬 问攻略' }).click()
  await expect(page.getByText(/2026-10-01 至 2026-10-03 · 3 天/)).toBeVisible()
  await expect(page.getByRole('button', { name: '修改行程' })).toHaveCount(0)
  await expect(page.getByText('修改安排请先进入具体行程。')).toBeVisible()
})

test('legacy tasks route opens only actionable task states inside my trips', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  await page.route('**/api/history**', route => route.fulfill({ json: { success: true, data: [], total: 0 } }))
  await page.route('**/api/trip/tasks**', route => {
    expect(new URL(route.request().url()).searchParams.get('actionable')).toBe('true')
    return route.fulfill({ json: { data: [
      { id: 'running-1', city: '北京', status: 'running', message: '正在生成', error_code: null },
      { id: 'failed-1', city: '上海', status: 'failed', message: '生成中断', error_code: 'PROCESS_INTERRUPTED' }
    ], total: 2 } })
  })

  await page.goto('/tasks')
  await expect(page).toHaveURL(/\/history\?tab=tasks$/)
  await expect(page.getByText('我的行程', { exact: true })).toBeVisible()
  await expect(page.getByText('北京', { exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '取消任务' })).toBeVisible()
  await expect(page.getByRole('button', { name: '重新提交' })).toBeVisible()
  await expect(page.getByRole('button', { name: '查看结果' })).toHaveCount(0)
})

test('explore formats provider categories and renders a same-origin reference image', async ({ page }) => {
  await page.route('**/api/map/poi**', route => route.fulfill({ json: { success: true, data: [{
    id: 'B0001', name: '故宫博物院', type: '风景名胜;风景名胜;世界遗产|科教文化服务;博物馆;博物馆',
    address: '景山前街4号', opening_hours: '08:30-17:00', location: { longitude: 116.4, latitude: 39.9 }, photos: []
  }] } }))
  await page.route('**/api/poi/photo/image**', route => route.fulfill({ contentType: 'image/svg+xml', body: '<svg xmlns="http://www.w3.org/2000/svg" width="800" height="450" />' }))

  await page.goto('/explore')
  await expect(page.getByRole('heading', { name: '故宫博物院' })).toBeVisible()
  await expect(page.getByText('世界遗产')).toBeVisible()
  await expect(page.getByText('博物馆', { exact: true })).toBeVisible()
  await expect(page.getByText(/风景名胜;风景名胜/)).toHaveCount(0)
  await expect(page.getByAltText('故宫博物院参考图片')).toHaveAttribute('src', /poi_id=B0001/)
})

test('draft history can reverify routes without asking for an id', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  let verified = false
  await page.route('**/api/history**', route => route.fulfill({ json: { success: true, data: [{
    id: 52, version: 4, city: '北京', start_date: '2026-10-01', end_date: '2026-10-01', travel_days: 1,
    attraction_count: 3, preferences: [], outcome: 'draft'
  }], total: 1 } }))
  await page.route('**/api/trips/52/verify', async route => {
    expect(route.request().headers()['if-match']).toBe('4')
    verified = true
    return route.fulfill({ json: { success: true, version: 5, quality: { outcome: 'degraded', issues: [] } } })
  })
  await page.goto('/history')
  await page.getByRole('button', { name: '🧭 重新核验路线' }).click()
  await expect.poll(() => verified).toBeTruthy()
  await expect(page.getByText('路线已重新核验，时间统计已更新')).toBeVisible()
})

test('result opened from history returns to history for another selection', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  const plan = {
    city: '北京', start_date: '2026-10-01', end_date: '2026-10-01', overall_suggestions: '行程建议',
    constraints: { must_visit: [], avoid: [], daily_minutes: 600, max_inter_stop_walking_km: null },
    weather_info: [], enrichment_notices: [], days: [{
      date: '2026-10-01', day_index: 0, description: '故宫一日游', transportation: '步行', accommodation: '经济型酒店',
      attractions: [{ poi_id: 'B0001', name: '故宫博物院', address: '景山前街4号', location: { longitude: 116.4, latitude: 39.9 }, visit_duration: 180, description: '提前预约', photos: [] }],
      meals: [], generation_mode: 'llm'
    }]
  }
  await page.route('**/api/history**', route => {
    if (/\/api\/history\/61(?:\?|$)/.test(route.request().url())) {
      return route.fulfill({ json: { success: true, data: { id: 61, version: 1, plan, quality: { policy_version: 'constraints-v2', outcome: 'complete', issues: [] } } } })
    }
    return route.fulfill({ json: { success: true, data: [{ id: 61, version: 1, city: '北京', start_date: '2026-10-01', end_date: '2026-10-01', travel_days: 1, attraction_count: 1 }], total: 1 } })
  })
  await page.route('**/api/poi/photo/image**', route => route.fulfill({ status: 404 }))

  await page.goto('/history')
  await page.getByRole('button', { name: '👁️ 查看行程' }).click()
  await expect(page).toHaveURL(/\/result\?from=history$/)
  await expect(page.getByRole('button', { name: '✨ AI 重新安排' })).toBeVisible()
  await page.getByRole('button', { name: '← 返回我的行程' }).click()
  await expect(page).toHaveURL(/\/history$/)
  await expect(page.getByText('行程 #61')).toBeVisible()
})
