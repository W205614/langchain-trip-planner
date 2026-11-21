import type { DayPlan, Location, PlanningInput, TimelineItem } from '@/types'

export const tripZone = (input?: PlanningInput) => input?.timezone || 'Asia/Shanghai'
export const tripDate = (time: number, zone: string) => new Intl.DateTimeFormat('sv-SE', { timeZone: zone }).format(time)
export const tripTime = (time: number, zone: string) =>
  new Intl.DateTimeFormat('zh-CN', { timeZone: zone, hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(time)
export const tripLocalTime = (time: number, zone: string) => tripDate(time, zone) + 'T' + tripTime(time, zone)

export function orderedTimeline(day?: DayPlan) {
  const time = (item: TimelineItem) => item.start_at ? Date.parse(item.start_at) : Infinity
  return [...(day?.timeline || [])].sort((left, right) => time(left) - time(right))
}

export function zonedTimestamp(local: string, zone: string) {
  const wallTime = Date.parse(local + ':00Z')
  if (!Number.isFinite(wallTime)) return null
  let time = wallTime
  for (let attempt = 0; attempt < 2; attempt++) {
    const offset = Date.parse(tripLocalTime(time, zone) + ':00Z') - time
    time = wallTime - offset
  }
  return tripLocalTime(time, zone) === local ? new Date(time).toISOString() : null
}

export function activityPlace(day: DayPlan, item: TimelineItem, input?: PlanningInput, now = Date.now()) {
  const places = [...day.attractions, ...day.meals]
  const place = places.find(entry => entry.activity_id && entry.activity_id === item.activity_id) ||
    places.find(entry => entry.poi_id && item.evidence_refs?.includes(entry.poi_id))
  const entrance = input?.visit_entrances?.find(entry => entry.day_index === day.day_index && entry.poi_id === place?.poi_id &&
    entry.confirmed && Date.parse(entry.observed_at) <= now && Date.parse(entry.expires_at) > now)
  const commitment = item.activity_id ? input?.commitments.find(entry => entry.status === 'confirmed' &&
    (entry.activity_id === item.activity_id || 'commitment:' + entry.id === item.activity_id)) : undefined
  const hotel = item.kind === 'hotel' ? day.hotel : undefined
  const mapLocation = place && /amap|高德/i.test(place.fact_source || '') ? place.location : undefined
  const confirmedLocation = commitment?.origin_location || commitment?.location
  return {
    address: entrance?.address || place?.address || commitment?.origin_name || hotel?.address,
    entrance,
    location: entrance?.location || confirmedLocation || mapLocation
  }
}

function validLocation(location?: Location): location is Location {
  return !!location && Number.isFinite(location.longitude) && Number.isFinite(location.latitude) &&
    Math.abs(location.longitude) <= 180 && Math.abs(location.latitude) <= 90 &&
    (location.longitude !== 0 || location.latitude !== 0)
}

export function amapLink(location: Location | undefined, title: string, mode?: string) {
  if (!validLocation(location)) return undefined
  const destination = [location.longitude, location.latitude, title.replace(/[,，]/g, ' ')].join(',')
  const routeMode = ({ '自驾': 'car', '公共交通': 'bus', '步行': 'walk' } as Record<string, string>)[mode || '']
  const query = new URLSearchParams({ src: 'trip-planner', callnative: '1' })
  if (routeMode) {
    query.set('to', destination)
    query.set('mode', routeMode)
    return 'https://uri.amap.com/navigation?' + query
  }
  query.set('position', [location.longitude, location.latitude].join(','))
  query.set('name', title)
  query.set('coordinate', 'gaode')
  return 'https://uri.amap.com/marker?' + query
}
