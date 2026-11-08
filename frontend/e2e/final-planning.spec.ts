import { expect, test } from '@playwright/test'

const planning = { timezone: 'Asia/Shanghai', day_start: '09:00', day_end: '20:00', day_windows: [], city_segments: [], commitments: [], hotel_stays: [], meal_windows: [], opening_windows: [], locks: [], completed_activity_ids: [], budget_mode: 'estimate' }
const plan = { city: '北京', start_date: '2026-11-01', end_date: '2026-11-01', weather_info: [], overall_suggestions: '核对预约', days: [{ day_index: 0, date: '2026-11-01', attractions: [{ activity_id: 'activity-1', poi_id: 'P1', name: '故宫', address: '北京', description: '游览', visit_duration: 120, location: { longitude: 116.4, latitude: 39.9 } }], meals: [], transportation: '公共交通', accommodation: '酒店', description: '按预约游览', timeline: [{ activity_id: 'activity-1', title: '故宫', kind: 'attraction', status: 'estimated', start_at: '2026-11-01T09:00:00+08:00', end_at: '2026-11-01T11:00:00+08:00', notes: ['营业信息待核实'] }], feasibility: { status: 'unverified', unknown: ['opening_unknown_or_stale:activity-1'] } }] }

test('reduced attraction count stays visible alongside the final itinerary', async ({ page }) => {
  await page.addInitScript(({ plan }) => {
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
    sessionStorage.setItem('tripQuality', JSON.stringify({ attraction_adjustments: [{ day_index: 0, before_count: 2, after_count: 1 }] }))
  }, { plan })
  await page.route('https://*.amap.com/**', route => route.abort())
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
  await page.goto('/result')
  await expect(page.getByTestId('attraction-count-adjustment')).toContainText('第1天的景点由2个减少为1个，原数量未保留')
  await expect(page.getByTestId('attraction-count-adjustment')).toContainText('如需保留原数量')
})

test('an unmet daily attraction target explains the final count without a duplicate reduction notice', async ({ page }) => {
  await page.addInitScript(plan => {
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
    sessionStorage.setItem('tripQuality', JSON.stringify({
      attraction_targets: [{ day_index: 0, target_count: 2, actual_count: 1, met: false }],
      attraction_adjustments: [{ day_index: 0, before_count: 2, after_count: 1 }]
    }))
  }, plan)
  await page.route('https://*.amap.com/**', route => route.abort())
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
  await page.goto('/result')
  await expect(page.getByTestId('attraction-target-unmet')).toContainText('第1天要求2个景点，实际安排1个，数量目标尚未满足')
  await expect(page.getByTestId('attraction-target-unmet')).toContainText('补充可用景点或调整每日时间')
  await expect(page.getByTestId('attraction-count-adjustment')).toHaveCount(0)
})

for (const [code, phrase] of [
  ['arrival_endpoint_unknown', '抵达地点尚未确认'],
  ['departure_endpoint_unknown', '离开地点尚未确认'],
  ['start_endpoint_unknown', '当天出发地点尚未确认']
] as const) {
  test(`${code} has a useful endpoint explanation`, async ({ page }) => {
    await page.addInitScript(({ plan, code }) => {
      sessionStorage.setItem('tripPlan', JSON.stringify(plan))
      sessionStorage.setItem('tripQuality', JSON.stringify({ data_gaps: [code] }))
    }, { plan, code })
    await page.route('https://*.amap.com/**', route => route.abort())
    await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
    await page.goto('/result')
    await expect(page.getByText(new RegExp(phrase))).toBeVisible()
    await expect(page.getByText(code, { exact: true })).toHaveCount(0)
  })
}

test('fact warning lists only the unresolved evidence instead of fixed missing-fact claims', async ({ page }) => {
  await page.addInitScript(({ plan }) => {
    sessionStorage.setItem('tripPlan', JSON.stringify(plan))
    sessionStorage.setItem('tripQuality', JSON.stringify({ data_gaps: ['in_attraction_walking_unknown'] }))
  }, { plan })
  await page.route('https://*.amap.com/**', route => route.abort())
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
  await page.goto('/result')
  await expect(page.getByText('景区内部步行距离尚无可靠数据。', { exact: true })).toBeVisible()
  await expect(page.getByText(/酒店或餐厅接驳路线仍待核实/)).toHaveCount(0)
  await expect(page.getByText(/预约要求及余票请通过景区官方渠道确认/)).toHaveCount(0)
  await expect(page.getByText(/部分景点或餐厅的出行日开放时间待核实/)).toHaveCount(0)
})

test('legacy missing or stale schedule explains why the unchanged day still needs verification', async ({ page }) => {
  const code = 'schedule_unknown_or_stale'
  const sample = { ...plan, days: [{ ...plan.days[0], timeline: [], feasibility: { status: 'unverified', unknown: [code] } }] }
  await page.addInitScript(({ sample, code }) => { sessionStorage.setItem('tripPlan', JSON.stringify(sample)); sessionStorage.setItem('tripQuality', JSON.stringify({ data_gaps: [code], feasibility_status: 'unverified', facts_complete: false })) }, { sample, code })
  await page.route('https://*.amap.com/**', route => route.abort())
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
  await page.goto('/result')
  await expect(page.locator('.timeline')).toContainText('旧时间轴缺失或已过期，请重新核验或改排。')
  await expect(page.locator('.timeline')).toContainText('时间轴待重新核验')
  await expect(page.locator('.timeline')).toContainText('此时间轴的条件或事实已过期，仅供对照；请补充事实并重新规划后再锁定时段或标记完成。')
  await expect(page.getByText(/部分日期的旧时间轴缺失或已过期，请重新核验或改排/)).toBeVisible()
  await expect(page.getByText(code, { exact: true })).toHaveCount(0)
  await expect(page.locator('.timeline').getByText('满足已知时间约束', { exact: true })).toHaveCount(0)
})

test('omitted restaurant candidate remains an explicit dated meal gap', async ({ page }) => {
  const code = 'meal_candidate_unassigned:lunch:omitted-lunch'
  const sample = { ...plan, days: [{ ...plan.days[0], feasibility: { status: 'unverified', unknown: [code] } }] }
  await page.addInitScript(({ sample, code }) => { sessionStorage.setItem('tripPlan', JSON.stringify(sample)); sessionStorage.setItem('tripQuality', JSON.stringify({ data_gaps: [code], unmet_meals: [{ day_index: 0, activity_id: 'omitted-lunch', type: 'lunch', title: '远郊餐厅', reason: 'restaurant_candidate_does_not_fit_time_and_routes', action: '该餐厅未安排，需就近用餐并重新核验' }] })) }, { sample, code })
  await page.route('https://webapi.amap.com/**', route => route.abort())
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
  await page.goto('/result')
  const gap = page.getByTestId('unmet-meals')
  await expect(gap.getByText('第 1 天（2026-11-01） · 午餐 · 远郊餐厅：未安排')).toBeVisible()
  await expect(gap.getByText('该餐厅无法同时满足用餐时间与接驳路线。 该餐厅未安排，需就近用餐并重新核验')).toBeVisible()
  await expect(page.getByText('午餐候选未安排，需就近用餐并重新核验。')).toBeVisible()
  await expect(page.getByText(code, { exact: true })).toHaveCount(0)
  await expect(page.getByText('restaurant_candidate_does_not_fit_time_and_routes', { exact: true })).toHaveCount(0)
})

for (const [category, status] of [['transport', 'confirmed'], ['train', null], ['flight', undefined]] as const) {
test(`legacy ${category} cross-city service still warns about unmodeled station arrival buffer`, async ({ page }) => {
  const code = 'transport_predeparture_buffer_unverified:confirmed-train'
  const sample = { ...plan, planning_input: { ...planning, commitments: [{ id: 'confirmed-train', version: 1, day_index: 0, category, status, title: 'G123', origin_city: '杭州', destination_city: '上海', start_at: '2026-11-01T08:00:00+08:00', end_at: '2026-11-01T09:00:00+08:00' }] }, days: [{ ...plan.days[0], feasibility: { status: 'unverified', unknown: [code] } }] }
  await page.addInitScript(sample => { sessionStorage.setItem('tripPlan', JSON.stringify(sample)); sessionStorage.setItem('tripQuality', '{}') }, sample)
  await page.route('https://webapi.amap.com/**', route => route.abort())
  await page.route('**/api/poi/photo/image*', route => route.fulfill({ status: 404 }))
  await page.goto('/result')
  await expect(page.getByTestId('transport-predeparture-warning')).toContainText('尚未核实进站、安检和检票所需提前量；请按班次要求提前到站并调整出发时间或住宿。')
  await expect(page.locator('.timeline')).toContainText('G123：尚未核实进站、安检和检票所需提前量；请按班次要求提前到站并调整出发时间或住宿。')
  await expect(page.getByText(code, { exact: true })).toHaveCount(0)
})
}

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
