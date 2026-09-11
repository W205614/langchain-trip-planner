import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { pathToFileURL } from 'node:url'

const plan = {city:'导出测试',start_date:'2026-09-14',end_date:'2026-09-17',overall_suggestions:'完整四日行程',
  days:Array.from({length:4},(_,i)=>({day_index:i,date:`2026-09-${14+i}`,description:`第${i+1}天独有内容`,transportation:'自驾',accommodation:'酒店',meals:[],
    attractions:[{poi_id:`P${i}`,name:`第${i+1}天景点`,address:'测试地址',location:{longitude:116.4+i/100,latitude:39.9},visit_duration:180,description:`最后一天也必须出现在下载文件中 ${i+1}`}]}))}

test('PNG and PDF download all days and offline HTML toggles without a network',async({page,context},testInfo)=>{
  test.setTimeout(90000)
  await page.route('**/api/poi/photo/image*',async route=>{
    if(new URL(route.request().url()).searchParams.get('poi_id')==='P3') await new Promise(r=>setTimeout(r,9000))
    await route.fulfill({contentType:'image/svg+xml',body:'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300"><rect width="400" height="300" fill="#abc"/></svg>'}).catch(()=>{})
  })
  await page.addInitScript(plan=>{sessionStorage.setItem('tripPlan',JSON.stringify(plan));sessionStorage.setItem('tripQuality','{}')},plan)
  await page.goto('/result')
  expect(await page.locator('.ant-collapse-item-active').count()).toBe(1)
  for(const [label,extension] of [['导出为图片','png'],['导出为PDF','pdf'],['导出离线网页','html']]){
    await page.getByRole('button',{name:/导出行程/}).hover()
    const downloaded=page.waitForEvent('download',{timeout:45000})
    await page.getByRole('menuitem').filter({hasText:label}).click()
    if(extension!=='html'){
      const clone=page.locator('[data-trip-export]')
      await expect(clone.locator('.ant-collapse-content')).toHaveCount(4)
      await expect(clone.getByText('第4天独有内容',{exact:false})).toBeVisible()
      expect(await clone.locator('img[loading="lazy"]').count()).toBe(0)
    }
    const download=await downloaded
    const path=testInfo.outputPath(`trip.${extension}`)
    await download.saveAs(path)
    expect(await download.failure()).toBeNull()
    const bytes=await readFile(path)
    if(extension==='png'){
      expect(bytes.subarray(1,4).toString()).toBe('PNG')
      expect(bytes.readUInt32BE(20)).toBeGreaterThan(3000)
    }else if(extension==='pdf'){
      expect(bytes.subarray(0,5).toString()).toBe('%PDF-')
      expect(bytes.toString('latin1').match(/\/Type \/Page\b/g)!.length).toBeGreaterThan(1)
    }else{
      const offline=await context.newPage()
      await context.setOffline(true)
      await offline.goto(pathToFileURL(path).href)
      await expect(offline.locator('details')).toHaveCount(4)
      const last=offline.locator('details').nth(3)
      await expect(last).not.toHaveAttribute('open','')
      await last.locator('summary').click()
      await expect(last).toHaveAttribute('open','')
      await expect(last.getByText('第4天独有内容',{exact:false})).toBeVisible()
      await last.locator('summary').click()
      await expect(last).not.toHaveAttribute('open','')
      expect(await offline.locator('img').evaluateAll(images=>images.every(i=>i.src.startsWith('data:')))).toBe(true)
      await context.setOffline(false)
      await offline.close()
    }
    await expect(page.getByRole('button',{name:/导出行程/})).toBeEnabled()
    await expect(page.locator('[data-trip-export]')).toHaveCount(0)
    expect(await page.locator('.ant-collapse-item-active').count()).toBe(1)
  }
})
