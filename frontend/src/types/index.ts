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
  day_temp: number
  night_temp: number
  wind_direction: string
  wind_power: string
}

export interface TripPlan {
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

export interface TripPlanResponse {
  id?: number
  version?: number
  saved?: boolean
  quality?: { outcome?: string; revision_parent?: { record_id: number; version: number }; issues?: { code: string; reason: string; action: string; scope: string; blocking: boolean }[]; warnings?: string[]; data_gaps?: string[]; degraded_days?: number[] }
  success: boolean
  message: string
  data?: TripPlan
}

