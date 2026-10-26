/* Evidence only. Root executes once with all other final-stack tasks terminal.
 * BASE_URL=http://127.0.0.1:18088 node docs/evidence/optimization-20261026/poi-ui-acceptance.cjs
 * Only this fixed task's backend restart and amap-fixture stop/start are allowed.
 * No remove/recreate/pull/load, daily-stack operation, provider edit or HTTP fake.
 */
'use strict'
const assert = require('node:assert/strict')
const fs = require('node:fs/promises')
const path = require('node:path')
const { spawnSync } = require('node:child_process')
const { randomUUID, randomBytes } = require('node:crypto')
const repo = path.resolve(__dirname, '../../..')
const { chromium, expect } = require(path.join(repo, 'frontend/node_modules/@playwright/test'))
const baseURL = process.env.BASE_URL || 'http://127.0.0.1:18088'
assert.equal(new URL(baseURL).origin, 'http://127.0.0.1:18088')
const project = 'trip-opt-final-20261026'
const stamp = `${Date.now().toString(36)}_${randomUUID().slice(0, 5)}`
const output = path.resolve(process.env.POI_UI_OUTPUT_DIR || path.join(__dirname, `poi-ui-run-${stamp}`))
const password = `Isolated!Poi_${randomBytes(12).toString('hex')}`
const report = { passed: false, started_at: new Date().toISOString(), project, checks: [], screenshots: [],
  docker_mutations: [], restoration: {}, boundary: { real_business_http: true, cookie_ui_login: true,
    provider_fixture: true, real_supplier_validation: false, not_found_not_simulated: true,
    no_business_response_fulfill: true, direct_paid_provider_calls: 0 } }
let browser, context, page, targets, release, fixtureRestoreNeeded = false, backendTouched = false
const checked = (name, detail = {}) => report.checks.push({ name, passed: true, ...detail })
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
function dockerRead(...args) {
  const child = spawnSync('docker', args, { encoding: 'utf8', windowsHide: true, timeout: 30_000, maxBuffer: 2 * 1024 * 1024 })
  assert.equal(child.status, 0, `Read-only Docker ${args[0]} failed; stderr deliberately omitted`)
  return child.stdout
}
function validateContainer(service, identity) {
  assert.ok(['backend', 'amap-fixture'].includes(service))
  const item = JSON.parse(dockerRead('inspect', identity))[0]
  const labels = item.Config?.Labels || {}
  assert.equal(item.Id, identity)
  assert.equal(labels['com.docker.compose.project'], project)
  assert.equal(labels['com.docker.compose.service'], service)
  assert.equal(String(labels['com.docker.compose.oneoff']).toLowerCase(), 'false')
  assert.equal(labels['com.tripplanner.optimization'], '20261026')
  assert.equal(labels['com.tripplanner.validation'], 'candidate')
  assert.match(String(labels['com.docker.compose.project.config_files']).replaceAll('\\', '/'), /\.codex-optimization\/final\.compose\.json/)
  assert.equal(item.Image, release.images[service === 'backend' ? 'backend' : 'agent'].id)
  const env = new Map(item.Config.Env.map(entry => [entry.slice(0, entry.indexOf('=')), entry.slice(entry.indexOf('=') + 1)]))
  assert.equal(env.get('APP_ENV'), 'validation')
  assert.equal(env.get('VALIDATION_ALLOW_FIXTURES'), 'yes')
  if (service === 'amap-fixture') assert.equal(env.get('VALIDATION_AMAP_ONLY'), 'yes')
  else assert.equal(env.get('AMAP_REST_BASE_URL'), 'http://amap-fixture:9000/amap-fixture')
  return item
}
async function preflightContainers() {
  release = JSON.parse(await fs.readFile(path.join(__dirname, 'release-candidate-images.json'), 'utf8'))
  assert.equal(release.business_sha, 'a01a0d7ce35cac97918969157ce8bfcac8b9d506')
  targets = {}
  for (const service of ['backend', 'amap-fixture']) {
    const ids = dockerRead('ps', '-aq', '--no-trunc', '--filter', `label=com.docker.compose.project=${project}`,
      '--filter', `label=com.docker.compose.service=${service}`).trim().split(/\s+/).filter(Boolean)
    assert.equal(ids.length, 1, 'Refuse ambiguous or missing final-task container')
    const item = validateContainer(service, ids[0])
    assert.equal(item.State.Running, true, 'Final task containers must initially be running')
    targets[service] = { id: item.Id, image: item.Image, running: item.State.Running }
  }
  report.targets = targets
  checked('fixed_project_labels_images_and_initial_state_verified')
}
function mutate(action, service) {
  assert.ok((service === 'backend' && ['restart', 'start'].includes(action)) ||
    (service === 'amap-fixture' && ['stop', 'start'].includes(action)), 'Only fixed authorized lifecycle operations')
  const identity = targets[service].id
  validateContainer(service, identity) // Revalidate immediately before every mutation.
  report.docker_mutations.push({ action, service, id: identity, attempted_at: new Date().toISOString() })
  const child = spawnSync('docker', [action, ...(action === 'restart' || action === 'stop' ? ['--time', '30'] : []), identity],
    { encoding: 'utf8', windowsHide: true, timeout: 90_000, maxBuffer: 1024 * 1024 })
  assert.equal(child.status, 0, `Authorized ${service} ${action} failed; stderr deliberately omitted`)
}
async function healthy(service) {
  await expect.poll(() => {
    const item = validateContainer(service, targets[service].id)
    return item.State.Running && item.State.Health?.Status === 'healthy'
  }, { timeout: 120_000, intervals: [500, 1000] }).toBe(true)
}
async function api(method, endpoint, data, expected = 200, headers = {}) {
  const csrf = (await context.cookies()).find(cookie => cookie.name === 'trip_csrf')?.value || ''
  const response = await context.request.fetch(endpoint, { method, data, headers: { 'X-CSRF-Token': csrf, ...headers } })
  assert.equal(response.status(), expected, `${method} ${endpoint}`)
  return response.json()
}
function responseFor(method, endpoint) {
  return page.waitForResponse(response => response.request().method() === method &&
    new URL(response.url()).pathname === endpoint, { timeout: 120_000 })
}
async function screenshot(name) {
  assert.match(new URL(page.url()).pathname, /^\/(result|history|trips\/\d+\/operations)$/)
  assert.equal(await page.locator('input[type=password]:visible').count(), 0)
  const destination = path.join(output, name)
  await page.screenshot({ path: destination, fullPage: true }); report.screenshots.push(destination)
}
async function historyCard(tripId) {
  await page.goto('/history')
  const card = page.locator('.record-card').filter({ has: page.getByText(`行程 #${tripId}`, { exact: true }) })
  await expect(card).toHaveCount(1); return card
}
async function openReplacement(tripId, poiId) {
  await (await historyCard(tripId)).getByRole('button', { name: '👁️ 查看行程', exact: true }).click()
  await expect(page).toHaveURL(/\/result(?:\?|$)/)
  // History's real UI loads its record into application-managed state. No
  // browser Storage value is injected by this driver.
  await page.goto(`/result?from=history&replace_poi=${encodeURIComponent(poiId)}`)
}
const poiIds = plan => plan.days.flatMap(day => day.attractions.map(item => item.poi_id))
const attractions = plan => plan.days.map(day => day.attractions)

;(async () => {
  await fs.mkdir(output, { recursive: true })
  browser = await chromium.launch({ headless: true })
  context = await browser.newContext({ baseURL: new URL(baseURL).origin, viewport: { width: 1440, height: 1000 } })
  context.setDefaultTimeout(12_000)
  await context.route('**/*', route => {
    const target = new URL(route.request().url())
    return !['http:', 'https:'].includes(target.protocol) || target.origin === new URL(baseURL).origin
      ? route.continue() : route.abort('blockedbyclient')
  })
  const marker = await context.request.get('/api/validation/fixture')
  assert.equal(marker.status(), 200); assert.equal((await marker.json()).offline_fixture, true)
  checked('isolated_fixture_marker')
  const username = `poi_${stamp}`
  await api('POST', '/api/auth/register', { username, password })
  page = await context.newPage(); await page.goto('/login')
  await page.getByPlaceholder('用户名', { exact: true }).fill(username)
  await page.getByPlaceholder('密码', { exact: true }).fill(password)
  const loggingIn = responseFor('POST', '/api/auth/login')
  await page.getByRole('button', { name: /登\s*录/, exact: true }).click()
  assert.equal((await loggingIn).status(), 200)
  await expect(page).toHaveURL(`${new URL(baseURL).origin}/`)
  assert.equal((await context.cookies()).find(cookie => cookie.name === 'trip_session')?.httpOnly, true)
  const format = value => new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(value)
  const start = new Date(Date.now() + 10 * 86400000), end = new Date(start.getTime() + 86400000)
  const created = await api('POST', '/api/trip/tasks', { city: '北京', start_date: format(start), end_date: format(end), travel_days: 2,
    transportation: '公共交通', accommodation: '经济型酒店', preferences: [],
    planning_input: { timezone: 'Asia/Shanghai', day_start: '09:00', day_end: '20:00' } }, 202, { 'Idempotency-Key': randomUUID() })
  let task
  await expect.poll(async () => { task = (await api('GET', `/api/trip/tasks/${created.data.id}`)).data; return task.status },
    { timeout: 180_000, intervals: [300, 600, 1000] }).toMatch(/^(succeeded|needs_attention|failed|cancelled)$/)
  assert.match(task.status, /^(succeeded|needs_attention)$/)
  const tripId = Number(task.result?.id); assert.ok(tripId > 0); report.trip_id = tripId
  const generated = (await api('GET', `/api/history/${tripId}`)).data
  const choices = generated.plan.days.flatMap(day => day.attractions).filter(item => item.activity_id && item.poi_id)
  assert.ok(choices.length >= 2, 'Seed needs distinct locked and unlocked targets')
  const locked = choices[0], unlocked = choices.find(item => item.poi_id !== locked.poi_id)
  assert.ok(unlocked)
  let input = (await api('GET', `/api/trips/${tripId}/planning-input`)).data
  await api('POST', `/api/trips/${tripId}/activities/${encodeURIComponent(locked.activity_id)}/state`,
    { locked: true, lock_mode: 'content', planning_revision: input.planning_revision }, 200, { 'If-Match': String(input.trip_version) })
  input = (await api('GET', `/api/trips/${tripId}/planning-input`)).data
  const original = (await api('GET', `/api/history/${tripId}`)).data
  assert.ok(input.planning_input.locks.some(item => item.activity_id === locked.activity_id))
  checked('real_cookie_owner_seed_and_nonempty_content_lock')

  await openReplacement(tripId, unlocked.poi_id)
  const picker = page.getByRole('dialog', { name: '搜索并添加景点', exact: true })
  await expect(picker).toBeVisible()
  await picker.getByPlaceholder('输入景点名称', { exact: true }).fill('景点')
  const searching = responseFor('GET', '/api/map/poi')
  await picker.getByRole('button', { name: /搜\s*索/, exact: true }).click()
  const search = await searching; assert.equal(search.status(), 200)
  const used = new Set(poiIds(original.plan))
  const available = (await search.json()).data.find(item => item.id && item.location && !used.has(item.id))
  assert.ok(available, 'Real local fixture HTTP search must return an unused candidate')
  // `has` is evaluated relative to each list item. Repeating the outer dialog
  // in its inner locator would require a dialog nested inside that item.
  const candidate = picker.locator('.ant-list-item').filter({ has: page.getByText(available.name, { exact: true }) })
  await candidate.getByRole('button', { name: /添\s*加/, exact: true }).click()
  const review = page.getByRole('dialog', { name: '预览景点替换', exact: true })
  await expect(review).toBeVisible()
  await expect(review.getByText(`原景点：${unlocked.name}`, { exact: true })).toBeVisible()
  await expect(review.getByText(`拟替换为：${available.name}`, { exact: true })).toBeVisible()
  assert.deepEqual((await api('GET', `/api/history/${tripId}`)).data.plan, original.plan)
  await screenshot('01-explicit-replacement-review.png')
  await review.getByRole('button', { name: '确认加入编辑草稿', exact: true }).click()
  await expect(review).not.toBeVisible()
  await expect(page.getByText('替换已加入编辑草稿；保存前原行程不变', { exact: true })).toBeVisible()
  const renderedNames = await page.locator('.attraction-card .ant-card-head-title').allTextContents()
  assert.ok(renderedNames.includes(available.name)); assert.ok(renderedNames.includes(locked.name))
  const serverAfterDraft = (await api('GET', `/api/history/${tripId}`)).data
  assert.equal(serverAfterDraft.version, original.version); assert.deepEqual(serverAfterDraft.plan, original.plan)
  assert.deepEqual((await api('GET', `/api/trips/${tripId}/planning-input`)).data.planning_input.locks, input.planning_input.locks)
  checked('explicit_confirmation_changes_only_local_draft_preserving_server_and_locked_activity', { candidate_id: available.id })
  await screenshot('02-local-draft-server-unchanged.png')
  await page.getByRole('button', { name: '❌ 取消编辑', exact: true }).click()
  await openReplacement(tripId, locked.poi_id)
  await expect(page.getByText('该安排已锁定或已完成，请保留保护项或由所有者明确解除后再替换', { exact: true })).toBeVisible()
  await expect(picker).not.toBeVisible(); await expect(review).not.toBeVisible()
  assert.deepEqual((await api('GET', `/api/history/${tripId}`)).data.plan, original.plan)
  checked('locked_replacement_target_is_rejected')

  // All tasks belonging to this fresh isolated owner must be terminal before
  // fault injection. The caller must also serialize other final-stack work.
  const listing = await api('GET', '/api/trip/tasks?actionable=false')
  assert.ok(Array.isArray(listing.data) && listing.data.length > 0)
  assert.ok(listing.data.every(item => ['succeeded', 'needs_attention', 'failed', 'cancelled'].includes(item.status)))
  await preflightContainers()
  backendTouched = true; mutate('restart', 'backend'); await healthy('backend')
  fixtureRestoreNeeded = true; mutate('stop', 'amap-fixture')
  const stoppedAt = Date.now(); report.map_outage_started_at = new Date(stoppedAt).toISOString()
  assert.equal(validateContainer('amap-fixture', targets['amap-fixture'].id).State.Running, false)
  const card = await historyCard(tripId)
  const verifying = responseFor('POST', `/api/trips/${tripId}/verify`)
  // History is the first reverify entry. Workspace's "核验当天信息" uses
  // /checks and intentionally cannot be substituted for POI /verify evidence.
  await card.getByRole('button', { name: '🧭 重新核验路线', exact: true }).click()
  const failureResponse = await verifying; assert.equal(failureResponse.status(), 200)
  const failed = await failureResponse.json()
  assert.ok(failed.quality.poi_verification.length > 0)
  assert.ok(failed.quality.poi_verification.every(item => item.status === 'lookup_failed' && item.retryable === true))
  assert.deepEqual(failed.quality.poi_verification.map(item => item.poi_id).sort(), [...poiIds(original.plan)].sort())
  assert.ok(failed.quality.poi_verification.every(item => Date.parse(item.checked_at) >= stoppedAt))
  assert.deepEqual(poiIds(failed.data), poiIds(original.plan)); assert.deepEqual(attractions(failed.data), attractions(original.plan))
  assert.deepEqual((await api('GET', `/api/trips/${tripId}/planning-input`)).data.planning_input.locks, input.planning_input.locks)
  checked('actual_uncached_map_outage_yields_new_lookup_failed_findings_without_poi_or_lock_mutation')
  await page.goto(`/trips/${tripId}/operations`)
  await page.getByRole('tab', { name: '行前核验', exact: true }).click()
  const facts = page.getByTestId('fact-completion')
  await expect(facts.getByText('本次地点核验失败，可重试', { exact: true }).first()).toBeVisible()
  await screenshot('03-real-map-outage-retry-ui.png')
  mutate('start', 'amap-fixture'); await healthy('amap-fixture')
  await delay(16_100) // Existing reverify rate window, never a bypass/reset.
  const retrying = responseFor('POST', `/api/trips/${tripId}/verify`)
  await facts.getByRole('button', { name: '重试地图核验', exact: true }).first().click()
  const retryResponse = await retrying; assert.equal(retryResponse.status(), 200)
  const retried = await retryResponse.json()
  assert.ok(retried.quality.poi_verification.length > 0)
  assert.ok(retried.quality.poi_verification.every(item => item.status === 'verified' && Date.parse(item.checked_at) > stoppedAt))
  assert.deepEqual(poiIds(retried.data), poiIds(original.plan)); assert.deepEqual(attractions(retried.data), attractions(original.plan))
  assert.deepEqual((await api('GET', `/api/trips/${tripId}/planning-input`)).data.planning_input.locks, input.planning_input.locks)
  checked('actual_retry_after_fixture_restore_obtains_new_verified_findings_and_preserves_pois_and_locks')
  await expect(page.getByText('本次核验状态已更新，原景点仍保留', { exact: true })).toBeVisible()
  await screenshot('04-map-retry-completed.png')
  report.passed = true
})().catch(error => {
  report.error = { type: error.name, message: String(error.message).split(password).join('[REDACTED]').slice(0, 2400) }
  process.exitCode = 1
}).finally(async () => {
  // Restore the exact initially running fixture even when stop/verification/UI
  // assertions fail. Each action revalidates ownership and exact release image.
  if (fixtureRestoreNeeded) {
    try { mutate('start', 'amap-fixture'); await healthy('amap-fixture'); report.restoration.amap_fixture_running_and_healthy = true }
    catch (error) { report.restoration.amap_fixture_running_and_healthy = false; report.restoration.fixture_error_type = error.name; report.passed = false; process.exitCode = 1 }
  }
  if (backendTouched) {
    try {
      if (!validateContainer('backend', targets.backend.id).State.Running) mutate('start', 'backend')
      await healthy('backend'); report.restoration.backend_running_and_healthy = true
    } catch (error) { report.restoration.backend_running_and_healthy = false; report.restoration.backend_error_type = error.name; report.passed = false; process.exitCode = 1 }
  }
  if (context) await context.close().catch(() => {})
  if (browser) await browser.close().catch(() => {})
  report.finished_at = new Date().toISOString()
  await fs.mkdir(output, { recursive: true }); await fs.writeFile(path.join(output, 'report.json'), JSON.stringify(report, null, 2) + '\n')
  console.log(JSON.stringify({ passed: report.passed, checks: report.checks.length, report: path.join(output, 'report.json'), error: report.error || null }, null, 2))
})
