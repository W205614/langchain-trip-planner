import { onBeforeUnmount, watch, type Ref } from 'vue'
import AMapLoader from '@amap/amap-jsapi-loader'
import type { TripPlan } from '@/types'

/** Own the SDK lifecycle and redraw whenever edited attractions change. */
export function useTripMap(plan: Ref<TripPlan | null>) {
  let map: any = null
  let sdk: any = null
  let disposed = false
  let pending: Promise<void> | null = null
  const escape = (value: string) => value.replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[char]!))

  function redraw() {
    if (!map || !sdk || !plan.value) return
    map.clearInfoWindow()
    map.clearMap()
    const markers: any[] = []
    plan.value.days.forEach((day, dayIndex) => {
      const path: number[][] = []
      day.attractions.forEach((attraction, index) => {
        const { longitude, latitude } = attraction.location
        if (!Number.isFinite(longitude) || !Number.isFinite(latitude)) return
        const position = [longitude, latitude]
        path.push(position)
        const marker = new sdk.Marker({ position, title: attraction.name,
          label: { content: `${dayIndex + 1}-${index + 1}`, direction: 'top' } })
        const info = new sdk.InfoWindow({ content: `<div><strong>${escape(attraction.name)}</strong><p>${escape(attraction.address)}</p><p>${attraction.visit_duration} 分钟</p><p>${escape(attraction.description)}</p></div>` })
        marker.on('click', () => info.open(map, position))
        markers.push(marker)
      })
      if (path.length > 1) map.add(new sdk.Polyline({ path, strokeColor: '#1890ff', strokeWeight: 4, strokeStyle: 'dashed', showDir: true }))
    })
    map.add(markers)
    if (markers.length) map.setFitView(markers)
  }

  async function initMap() {
    if (disposed) return
    if (map) { redraw(); return }
    if (pending) return pending
    pending = (async () => {
      try {
        sdk = await AMapLoader.load({ key: import.meta.env.VITE_AMAP_WEB_JS_KEY, version: '2.0', plugins: ['AMap.Marker', 'AMap.Polyline', 'AMap.InfoWindow'] })
        if (disposed || !document.getElementById('amap-container')) return
        map = new sdk.Map('amap-container', { zoom: 12, center: [116.397128, 39.916527] })
        redraw()
      } catch {
        const container = document.getElementById('amap-container')
        if (!disposed && container) container.textContent = '地图暂不可用，仍可查看、编辑和导出行程。'
      } finally { pending = null }
    })()
    return pending
  }

  watch(plan, redraw, { deep: true, flush: 'post' })
  onBeforeUnmount(() => { disposed = true; map?.destroy(); map = null })
  return { initMap }
}
