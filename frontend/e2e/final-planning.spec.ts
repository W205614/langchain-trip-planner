import { expect, test } from '@playwright/test'

const planning = { timezone: 'Asia/Shanghai', day_start: '09:00', day_end: '20:00', day_windows: [], city_segments: [], commitments: [], hotel_stays: [], meal_windows: [], opening_windows: [], locks: [], completed_activity_ids: [], budget_mode: 'estimate' }
const plan = { city: '北京', start_date: '2026-11-01', end_date: '2026-11-01', weather_info: [], overall_suggestions: '核对预约', days: [{ day_index: 0, date: '2026-11-01', attractions: [{ activity_id: 'activity-1', poi_id: 'P1', name: '故宫', address: '北京', description: '游览', visit_duration: 120, location: { longitude: 116.4, latitude: 39.9 } }], meals: [], transportation: '公共交通', accommodation: '酒店', description: '按预约游览', timeline: [{ activity_id: 'activity-1', title: '故宫', kind: 'attraction', status: 'estimated', start_at: '2026-11-01T09:00:00+08:00', end_at: '2026-11-01T11:00:00+08:00', notes: ['营业信息待核实'] }], feasibility: { status: 'unverified', unknown: ['opening_unknown_or_stale:activity-1'] } }] }

test('owner saves structured daily windows and reviews a version conflict', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  await page.route('**/api/trips/42/workspace', route => route.fulfill({ json: { data: { id: 42, version: 3, role: 'owner', city: '北京', travel_days: 1, plan } } }))
  await page.route('**/api/trips/42/checks/latest*', route => route.fulfill({ json: { data: { status: 'never_checked', risks: [] } } }))
  await page.route('**/api/trips/42/commitments', route => route.fulfill({ json: { data: [] } }))
  await page.route('**/api/trips/42/expenses', route => route.fulfill({ json: { data: [], summary: {} } }))
  await page.route('**/api/trips/42/members', route => route.fulfill({ json: { data: [] } }))
  let saved = 0
  await page.route('**/api/trips/42/planning-input', route => {
    if (route.request().method() === 'PUT') {
      const payload = route.request().postDataJSON()
      expect(payload.planning_revision).toBe(saved ? 5 : 4)
      expect(payload.planning_input.day_start).toBe('08:30')
      expect(route.request().headers()['if-match']).toBe('3')
      if (saved++) return route.fulfill({ status: 409, json: { message: 'INPUT_CHANGED' } })
      return route.fulfill({ json: { success: true, data: { planning_input: payload.planning_input, planning_revision: 5, trip_version: 3, plan } } })
    }
    return route.fulfill({ json: { data: { planning_input: planning, planning_revision: 4, trip_version: 3, plan } } })
  })
  await page.goto('/trips/42/operations')
  await page.getByRole('tab', { name: '时间与约束' }).click()
  await page.getByLabel('每日最早出发', { exact: true }).fill('08:30')
  await page.getByTestId('planning-save').click()
  await expect(page.getByText('规划输入已保存；当前行程未自动改排')).toBeVisible()
  await page.getByTestId('planning-save').click()
  await expect(page.getByText('行程或输入已变化，请重新加载后检查并保存。')).toBeVisible()
})

test('owner timeline state action sends concurrency guards and scope keeps one activity', async ({ page }) => {
  await page.addInitScript(({ plan }) => { sessionStorage.setItem('access_token', 'fixture-browser-token'); sessionStorage.setItem('tripPlan', JSON.stringify(plan)); sessionStorage.setItem('tripPlanId', '42'); sessionStorage.setItem('tripPlanVersion', '3') }, { plan })
  await page.route('**/api/history/42', route => route.fulfill({ json: { success: true, data: { plan, version: 3, quality: {} } } }))
  await page.route('**/api/trips/42/planning-input', route => route.fulfill({ json: { data: { planning_input: planning, planning_revision: 4, trip_version: 3 } } }))
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
  let stateSaved = false
  await page.route('**/api/trips/42/activities/activity-1/state', route => {
    expect(route.request().headers()['if-match']).toBe('3')
    expect(route.request().postDataJSON()).toMatchObject({ locked: true, planning_revision: 4 })
    stateSaved = true
    return route.fulfill({ json: { data: { planning_input: { ...planning, locks: [{ activity_id: 'activity-1', mode: 'content' }] }, planning_revision: 5, trip_version: 3, plan } } })
  })
  await page.goto('/result')
  await expect(page.getByText('故宫：景点营业信息未知或已过期，请核对出行日期的开放与最后入场时间。')).toBeVisible()
  await expect(page.getByText('opening_unknown_or_stale:activity-1', { exact: true })).toHaveCount(0)
  await page.getByTestId('activity-activity-1').getByRole('button', { name: '锁定安排' }).click()
  await expect.poll(() => stateSaved).toBe(true)
  await expect(page.getByTestId('activity-activity-1').getByText('已锁定')).toBeVisible()
  await page.getByRole('button', { name: 'AI 重新安排' }).click()
  await page.getByTestId('revision-scope').locator('.ant-select-selector').first().click()
  await page.getByText('选定活动', { exact: true }).click()
  await expect(page.getByTestId('revision-scope').getByRole('combobox').nth(1)).toBeVisible()
  await page.getByTestId('revision-scope').locator('.ant-select-selector').nth(1).click()
  await page.getByTestId('revision-scope').getByRole('combobox').nth(1).press('ArrowDown')
  await page.getByTestId('revision-scope').getByRole('combobox').nth(1).press('Enter')
  await page.getByPlaceholder('例如：下雨，改为室内博物馆并减少步行').fill('替换为室内景点')
  let scopeSubmitted = false
  await page.route('**/api/history/42/revise-task', route => { expect(route.request().postDataJSON()).toMatchObject({ planning_revision: 5, revision_scope: { day_indices: [0], activity_ids: ['activity-1'] } }); scopeSubmitted = true; return route.fulfill({ status: 202, json: { data: { id: 'revision-1' } } }) })
  await page.route('**/api/trip/tasks/revision-1', route => route.fulfill({ json: { data: { status: 'succeeded', result: { success: true, id: 43, version: 1, data: plan, quality: {} } } } }))
  await expect(page.getByText('范围外、已完成和锁定的安排会保留。', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: '生成差异提案' }).click()
  await expect.poll(() => scopeSubmitted).toBe(true)
})
