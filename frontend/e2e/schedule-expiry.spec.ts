import { expect, test } from '@playwright/test'

const now = new Date('2026-11-09T08:00:00Z')
const plan = {
  city: '北京', start_date: '2026-11-10', end_date: '2026-11-10',
  days: [{ day_index: 0, date: '2026-11-10', attractions: [], meals: [],
    timeline: ['confirmed', 'estimated'].map((status, index) => ({
      activity_id: `activity-${index}`, title: `活动${index + 1}`, kind: 'attraction', status,
      start_at: '2026-11-10T09:00:00+08:00', end_at: '2026-11-10T10:00:00+08:00'
    })),
    feasibility: { status: 'feasible', unknown: [] },
    schedule_dependencies: { input_hash: 'input', fact_hash: 'facts', expires_at: '2026-11-09T08:00:30Z' }
  }]
}

test('open shared timeline withdraws confirmed and estimated labels when dependencies expire', async ({ page }) => {
  await page.clock.install({ time: now })
  await page.route('**/api/shared-trips/expiry-fixture', route => route.fulfill({ json: { data: { plan, city: plan.city } } }))
  await page.goto('/shared/expiry-fixture')
  const timeline = page.locator('.timeline')
  await expect(timeline.getByText('已确认安排', { exact: true })).toBeVisible()
  await expect(timeline.getByText('时间估算', { exact: true })).toBeVisible()
  await page.clock.fastForward(31000)
  await expect(timeline).toContainText('时间轴待重新核验')
  await expect(timeline.getByText('已确认安排', { exact: true })).toHaveCount(0)
  await expect(timeline.getByText('时间估算', { exact: true })).toHaveCount(0)
  await expect(timeline.getByText('待核实', { exact: true })).toHaveCount(2)
})

test('shared legacy timeline without dependency bindings never certifies activity status', async ({ page }) => {
  const legacy = { ...plan, days: [{ ...plan.days[0], schedule_dependencies: undefined }] }
  await page.route('**/api/shared-trips/expiry-fixture', route => route.fulfill({ json: { data: { plan: legacy, city: plan.city } } }))
  await page.goto('/shared/expiry-fixture')
  const timeline = page.locator('.timeline')
  await expect(timeline).toContainText('时间轴待重新核验')
  await expect(timeline.getByText('已确认安排', { exact: true })).toHaveCount(0)
  await expect(timeline.getByText('时间估算', { exact: true })).toHaveCount(0)
  await expect(timeline.getByText('待核实', { exact: true })).toHaveCount(2)
})

test('a schedule with no expiring evidence keeps its estimates without an invented thirty minute expiry', async ({ page }) => {
  await page.clock.install({ time: now })
  const estimate = { ...plan, days: [{ ...plan.days[0],
    timeline: [{ ...plan.days[0].timeline[1] }],
    feasibility: { status: 'unverified', unknown: ['arrival_endpoint_unknown'] },
    schedule_dependencies: { ...plan.days[0].schedule_dependencies, expires_at: null }
  }] }
  await page.route('**/api/shared-trips/expiry-fixture', route => route.fulfill({ json: { data: { plan: estimate, city: plan.city } } }))
  await page.goto('/shared/expiry-fixture')
  const timeline = page.locator('.timeline')
  await expect(timeline).toContainText('部分条件待核实')
  await page.clock.fastForward(7200000)
  await expect(timeline.getByText('时间估算', { exact: true })).toBeVisible()
  await expect(timeline).toContainText('尚未填写实际抵达地点')
  await expect(timeline).not.toContainText('时间轴待重新核验')
  await expect(timeline.getByText('已确认安排', { exact: true })).toHaveCount(0)
})

test('an unscheduled draft reports its missing schedule without claiming facts expired', async ({ page }) => {
  const draft = { ...plan, days: [{ ...plan.days[0], timeline: [], schedule_dependencies: undefined,
    feasibility: { status: 'not_found', conflicts: ['candidate_time_model_has_no_solution'], unknown: [] }
  }] }
  await page.route('**/api/shared-trips/expiry-fixture', route => route.fulfill({ json: { data: { plan: draft, city: plan.city } } }))
  await page.goto('/shared/expiry-fixture')
  const timeline = page.locator('.timeline')
  await expect(timeline).toContainText('尚未生成可用时间轴')
  await expect(timeline).toContainText('计算预算内未找到方案')
  await expect(timeline).not.toContainText('已过期')
})
