import { ref, type Ref } from 'vue'
import { message } from 'ant-design-vue'
import type { TripPlan } from '@/types'
import { refreshScheduleValidity } from '@/utils/scheduleValidity'
const svgToDataUrl = (svg: string) => `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`
const handleImageError = (event: Event) => { (event.target as HTMLImageElement).src = svgToDataUrl('<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="100%" height="100%" fill="#eee"/></svg>') }

export function useTripExport(tripPlan: Ref<TripPlan | null>, beforeExport: () => Promise<void>) {
const exportBusy = ref(false)
const escapeSvgText = (value: string): string => value
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&apos;')

const buildExportMapImage = (): string => {
  const width = 920
  const height = 460
  const margin = 58
  const points = (tripPlan.value?.days || []).flatMap((day, dayIndex) =>
    day.attractions
      .filter(attraction => Number.isFinite(attraction.location?.longitude) && Number.isFinite(attraction.location?.latitude))
      .map((attraction, attractionIndex) => ({ attraction, dayIndex, attractionIndex }))
  )

  if (!points.length) {
    return svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}"><rect width="100%" height="100%" fill="#f5f7fa"/><text x="50%" y="50%" text-anchor="middle" font-family="sans-serif" font-size="24" fill="#64748b">暂无可导出的景点坐标</text></svg>`)
  }

  const longitudes = points.map(point => point.attraction.location.longitude)
  const latitudes = points.map(point => point.attraction.location.latitude)
  const minLongitude = Math.min(...longitudes)
  const maxLongitude = Math.max(...longitudes)
  const minLatitude = Math.min(...latitudes)
  const maxLatitude = Math.max(...latitudes)
  const longitudeRange = maxLongitude - minLongitude || 0.01
  const latitudeRange = maxLatitude - minLatitude || 0.01
  const project = (point: typeof points[number]) => ({
    x: margin + ((point.attraction.location.longitude - minLongitude) / longitudeRange) * (width - margin * 2),
    y: height - margin - ((point.attraction.location.latitude - minLatitude) / latitudeRange) * (height - margin * 2)
  })
  const projected = points.map(point => ({ ...point, ...project(point) }))
  const colors = ['#2563eb', '#7c3aed', '#db2777', '#0891b2', '#16a34a']
  const routeLines = Array.from({ length: tripPlan.value?.days.length || 0 }, (_, dayIndex) => {
    const dayPoints = projected.filter(point => point.dayIndex === dayIndex)
    if (dayPoints.length < 2) return ''
    return `<polyline points="${dayPoints.map(point => `${point.x.toFixed(1)},${point.y.toFixed(1)}`).join(' ')}" fill="none" stroke="${colors[dayIndex % colors.length]}" stroke-width="5" stroke-linecap="round" stroke-linejoin="round" opacity="0.7"/>`
  }).join('')
  const markers = projected.map((point, index) => {
    const label = escapeSvgText(point.attraction.name.length > 12 ? `${point.attraction.name.slice(0, 12)}…` : point.attraction.name)
    const color = colors[point.dayIndex % colors.length]
    return `<g><circle cx="${point.x.toFixed(1)}" cy="${point.y.toFixed(1)}" r="16" fill="${color}" stroke="#fff" stroke-width="4"/><text x="${point.x.toFixed(1)}" y="${(point.y + 5).toFixed(1)}" text-anchor="middle" font-family="sans-serif" font-size="14" font-weight="700" fill="#fff">${index + 1}</text><text x="${point.x.toFixed(1)}" y="${(point.y - 24).toFixed(1)}" text-anchor="middle" font-family="sans-serif" font-size="15" font-weight="600" fill="#1e293b">${label}</text></g>`
  }).join('')
  const legend = (tripPlan.value?.days || []).map((_, index) => `<tspan fill="${colors[index % colors.length]}">●</tspan><tspan> 第${index + 1}天 </tspan>`).join('')

  return svgToDataUrl(`<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}"><defs><pattern id="grid" width="46" height="46" patternUnits="userSpaceOnUse"><path d="M 46 0 L 0 0 0 46" fill="none" stroke="#dbeafe" stroke-width="1"/></pattern></defs><rect width="100%" height="100%" rx="14" fill="#eff6ff"/><rect x="18" y="18" width="${width - 36}" height="${height - 36}" rx="10" fill="url(#grid)" stroke="#bfdbfe"/><text x="${margin}" y="36" font-family="sans-serif" font-size="16" fill="#475569">行程点位示意图（按 POI 坐标绘制）</text><text x="${width - margin}" y="36" text-anchor="end" font-family="sans-serif" font-size="14" fill="#475569">${legend}</text>${routeLines}${markers}</svg>`)
}

const waitForExportImages = async (container: HTMLElement): Promise<void> => {
  const fallback = svgToDataUrl('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect width="100%" height="100%" fill="#e2e8f0"/><text x="50%" y="50%" text-anchor="middle" font-family="sans-serif" font-size="24" fill="#475569">图片暂不可用，请以文字行程为准</text></svg>')
  await Promise.all(Array.from(container.querySelectorAll('img')).map(image => new Promise<void>(resolve => {
    let done = false
    const finish = (ok: boolean) => {
      if (done) return
      done = true
      clearTimeout(timer)
      image.removeEventListener('load', loaded)
      image.removeEventListener('error', failed)
      if (!ok) {
        image.removeAttribute('srcset')
        image.src = fallback
      }
      resolve()
    }
    const loaded = () => finish(image.naturalWidth > 0)
    const failed = () => finish(false)
    const timer = window.setTimeout(failed, 8000)
    image.addEventListener('load', loaded)
    image.addEventListener('error', failed)
    image.loading = 'eager'
    if (image.complete) finish(image.naturalWidth > 0)
  })))
}

const prepareExportContainer = (element: HTMLElement): HTMLElement => {
  if (tripPlan.value) refreshScheduleValidity(tripPlan.value)
  const exportContainer = document.createElement('div')
  exportContainer.style.width = `${Math.max(1000, element.offsetWidth)}px`
  exportContainer.dataset.tripExport = "true"
  exportContainer.style.backgroundColor = '#f5f7fa'
  exportContainer.style.padding = '20px'
  exportContainer.innerHTML = element.innerHTML
  const status = document.createElement('p')
  status.setAttribute('role', 'note')
  status.style.cssText = 'padding:16px;background:#fff3cd;color:#563f00;border:1px solid #d6bb65;'
  status.textContent = tripPlan.value?.days.some(day => day.schedule_stale) ? '时间轴已过期或依赖条件已变化；以下内容仅供对照，出行前请补充事实并重新规划。' : '此文件为导出时的行程快照。营业、预约与路线有有效期，出行前仍需重新核实。'
  exportContainer.prepend(status)

  // 高德底图的瓦片/canvas 受跨域保护，不能可靠地导出；使用同一 POI 坐标生成静态示意图。
  const exportMapContainer = exportContainer.querySelector('#amap-container')
  if (exportMapContainer) {
    exportMapContainer.innerHTML = `<img src="${buildExportMapImage()}" alt="行程点位示意图" style="width:100%;height:100%;object-fit:cover;display:block;" />`
  }

  exportContainer.querySelectorAll('.ant-card').forEach(card => {
    const cardEl = card as HTMLElement
    cardEl.className = ''
    cardEl.style.setProperty('background-color', '#ffffff')
    cardEl.style.setProperty('border-radius', '12px')
    cardEl.style.setProperty('box-shadow', '0 4px 12px rgba(0, 0, 0, 0.1)')
    cardEl.style.setProperty('margin-bottom', '20px')
    cardEl.style.setProperty('overflow', 'hidden')
  })
  exportContainer.querySelectorAll('.ant-card-head').forEach(head => {
    const headEl = head as HTMLElement
    headEl.style.setProperty('background-color', '#667eea')
    headEl.style.setProperty('color', '#ffffff')
    headEl.style.setProperty('padding', '16px 24px')
    headEl.style.setProperty('font-size', '18px')
    headEl.style.setProperty('font-weight', '600')
  })
  exportContainer.querySelectorAll('.ant-card-body').forEach(body => {
    (body as HTMLElement).style.setProperty('background-color', '#ffffff')
    ;(body as HTMLElement).style.setProperty('padding', '24px')
  })
  exportContainer.querySelectorAll('.hotel-card').forEach(card => {
    const head = card.querySelector('.ant-card-head') as HTMLElement
    if (head) head.style.setProperty('background-color', '#1976d2')
    ;(card as HTMLElement).style.setProperty('background-color', '#e3f2fd')
  })
  exportContainer.querySelectorAll('.weather-card').forEach(card => {
    ;(card as HTMLElement).style.setProperty('background-color', '#e0f7fa')
  })
  const budgetTotal = exportContainer.querySelector('.budget-total') as HTMLElement | null
  if (budgetTotal) {
    budgetTotal.style.setProperty('background-color', '#667eea')
    budgetTotal.style.setProperty('color', '#ffffff')
    budgetTotal.style.setProperty('padding', '20px')
    budgetTotal.style.setProperty('border-radius', '12px')
    budgetTotal.style.setProperty('margin-bottom', '20px')
  }
  exportContainer.querySelectorAll('.budget-item').forEach(item => {
    const itemEl = item as HTMLElement
    itemEl.style.setProperty('background-color', '#f5f7fa')
    itemEl.style.setProperty('padding', '16px')
    itemEl.style.setProperty('border-radius', '8px')
    itemEl.style.setProperty('margin-bottom', '12px')
  })
  exportContainer.querySelectorAll('.ant-collapse-content').forEach(content => {
    const panel = content as HTMLElement
    panel.classList.remove('ant-collapse-content-hidden')
    panel.style.display = 'block'
    panel.style.height = 'auto'
    panel.style.visibility = 'visible'
  })
  exportContainer.querySelectorAll('.timeline button.title').forEach(control => { const title = document.createElement('strong'); title.className = control.className; title.textContent = control.textContent; control.replaceWith(title) })
  exportContainer.querySelectorAll('.timeline details').forEach(details => details.setAttribute('open', ''))
  exportContainer.querySelectorAll('button, .ant-collapse-expand-icon').forEach(control => control.remove())
  exportContainer.querySelectorAll('img').forEach(image => {
    image.loading = 'eager'
    image.decoding = 'sync'
    image.setAttribute('crossorigin', 'anonymous')
    // cloneNode/innerHTML 不会复制 Vue 绑定的 @error 监听器；导出副本也要替换失败图片，
    // 否则单张上游图片异常仍会留下空白区域。
    image.addEventListener('error', handleImageError, { once: true })
  })
  exportContainer.style.position = 'absolute'
  exportContainer.style.left = '-100000px'
  exportContainer.style.top = '0'
  return exportContainer
}

const createExportCanvas = async (): Promise<HTMLCanvasElement> => {
  const element = document.querySelector('.main-content') as HTMLElement | null
  if (!element) throw new Error('未找到内容元素')
  const exportContainer = prepareExportContainer(element)
  document.body.appendChild(exportContainer)
  try {
    await waitForExportImages(exportContainer)
    const width = exportContainer.offsetWidth
    const height = exportContainer.scrollHeight
    // Bound both browser canvas dimensions and memory for multi-week itineraries.
    const scale = Math.min(2, 16000 / width, 16000 / height, Math.sqrt(24000000 / (width * height)))
    if (!Number.isFinite(scale) || scale <= 0) throw new Error('行程尺寸无效，请刷新后重试')
    const { default: html2canvas } = await import('html2canvas')
    return await html2canvas(exportContainer, {
      backgroundColor: '#f5f7fa',
      scale,
      logging: false,
      useCORS: true,
      allowTaint: false,
      imageTimeout: 15000
    })
  } finally {
    document.body.removeChild(exportContainer)
  }
}

// Native details/summary provides portable offline interaction without executable scripts.
const exportAsHTML = async () => {
  if (exportBusy.value || !tripPlan.value) return
  exportBusy.value = true
  let container: HTMLElement | null = null
  try {
    await beforeExport()
    message.loading({ content: '正在打包离线网页...', key: 'export', duration: 0 })
    const source = document.querySelector('.main-content') as HTMLElement
    container = prepareExportContainer(source)
    document.body.appendChild(container)
    const images = Array.from(container.querySelectorAll('img'))
    await Promise.all(images.map(async image => {
      if (image.src.startsWith('data:')) return
      try {
        const response = await fetch(image.src, { signal: AbortSignal.timeout(8000) })
        if (!response.ok) throw new Error('图片下载失败')
        const blob = await response.blob()
        image.src = await new Promise<string>((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => resolve(String(reader.result))
          reader.onerror = reject
          reader.readAsDataURL(blob)
        })
      } catch {
        image.src = svgToDataUrl('<svg xmlns="http://www.w3.org/2000/svg" width="800" height="500"><rect width="100%" height="100%" fill="#e2e8f0"/><text x="50%" y="50%" text-anchor="middle" font-family="sans-serif" font-size="24">图片暂不可用，请以文字行程为准</text></svg>')
      }
      image.removeAttribute('srcset')
      image.removeAttribute('crossorigin')
    }))
    container.querySelectorAll('.ant-collapse-item').forEach((panel, index) => {
      const details = document.createElement('details')
      details.className = 'offline-day'
      details.open = index === 0
      const summary = document.createElement('summary')
      summary.textContent = panel.querySelector('.ant-collapse-header')?.textContent?.trim() || `第${index + 1}天`
      details.appendChild(summary)
      const content = panel.querySelector('.ant-collapse-content')
      if (content) details.appendChild(content)
      panel.replaceWith(details)
    })
    const css = Array.from(document.styleSheets).map(sheet => {
      try { return Array.from(sheet.cssRules).map(rule => rule.cssText).join('\n') } catch { return '' }
    }).join('\n')
    const offline = document.implementation.createHTMLDocument(`${tripPlan.value.city}旅行计划`)
    offline.documentElement.lang = 'zh-CN'
    const charset = offline.createElement('meta')
    charset.setAttribute('charset', 'utf-8')
    offline.head.prepend(charset)
    const policy = offline.createElement('meta')
    policy.httpEquiv = 'Content-Security-Policy'
    policy.content = "default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'"
    offline.head.appendChild(policy)
    const styles = offline.createElement('style')
    styles.textContent = css + '\nbody{margin:0;background:#f5f7fa;font-family:Arial,sans-serif} .offline-day{margin:16px 0;border:1px solid #dbe2ed;border-radius:10px;overflow:hidden} .offline-day>summary{cursor:pointer;padding:18px;background:#667eea;color:white;font-size:18px;font-weight:bold} .offline-day .ant-collapse-content{display:block!important} .offline-note{padding:16px;background:#eef2ff;color:#334155}'
    offline.head.appendChild(styles)
    const note = offline.createElement('p')
    note.className = 'offline-note'
    note.textContent = '离线旅行计划：点击每天的标题可展开或收起。图片和正文已打包，无需登录；PNG/PDF 为完整静态版本。'
    offline.body.appendChild(note)
    const copy = container.cloneNode(true) as HTMLElement
    copy.removeAttribute('data-trip-export')
    copy.style.position = 'static'
    copy.style.left = ''
    copy.style.margin = '0 auto'
    copy.style.maxWidth = '100%'
    copy.style.boxSizing = 'border-box'
    offline.body.appendChild(copy)
    const blob = new Blob(['<!DOCTYPE html>\n', offline.documentElement.outerHTML], { type: 'text/html;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value.city}_可交互.html`
    link.href = url
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 60000)
    message.success({ content: '离线网页已导出，用浏览器打开即可展开/收起每天行程', key: 'export' })
  } catch (error: any) {
    message.error({ content: `离线网页导出失败: ${error.message}`, key: 'export' })
  } finally {
    container?.remove()
    exportBusy.value = false
  }
}

// 导出为图片
const exportAsImage = async () => {
  if (exportBusy.value) return
  exportBusy.value = true
  try {
    await beforeExport()
    message.loading({ content: '正在生成图片...', key: 'export', duration: 0 })
    const canvas = await createExportCanvas()
    const link = document.createElement('a')
    link.download = `旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.png`
    const blob = await new Promise<Blob>((resolve, reject) => canvas.toBlob(value =>
      value ? resolve(value) : reject(new Error('图片编码失败')), 'image/png'))
    const url = URL.createObjectURL(blob)
    link.href = url
    document.body.appendChild(link)
    link.click()
    link.remove()
    window.setTimeout(() => URL.revokeObjectURL(url), 60000)
    message.success({ content: '图片导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出图片失败:', error)
    message.error({ content: `导出图片失败: ${error.message}`, key: 'export' })
  } finally {
    exportBusy.value = false
  }
}

// 导出为PDF
const exportAsPDF = async () => {
  if (exportBusy.value) return
  exportBusy.value = true
  try {
    await beforeExport()
    message.loading({ content: '正在生成PDF...', key: 'export', duration: 0 })
    const canvas = await createExportCanvas()
    const { default: jsPDF } = await import('jspdf')
    const pdf = new jsPDF({
      orientation: 'portrait',
      unit: 'mm',
      format: 'a4'
    })

    const margin = 8
    const imgWidth = 210 - margin * 2
    const pixelsPerPage = Math.floor((297 - margin * 2) * canvas.width / imgWidth)
    const context = canvas.getContext('2d')!
    let top = 0
    let pageIndex = 0
    while (top < canvas.height) {
      let bottom = Math.min(top + pixelsPerPage, canvas.height)
      // Prefer a nearby blank row so pagination does not cut text in half.
      if (bottom < canvas.height) {
        const scanHeight = Math.min(120, bottom - top)
        const scanTop = bottom - scanHeight
        const pixels = context.getImageData(0, scanTop, canvas.width, scanHeight).data
        for (let row = scanHeight - 1; row >= 0; row--) {
          let ink = 0
          for (let x = 0; x < canvas.width; x += 4) {
            const offset = (row * canvas.width + x) * 4
            if (pixels[offset + 3] > 0 && Math.min(pixels[offset], pixels[offset + 1], pixels[offset + 2]) < 230) ink++
          }
          if (ink < canvas.width / 800) { bottom = scanTop + row; break }
        }
      }
      const pageCanvas = document.createElement('canvas')
      pageCanvas.width = canvas.width
      pageCanvas.height = bottom - top
      pageCanvas.getContext('2d')!.drawImage(canvas, 0, top, canvas.width, bottom - top, 0, 0, canvas.width, bottom - top)
      if (pageIndex++) pdf.addPage()
      pdf.addImage(pageCanvas.toDataURL('image/jpeg', 0.92), 'JPEG', margin, margin, imgWidth, pageCanvas.height * imgWidth / canvas.width)
      pageCanvas.width = pageCanvas.height = 0
      top = bottom
    }

    pdf.save(`旅行计划_${tripPlan.value?.city}_${new Date().getTime()}.pdf`)

    message.success({ content: 'PDF导出成功!', key: 'export' })
  } catch (error: any) {
    console.error('导出PDF失败:', error)
    message.error({ content: `导出PDF失败: ${error.message}`, key: 'export' })
  } finally {
    exportBusy.value = false
  }
}


return { exportBusy, exportAsHTML, exportAsImage, exportAsPDF }
}
