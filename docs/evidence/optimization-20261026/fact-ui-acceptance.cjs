/* Run explicitly against the isolated fixture deployment only:
 *   $env:BASE_URL='http://127.0.0.1:18088'; node docs/evidence/optimization-20261026/fact-ui-acceptance.cjs
 * Uses real same-origin business HTTP and Cookie UI login. No business response
 * interception, Storage authentication injection, Docker operation or paid call.
 */
'use strict'
const assert = require('node:assert/strict')
const path = require('node:path')
const fs = require('node:fs/promises')
const { randomUUID, randomBytes } = require('node:crypto')
const repo = path.resolve(__dirname, '../../..')
const { chromium, expect } = require(path.join(repo, 'frontend/node_modules/@playwright/test'))
const BASE_URL = process.env.BASE_URL || 'http://127.0.0.1:18088'
const origin = new URL(BASE_URL)
assert.equal(origin.origin, 'http://127.0.0.1:18088', 'Only the explicit isolated loopback deployment is permitted')
assert.ok(!origin.username && !origin.password && !origin.search && !origin.hash)
const stamp = `${Date.now().toString(36)}_${randomUUID().slice(0, 5)}`
const output = path.resolve(process.env.FACT_UI_OUTPUT_DIR || path.join(__dirname, `fact-ui-run-${stamp}`))
const password = `Isolated!Fact_${randomBytes(12).toString('hex')}`
const report = { started_at: new Date().toISOString(), base_url: origin.origin, passed: false,
  boundary: { real_business_http: true, cookie_ui_login: true, provider_fixture: true,
    external_browser_resources_aborted: true, business_response_fulfill: false,
    real_supplier_validation: false, direct_paid_provider_calls: 0, backend_provider_counts: 'not_measured_here' }, checks: [], screenshots: [], http: [],
  coverage: ['fact_editor_source_ttl_declaration', 'save_planning_revision', 'local_replan_preview', 'explicit_proposal_confirmation'],
  not_covered_here: ['reverify_failure_retry_ui', 'explicit_poi_replacement_ui'] }
let browser, context, page
function checked(name, detail) { report.checks.push({ name, passed: true, ...(detail || {}) }) }
function localMinute(value) {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(value).replace(' ', 'T')
}
function localDay(value) { return localMinute(value).slice(0, 10) }
function responseFor(method, pathname) {
  return page.waitForResponse(response => response.request().method() === method &&
    new URL(response.url()).pathname === pathname, { timeout: 90_000 })
}
async function api(method, pathname, data, expected = 200, extra = {}) {
  const csrf = (await context.cookies()).find(cookie => cookie.name === 'trip_csrf')?.value || ''
  const response = await context.request.fetch(pathname, { method, data,
    headers: { 'X-CSRF-Token': csrf, ...extra } })
  assert.equal(response.status(), expected, `${method} ${pathname}`)
  return response.json()
}
async function terminal(taskId) {
  let state
  await expect.poll(async () => {
    state = (await api('GET', `/api/trip/tasks/${taskId}`)).data
    return state.status
  }, { timeout: 180_000, intervals: [300, 600, 1000] }).toMatch(/^(succeeded|needs_attention|failed|cancelled)$/)
  assert.match(state.status, /^(succeeded|needs_attention)$/, 'The actual task must create a reviewable result')
  assert.ok(Number(state.result?.id) > 0)
  return state
}
async function safeScreenshot(name) {
  assert.match(new URL(page.url()).pathname, /^\/(trips\/\d+\/operations|result|history)$/)
  assert.equal(await page.locator('input[type="password"]:visible').count(), 0)
  const destination = path.join(output, name)
  await page.screenshot({ path: destination, fullPage: true })
  report.screenshots.push(destination)
}

;(async () => {
  await fs.mkdir(output, { recursive: true })
  browser = await chromium.launch({ headless: true })
  context = await browser.newContext({ baseURL: origin.origin, viewport: { width: 1440, height: 1000 } })
  context.setDefaultTimeout(20_000)
  // External SDK/fonts are unavailable by design. Same-origin HTTP always
  // continues to the real server, including every business mutation below.
  await context.route('**/*', route => {
    const target = new URL(route.request().url())
    return !['http:', 'https:'].includes(target.protocol) || target.origin === origin.origin
      ? route.continue() : route.abort('blockedbyclient')
  })
  const marker = await context.request.get('/api/validation/fixture')
  assert.equal(marker.status(), 200, 'Refuse all registration/seeding outside the isolated fixture stack')
  assert.equal((await marker.json()).offline_fixture, true)
  checked('isolated_fixture_marker')
  const username = `fact_${stamp}`
  await api('POST', '/api/auth/register', { username, password })
  page = await context.newPage()
  page.on('response', response => {
    const url = new URL(response.url())
    if (url.origin === origin.origin && url.pathname.startsWith('/api/'))
      report.http.push({ method: response.request().method(), path: url.pathname, status: response.status() })
  })
  await page.goto('/login')
  await page.getByPlaceholder('用户名', { exact: true }).fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill(password)
  const login = responseFor('POST', '/api/auth/login')
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  assert.equal((await login).status(), 200)
  await expect(page).toHaveURL(`${origin.origin}/`)
  assert.equal((await context.cookies()).find(cookie => cookie.name === 'trip_session')?.httpOnly, true)
  checked('ui_login_issued_http_only_cookie')

  const start = new Date(Date.now() + 10 * 86400000), end = new Date(start.getTime() + 86400000)
  const created = await api('POST', '/api/trip/tasks', { city: '北京', start_date: localDay(start), end_date: localDay(end),
    travel_days: 2, transportation: '公共交通', accommodation: '经济型酒店', preferences: [],
    planning_input: { timezone: 'Asia/Shanghai', day_start: '09:00', day_end: '20:00' } },
    202, { 'Idempotency-Key': randomUUID() })
  const tripId = Number((await terminal(created.data.id)).result.id)
  report.trip_id = tripId
  const generated = (await api('GET', `/api/history/${tripId}`)).data
  let initial = (await api('GET', `/api/trips/${tripId}/planning-input`)).data
  assert.equal(generated.plan.days.length, 2)
  const protectedActivity = generated.plan.days[0].attractions.find(item => item.activity_id && item.poi_id)
  assert.ok(protectedActivity, 'The isolated seed must contain a stable activity to protect')
  await api('POST', `/api/trips/${tripId}/activities/${encodeURIComponent(protectedActivity.activity_id)}/state`,
    { locked: true, lock_mode: 'content', planning_revision: initial.planning_revision }, 200,
    { 'If-Match': String(initial.trip_version) })
  initial = (await api('GET', `/api/trips/${tripId}/planning-input`)).data
  assert.ok(initial.planning_input.locks.some(item => item.activity_id === protectedActivity.activity_id && item.mode === 'content'))
  const original = (await api('GET', `/api/history/${tripId}`)).data
  const protectedContent = item => ({ activity_id: item.activity_id, poi_id: item.poi_id,
    name: item.name, visit_duration: item.visit_duration, location: item.location })
  report.protected_activity_id = protectedActivity.activity_id
  await page.goto(`/trips/${tripId}/operations`)
  await page.getByRole('tab', { name: '行前核验', exact: true }).click()
  const facts = page.getByTestId('fact-completion')
  await expect(facts).toBeVisible()
  await facts.getByLabel(/^处理日期/).selectOption('0')
  await facts.getByLabel(/^补充类型/).selectOption('meal')
  await facts.getByLabel(/^餐别/).selectOption('lunch')
  await facts.getByLabel('经度', { exact: true }).fill('116.397')
  await facts.getByLabel('纬度', { exact: true }).fill('39.908')
  const source = `合成验收自理用餐声明-${stamp}`
  const observed = localMinute(new Date(Date.now() - 120000))
  const expiry = localMinute(new Date(Date.now() + 2 * 3600000))
  await facts.getByLabel('来源名称或链接', { exact: true }).fill(source)
  await facts.getByLabel('获取时间', { exact: true }).fill(observed)
  await facts.getByLabel('有效至', { exact: true }).fill(expiry)
  await facts.getByRole('checkbox', { name: '我已核对以上信息，了解这是我的声明，系统不会代订票或确认官方真实性', exact: true }).check()
  await facts.getByRole('button', { name: '加入待保存事实', exact: true }).click()
  await facts.getByText('检查已加入的补充事实、来源与有效期', { exact: true }).click()
  await expect(facts.getByText(source, { exact: false })).toBeVisible()
  await expect(facts.getByText(/已补充 1 项/)).toBeVisible()
  assert.equal((await api('GET', `/api/trips/${tripId}/planning-input`)).data.planning_revision, initial.planning_revision)
  assert.deepEqual((await api('GET', `/api/history/${tripId}`)).data.plan, original.plan)
  checked('adding_unsaved_fact_does_not_mutate_server_plan_or_revision')
  await safeScreenshot('01-fact-source-ttl-awaiting-save.png')

  const saving = responseFor('PUT', `/api/trips/${tripId}/planning-input`)
  const submitted = page.waitForResponse(response => response.request().method() === 'POST' &&
    /^\/api\/assistant\/conversations\/[^/]+\/messages$/.test(new URL(response.url()).pathname), { timeout: 90_000 })
  await facts.getByRole('button', { name: '保存已补充事实并局部重验求解', exact: true }).click()
  const savedResponse = await saving
  assert.equal(savedResponse.status(), 200)
  const saved = (await savedResponse.json()).data
  assert.equal(saved.planning_revision, initial.planning_revision + 1)
  const fact = saved.planning_input.meal_resolutions.find(item => item.day_index === 0 && item.type === 'lunch')
  assert.ok(fact)
  assert.equal(fact.authority, 'user_declared')
  assert.equal(fact.source, source)
  assert.equal(fact.confirmed, true)
  assert.equal(Date.parse(fact.observed_at), Date.parse(`${observed}:00+08:00`))
  assert.equal(Date.parse(fact.expires_at), Date.parse(`${expiry}:00+08:00`))
  assert.deepEqual(fact.location, { longitude: 116.397, latitude: 39.908 })
  assert.deepEqual(saved.planning_input.locks, initial.planning_input.locks)
  assert.deepEqual(saved.planning_input.completed_activity_ids || [], initial.planning_input.completed_activity_ids || [])
  checked('saved_fact_preserves_source_ttl_and_user_declared_authority', { planning_revision: saved.planning_revision })
  const baseline = (await api('GET', `/api/history/${tripId}`)).data
  // Saving may legitimately project obsolete solver metadata as stale. Content
  // is not replanned until a proposal is explicitly reviewed and confirmed.
  assert.deepEqual(baseline.plan.days.map(day => day.attractions), original.plan.days.map(day => day.attractions))
  const submission = await submitted
  assert.equal(submission.status(), 202)
  const accepted = await submission.json()
  assert.equal(accepted.status, 'accepted')
  assert.ok(accepted.task_id)
  report.task_id = accepted.task_id
  const proposalId = Number((await terminal(accepted.task_id)).result.id)
  report.proposal_id = proposalId
  const proposal = (await api('GET', `/api/history/${proposalId}`)).data
  assert.equal(proposal.quality.assistant_proposal_status, 'pending')
  assert.deepEqual(protectedContent(proposal.plan.days[0].attractions.find(item => item.activity_id === protectedActivity.activity_id)),
    protectedContent(protectedActivity), 'Local replan must retain the actual content-locked activity')
  assert.deepEqual(proposal.plan.days[1], baseline.plan.days[1], 'The untargeted day must be retained')
  const waiting = (await api('GET', `/api/history/${tripId}`)).data
  assert.equal(waiting.version, baseline.version)
  assert.deepEqual(waiting.plan, baseline.plan, 'The generated proposal must not overwrite its original')
  checked('local_replan_preserves_untargeted_day_and_original_until_confirmation')

  await page.goto('/history?tab=tasks')
  await page.getByText('全部任务', { exact: true }).click()
  await page.getByTestId(`task-${accepted.task_id}`).getByRole('button', { name: '查看助手方案', exact: true }).click()
  await expect(page.getByTestId('proposal-diff')).toBeVisible()
  await expect(page.getByText(`待确认的助手改排方案；原行程 #${tripId} 尚未更新。`, { exact: true })).toBeVisible()
  await safeScreenshot('02-local-replan-preview-before-confirmation.png')
  const confirming = responseFor('POST', `/api/assistant/conversations/${proposal.quality.assistant_conversation_id}/proposals/${proposalId}/confirm`)
  await page.getByRole('button', { name: '确认应用到原行程', exact: true }).click()
  const confirmed = await confirming
  assert.equal(confirmed.status(), 200)
  const result = await confirmed.json()
  assert.equal(Number(result.id), tripId)
  const applied = (await api('GET', `/api/history/${tripId}`)).data
  assert.ok(applied.version > baseline.version)
  assert.deepEqual(applied.plan.days[1], baseline.plan.days[1])
  const effective = (await api('GET', `/api/trips/${tripId}/planning-input`)).data
  assert.deepEqual(effective.planning_input.locks, initial.planning_input.locks)
  assert.equal(effective.planning_input.meal_resolutions.find(item => item.day_index === 0 && item.type === 'lunch').source, source)
  checked('explicit_ui_confirmation_updates_only_the_owned_original_and_retains_fact')
  await safeScreenshot('03-confirmed-fact-replan.png')
  report.passed = true
})().catch(error => {
  // Never retain credentials, headers, Cookie values, request bodies or a login
  // screenshot in evidence. Failures remain failures; there is no skip/retry-to-pass.
  report.error = { type: error.name, message: String(error.message).split(password).join('[REDACTED]').slice(0, 2200) }
  process.exitCode = 1
}).finally(async () => {
  report.finished_at = new Date().toISOString()
  if (context) await context.close().catch(() => {})
  if (browser) await browser.close().catch(() => {})
  await fs.mkdir(output, { recursive: true })
  await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.join(output, 'report.json'),
    error: report.error || null }, null, 2))
})
