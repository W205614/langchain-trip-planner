import apiClient from './api'
import type { POIInfo } from '@/types'

export async function searchAttractionPOIs(city: string, keywords: string): Promise<POIInfo[]> {
  const response = await apiClient.get<{ success: boolean; data: POIInfo[] }>('/api/map/poi', {
    params: { city, keywords, citylimit: true }
  })
  if (!response.data.success) throw new Error('景点搜索失败')
  return response.data.data.filter(poi => poi.id && poi.location
    && Number.isFinite(poi.location.longitude) && Number.isFinite(poi.location.latitude))
}
