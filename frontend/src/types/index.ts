// 类型定义

export interface Location {
  longitude: number
  latitude: number
}

export interface POIInfo {
  id: string
  name: string
  type: string
  address: string
  location: Location
  photos: string[]
  opening_hours: string
}

export interface Attraction {
  activity_id?: string
  poi_id?: string
  opening_hours?: string
  fact_source?: string
  price_source?: string
  requested_names?: string[]
  name: string
  address: string
  location: Location
  visit_duration: number
  description: string
  category?: string
  rating?: number
  image_url?: string
  ticket_price?: number
}

export interface Meal {
  activity_id?: string
  type: 'breakfast' | 'lunch' | 'dinner' | 'snack'
  name: string
  address?: string
  location?: Location
  description?: string
  estimated_cost?: number
  poi_id?: string
  opening_hours?: string
  fact_source?: string
}

export interface Hotel {
  activity_id?: string
  name: string
  address: string
  location?: Location
  price_range: string
  rating: string
  distance: string
  type: string
  estimated_cost?: number
}

export interface Budget {
  assumptions?: string[]
  estimated?: boolean
  unknown_items?: string[]
  total_attractions: number
  total_hotels: number
  total_meals: number
  total_transportation: number
  total: number
  limit_total?: number | null
  within_limit?: boolean | null
}

export interface DayPlan {
  schedule_stale?: boolean
  route_fact_expires_at?: string | null
  schedule_dependencies?: { planning_revision: number; input_hash: string; fact_hash: string; computed_at: string; expires_at: string }
  city?: string
  timeline?: TimelineItem[]
  feasibility?: { status: 'feasible' | 'unverified' | 'not_found' | 'infeasible'; solver_status?: string; unknown?: string[]; conflicts?: string[]; omitted_activity_ids?: string[] }
  date: string
  day_index: number
  description: string
  theme?: string
  activities?: string[]
  transportation: string
  accommodation: string
  hotel?: Hotel
  attractions: Attraction[]
  meals: Meal[]
}

export interface WeatherInfo {
  date: string
  day_weather: string
  night_weather: string
  day_temp: number | null
  night_temp: number | null
  wind_direction: string
  wind_power: string
}

export interface TripPlan {
  planning_input?: PlanningInput
  enrichment_notices?: string[]
  city: string
  start_date: string
  end_date: string
  days: DayPlan[]
  weather_info: WeatherInfo[]
  weather_notice?: string
  overall_suggestions: string
  budget?: Budget
}

export interface TripFormData {
  planning_input?: PlanningInput
  constraints?: {
    must_visit: string[]
    avoid: string[]
    daily_minutes: number
    max_inter_stop_walking_km: number | null
  }
  city: string
  departure_city: string
  start_date: string
  end_date: string
  travel_days: number
  transportation: string
  accommodation: string
  traveler_count: number
  room_count: number
  budget_total: number | null
  preferences: string[]
  free_text_input: string
}

export interface RevisionScope {
  day_indices: number[]
  activity_ids: string[]
  start_at?: string | null
  end_at?: string | null
}

export interface PlanningCommitment {
  time_mode?: 'fixed' | 'entry_window'
  duration_minutes?: number
  id: string | number
  version: number
  activity_id?: string
  day_index: number
  category: string
  status: string
  title: string
  poi_id?: string
  location?: Location | null
  origin_location?: Location | null
  destination_location?: Location | null
  origin_name?: string
  destination_name?: string
  origin_city?: string
  destination_city?: string
  start_at?: string | null
  end_at?: string | null
  amount_cents?: number | null
}

export interface PlanningInput {
  timezone: string
  day_start: string
  day_end: string
  day_windows: { day_index: number; start: string; end: string }[]
  city_segments: { city: string; start_day: number; end_day: number }[]
  commitments: PlanningCommitment[]
  hotel_stays: { day_index: number; name: string; poi_id?: string; location?: Location | null }[]
  meal_windows: { type: string; start: string; end: string; duration_minutes: number }[]
  opening_windows: { poi_id: string; date: string; windows: { start: string; end: string }[]; last_entry?: string | null; closed: boolean; source: string; observed_at?: string; expires_at?: string; confirmed: boolean }[]
  reservation_facts?: (TravelFact & { poi_id: string; date: string; status: 'confirmed' | 'not_required' | 'unavailable' })[]
  transport_buffers?: (TravelFact & { commitment_id: string; minutes: number })[]
  poi_relations?: (TravelFact & { parent_poi_id: string; child_poi_id: string; relation: 'included' | 'separate' })[]
  meal_resolutions?: (TravelFact & { day_index: number; type: string; mode: 'self_arranged'; location: Location })[]
  locks: { activity_id: string; mode: 'content' | 'time'; start_at?: string | null; end_at?: string | null }[]
  completed_activity_ids: string[]
  arrival_at?: string | null
  departure_at?: string | null
  budget_mode: 'estimate' | 'confirmed'
  budget_cents?: number | null
  revision_scope?: RevisionScope
}

export interface TravelFact {
  source: string
  observed_at: string
  expires_at: string
  confirmed: boolean
  authority?: 'user_declared'
}

export interface TimelineItem {
  activity_id?: string
  title: string
  kind: string
  start_at?: string | null
  end_at?: string | null
  status: 'confirmed' | 'estimated' | 'unverified'
  source?: string
  evidence_refs?: (string | { source?: string; id?: string })[]
  notes?: string[]
  location?: Location
}

export interface TripPlanResponse {
  id?: number
  version?: number
  saved?: boolean
  quality?: { outcome?: string; revision_parent?: { record_id: number; version: number }; issues?: { code: string; reason: string; action: string; scope: string; blocking: boolean }[]; warnings?: string[]; data_gaps?: string[]; degraded_days?: number[] }
  success: boolean
  message: string
  data?: TripPlan
}

