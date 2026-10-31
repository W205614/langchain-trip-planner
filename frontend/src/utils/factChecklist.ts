import type { PlanningInput, TripPlan } from '@/types'

export type FactKind = 'opening' | 'reservation' | 'boarding'
export interface FactGap {
  key: string
  kind: FactKind
  label: string
  status: '未补充' | '声明已到期' | '待确认' | '声明时间无效' | '声明冲突' | '尚无可用预约' | '声明闭馆'
  poiId?: string
  commitmentId?: string
}

// This checklist helps complete input. Only the server's subsequent solve can
// establish freshness, route feasibility and the effective schedule status.
export function factChecklist(plan: TripPlan, input: PlanningInput, dayIndex: number, now: number): FactGap[] {
  const day = plan.days.find(item => item.day_index === dayIndex)
  if (!day) return []
  const gaps: FactGap[] = []
  function add(kind: FactKind, key: string, label: string,
    facts: { source: string; confirmed: boolean; observed_at?: string; expires_at?: string; status?: string; closed?: boolean }[],
    identity: { poiId?: string; commitmentId?: string }) {
    let status: FactGap['status'] | undefined
    if (!facts.length) status = '未补充'
    else if (facts.length > 1) status = '声明冲突'
    else {
      const fact = facts[0], observed = Date.parse(fact.observed_at || ''), expiry = Date.parse(fact.expires_at || '')
      if (!fact.confirmed || !fact.source.trim()) status = '待确认'
      else if (!Number.isFinite(observed) || !Number.isFinite(expiry) || observed > now || expiry <= observed) status = '声明时间无效'
      else if (expiry <= now) status = '声明已到期'
      else if (fact.status === 'unavailable') status = '尚无可用预约'
      else if (fact.closed) status = '声明闭馆'
    }
    if (status) gaps.push({ kind, key, label, status, ...identity })
  }
  for (const place of [...day.attractions, ...day.meals]) {
    if (!place.poi_id) continue
    const identity = { poiId: place.poi_id }
    add('opening', `opening:${place.poi_id}`, `${place.name} · 营业与最后入场`,
      input.opening_windows.filter(fact => fact.poi_id === place.poi_id && fact.date === day.date), identity)
    if (day.attractions.some(attraction => attraction.poi_id === place.poi_id))
      add('reservation', `reservation:${place.poi_id}`, `${place.name} · 预约`,
        (input.reservation_facts || []).filter(fact => fact.poi_id === place.poi_id && fact.date === day.date), identity)
  }
  for (const trip of input.commitments) {
    if (trip.day_index !== dayIndex || trip.status !== 'confirmed' || !['transport', 'train', 'flight'].includes(trip.category)) continue
    if (!trip.origin_city || !trip.destination_city || trip.origin_city === trip.destination_city) continue
    const aliases = new Set([String(trip.id), trip.input_commitment_id].filter(Boolean))
    add('boarding', `boarding:${trip.id}`, `${trip.title} · 提前到站`,
      (input.transport_buffers || []).filter(fact => aliases.has(fact.commitment_id)), { commitmentId: String(trip.id) })
  }
  return [...new Map(gaps.map(gap => [gap.key, gap])).values()]
}

export function factTimeError(observed: string, expiry: string, now: number): string {
  const start = Date.parse(observed), end = Date.parse(expiry)
  return !Number.isFinite(start) || !Number.isFinite(end) || start > now + 300_000 || end <= Math.max(start, now)
    ? '获取时间须有效且不能超过当前时间5分钟，有效期须晚于获取时间和当前时间。' : ''
}
