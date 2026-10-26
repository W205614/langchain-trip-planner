import type { TripPlan, DayPlan } from '@/types'

export function scheduleCurrent(day: DayPlan, now = Date.now()): boolean {
  const binding = day.schedule_dependencies
  return !day.schedule_stale && !!binding?.input_hash && !!binding.fact_hash &&
    Number.isFinite(Date.parse(binding.expires_at)) && Date.parse(binding.expires_at) > now
}

export function refreshScheduleValidity(plan: TripPlan, forceStale = false): boolean {
  let changed = false
  for (const day of plan.days) {
    if (!day.timeline?.length || !forceStale && scheduleCurrent(day)) continue
    day.schedule_stale = true
    day.feasibility = { ...day.feasibility, status: 'unverified', solver_status: 'DEPENDENCY_EXPIRED',
      unknown: [...new Set([...(day.feasibility?.unknown || []), 'schedule_unknown_or_stale'])] }
    for (const item of day.timeline) item.status = 'unverified'
    changed = true
  }
  return changed
}
