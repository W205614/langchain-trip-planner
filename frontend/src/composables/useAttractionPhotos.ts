import { ref, type Ref } from 'vue'
import type { Attraction, TripPlan } from '@/types'

const photoKey = (attraction: Attraction, city: string) =>
  `${city}:${attraction.poi_id || attraction.name}`

export function useAttractionPhotos(tripPlan: Ref<TripPlan | null>) {
  const photoUrls = ref<Record<string, string>>({})

  function loadAttractionPhotos() {
    const plan = tripPlan.value
    if (!plan) return

    for (const day of plan.days) {
      const city = day.city || plan.city
      for (const attraction of day.attractions) {
        // Keep the proxy version in the URL to bypass stale placeholders.
        photoUrls.value[photoKey(attraction, city)] = `/api/poi/photo/image?name=${encodeURIComponent(attraction.name)}&poi_id=${encodeURIComponent(attraction.poi_id || '')}&city=${encodeURIComponent(city)}&v=java-photo-v3`
      }
    }
  }

  function getAttractionImage(attraction: Attraction, index: number, city: string): string {
    const url = photoUrls.value[photoKey(attraction, city)]
    if (url) return url

    const colors = [
      { start: '#667eea', end: '#764ba2' },
      { start: '#f093fb', end: '#f5576c' },
      { start: '#4facfe', end: '#00f2fe' },
      { start: '#43e97b', end: '#38f9d7' },
      { start: '#fa709a', end: '#fee140' }
    ]
    const colorIndex = index % colors.length
    const { start, end } = colors[colorIndex]

    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300">
      <defs>
        <linearGradient id="grad${index}" x1="0%" y1="0%" x2="100%" y2="100%">
          <stop offset="0%" style="stop-color:${start};stop-opacity:1" />
          <stop offset="100%" style="stop-color:${end};stop-opacity:1" />
        </linearGradient>
      </defs>
      <rect width="400" height="300" fill="url(#grad${index})"/>
      <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="24" font-weight="bold" fill="white">景点图片</text>
    </svg>`

    return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
  }

  const handleImageError = (event: Event) => {
    const img = event.target as HTMLImageElement
    img.src = 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" width="400" height="300"%3E%3Crect width="400" height="300" fill="%23f0f0f0"/%3E%3Ctext x="50%25" y="50%25" dominant-baseline="middle" text-anchor="middle" font-family="sans-serif" font-size="18" fill="%23999"%3E图片加载失败%3C/text%3E%3C/svg%3E'
  }

  return { loadAttractionPhotos, getAttractionImage, handleImageError }
}
