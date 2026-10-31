import { expect, test } from '@playwright/test'
import { factChecklist, factTimeError } from '../src/utils/factChecklist'
import { proposalChanges } from '../src/utils/proposalChanges'
import type { PlanningInput, TripPlan, TravelFact } from '../src/types'

const now = Date.parse('2026-11-01T02:00:00Z')
const fact: TravelFact = { source: '用户核对的公告', confirmed: true, observed_at: '2026-11-01T01:00:00Z', expires_at: '2026-11-01T07:00:00Z' }
function fixture(): { plan: TripPlan; input: PlanningInput } {
  return {
    plan: { city: '北京', start_date: '2026-11-02', end_date: '2026-11-02', weather_info: [], overall_suggestions: '',
      days: [{ day_index: 0, date: '2026-11-02', description: '', transportation: '公共交通', accommodation: '酒店',
        attractions: [{ poi_id: 'A', name: '景点A', address: '', location: { longitude: 116, latitude: 39 }, visit_duration: 60, description: '' }], meals: [] }] },
    input: { timezone: 'Asia/Shanghai', day_start: '09:00', day_end: '20:00', day_windows: [], city_segments: [], commitments: [],
      hotel_stays: [], meal_windows: [], opening_windows: [], locks: [], completed_activity_ids: [], budget_mode: 'estimate' },
  }
}

test('fact checklist identifies the exact venue and date without treating filled declarations as feasibility', () => {
  const { plan, input } = fixture()
  expect(factChecklist(plan, input, 0, now).map(gap => [gap.kind, gap.poiId, gap.status])).toEqual([
    ['opening', 'A', '未补充'], ['reservation', 'A', '未补充'],
  ])
  input.opening_windows = [{ ...fact, poi_id: 'A', date: '2026-11-03', windows: [], closed: false }]
  expect(factChecklist(plan, input, 0, now)[0].status).toBe('未补充')
  input.opening_windows[0].date = '2026-11-02'
  input.reservation_facts = [{ ...fact, poi_id: 'A', date: '2026-11-02', status: 'confirmed' }]
  const original = structuredClone(plan)
  expect(factChecklist(plan, input, 0, now)).toEqual([])
  expect(plan).toEqual(original)
})

test('expired, conflicting, unconfirmed and unavailable declarations stay actionable', () => {
  const { plan, input } = fixture()
  input.reservation_facts = [{ ...fact, poi_id: 'A', date: '2026-11-02', status: 'unavailable' }]
  input.opening_windows = [{ ...fact, poi_id: 'A', date: '2026-11-02', windows: [], closed: false, expires_at: '2026-11-01T01:30:00Z' }]
  expect(factChecklist(plan, input, 0, now).map(gap => gap.status)).toEqual(['声明已到期', '尚无可用预约'])
  input.opening_windows.push({ ...input.opening_windows[0] })
  expect(factChecklist(plan, input, 0, now)[0].status).toBe('声明冲突')
  input.opening_windows.pop(); input.opening_windows[0].confirmed = false
  expect(factChecklist(plan, input, 0, now)[0].status).toBe('待确认')
})

test('boarding checklist follows persisted commitment aliases and the selected day', () => {
  const { plan, input } = fixture()
  input.commitments = [{ id: 42, input_commitment_id: 'inline-rail', version: 1, day_index: 0, category: 'transport', status: 'confirmed', title: '已确认火车', origin_city: '北京', destination_city: '上海' }]
  expect(factChecklist(plan, input, 0, now).at(-1)?.commitmentId).toBe('42')
  input.transport_buffers = [{ ...fact, commitment_id: 'inline-rail', minutes: 30 }]
  expect(factChecklist(plan, input, 0, now).some(gap => gap.kind === 'boarding')).toBe(false)
  expect(factChecklist(plan, input, 1, now)).toEqual([])
})

test('fact form rejects invalid dates, expired statements and future observations', () => {
  for (const [observed, expiry] of [['invalid', fact.expires_at], [fact.observed_at, 'invalid'],
    ['2026-11-01T03:00:00Z', fact.expires_at], [fact.observed_at, '2026-11-01T01:59:59Z']])
    expect(factTimeError(observed, expiry, now)).not.toBe('')
  expect(factTimeError(fact.observed_at, fact.expires_at, now)).toBe('')
})

test('typed proposal changes retain activity duration, zero values and affected days', () => {
  expect(proposalChanges({ modified: [{ activity_id: 'A', before: { name: '原景点', visit_duration: 0 }, after: { name: '新景点', visit_duration: 60 } }],
    days: [{ day_index: 1, summary: '路线变化' }] })).toEqual(['调整：新景点（原景点 → 新景点；游览 0 → 60 分钟）', '第 2 天：路线变化'])
})
