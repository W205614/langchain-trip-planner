import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.route('https://*.amap.com/**', route => route.abort())
})

test('all tasks reveals ordinary draft recovery while assistant proposals only open their result', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  await page.route('**/api/history**', route => route.fulfill({ json: { success: true, data: [], total: 0 } }))
  const requestedFilters: string[] = []
  await page.route('**/api/trip/tasks?*', route => {
    const filter = new URL(route.request().url()).searchParams.get('actionable') || ''
    requestedFilters.push(filter)
    const data = filter === 'true' ? [] : [
      { id: 'draft-1', status: 'needs_attention', city: '普通草稿', record_id: 51 },
      { id: 'proposal-1', status: 'needs_attention', city: '助手提案', record_id: 52 },
      { id: 'finished-1', status: 'succeeded', city: '已完成行程', record_id: 53 },
      { id: 'deleted-1', status: 'needs_attention', city: '已删除结果', record_id: 54 },
    ]
    return route.fulfill({ json: { data, total: data.length } })
  })
  await page.route('**/api/trip/tasks/draft-1', route => route.fulfill({ json: { data: { id: 'draft-1', status: 'needs_attention', result: { id: 51, quality: { outcome: 'draft' } } } } }))
  await page.route('**/api/trip/tasks/proposal-1', route => route.fulfill({ json: { data: { id: 'proposal-1', status: 'needs_attention', result: { id: 52, quality: { outcome: 'draft', assistant_confirmation_required: true, assistant_proposal_status: 'pending' } } } } }))
  await page.route('**/api/trip/tasks/deleted-1', route => route.fulfill({ json: { data: { id: 'deleted-1', status: 'failed', error_code: 'RESULT_DELETED', message: '行程已删除' } } }))
  let resumes = 0
  await page.route('**/api/tasks/draft-1/resume', route => {
    expect(route.request().headers()['idempotency-key']).toBeTruthy()
    resumes++
    return route.fulfill({ json: { data: { id: 'resumed-1', status: 'queued', resumed_from: 'draft-1' } } })
  })
  await page.goto('/history?tab=tasks')
  await expect(page.getByText('当前显示需要操作的任务')).toBeVisible()
  expect(requestedFilters).toEqual(['true'])
  await page.getByText('全部任务', { exact: true }).click()
  await expect(page.getByTestId('task-draft-1').getByRole('button', { name: '恢复有效阶段' })).toBeVisible()
  await expect(page.getByTestId('task-draft-1').getByRole('button', { name: '查看结果' })).toBeVisible()
  await expect(page.getByTestId('task-proposal-1').getByRole('button', { name: '查看助手方案' })).toBeVisible()
  await expect(page.getByTestId('task-proposal-1').getByRole('button', { name: /恢复有效阶段|重新提交/ })).toHaveCount(0)
  await expect(page.getByTestId('task-finished-1').getByRole('button', { name: '查看结果' })).toBeVisible()
  await expect(page.getByTestId('task-finished-1').getByRole('button', { name: /恢复有效阶段|重新提交/ })).toHaveCount(0)
  await expect(page.getByTestId('task-deleted-1').getByRole('button')).toHaveCount(0)
  await page.getByTestId('task-draft-1').getByRole('button', { name: '恢复有效阶段' }).click()
  await expect.poll(() => resumes).toBe(1)
  await expect(page.getByText('已创建关联执行，将校验可复用阶段')).toBeVisible()
  await expect(page.getByRole('radio', { name: '全部任务' })).toBeEnabled()
  await page.getByText('需要操作', { exact: true }).click()
  await expect(page.getByTestId('task-draft-1')).toHaveCount(0)
  expect(requestedFilters).toContain('false')
})

test('unavailable draft details do not enable recovery or hide the other task results', async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem('access_token', 'fixture-browser-token'))
  await page.route('**/api/history**', route => route.fulfill({ json: { success: true, data: [], total: 0 } }))
  await page.route('**/api/trip/tasks?*', route => route.fulfill({ json: { data: [
    { id: 'unknown-1', status: 'needs_attention', city: '待核对草稿', record_id: 51 },
    { id: 'finished-1', status: 'succeeded', city: '已完成行程', record_id: 53 },
  ], total: 2 } }))
  await page.route('**/api/trip/tasks/unknown-1', route => route.fulfill({ status: 503, json: { message: '暂不可用' } }))
  await page.goto('/history?tab=tasks')
  await expect(page.getByTestId('task-unknown-1')).toContainText('任务详情暂不可用')
  await expect(page.getByTestId('task-unknown-1').getByRole('button', { name: /恢复有效阶段|重新提交/ })).toHaveCount(0)
  await expect(page.getByTestId('task-finished-1').getByRole('button', { name: '查看结果' })).toBeVisible()
})
