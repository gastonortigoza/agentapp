import {test,expect} from '@playwright/test';
const id='20000000-0000-4000-8000-000000000001';
test('UI04/05 card opens real detail, ordered gallery and exact WhatsApp link',async({page})=>{
  await page.goto('/');await page.getByRole('link',{name:'Promovido A',exact:true}).click();
  await expect(page).toHaveURL('/personas/'+id);
  await expect(page.getByRole('heading',{name:'Promovido A',exact:true})).toBeVisible();
  await expect(page.getByText('Zona sintética A',{exact:false})).toBeVisible();
  await expect(page.getByText('<script>window.__injected=true</script>',{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>('__injected' in window))).toBe(false);
  const images=page.getByRole('main').getByRole('img');await expect(images).toHaveCount(3);
  await expect.poll(()=>images.evaluateAll(xs=>xs.every(x=>(x as HTMLImageElement).naturalWidth>0))).toBe(true);
  await expect(images.first()).toHaveAttribute('alt',/principal/i);
  await expect(page.getByRole('link',{name:/WhatsApp/i})).toHaveAttribute('href','https://wa.me/5491100000000');
  await expect(page.getByRole('link',{name:/WhatsApp/i})).toHaveAttribute('rel',/noopener/);
  console.log('PROFILE_SCREENSHOT_JPEG='+Buffer.from(await page.screenshot({type:'jpeg',quality:35,fullPage:true})).toString('base64'));
  await page.getByRole('link',{name:'Volver al directorio'}).click();await expect(page.getByRole('region',{name:'Promocionados'})).toBeVisible();
});
test('UI04 direct expired/missing detail404; transient error retries the same ID',async({page})=>{
  for(const hidden of ['20000000-0000-4000-8000-000000000005','20000000-0000-4000-8000-000000000999']){
    await page.goto('/personas/'+hidden);await expect(page.getByText('Perfil no disponible',{exact:false})).toBeVisible();await expect(page.getByRole('img')).toHaveCount(0);await expect(page.getByRole('link',{name:/WhatsApp/i})).toHaveCount(0);
  }
  let calls=0;await page.route('**/api/profiles/'+id,async route=>{calls++;if(calls===1)await route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"service_unavailable","message":"No disponible"},"request_id":"fixture"}'});else await route.continue();});
  await page.goto('/personas/'+id);await expect(page.getByRole('button',{name:'Reintentar'})).toBeVisible();await page.getByRole('button',{name:'Reintentar'}).click();await expect(page.getByRole('heading',{name:'Promovido A'})).toBeVisible();expect(calls).toBe(2);
});
for(const width of [360,1280])test('UI10 gallery accessible without overflow at '+width,async({page})=>{
  await page.setViewportSize({width,height:900});await page.goto('/personas/'+id);await expect(page.getByRole('img')).toHaveCount(3);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  for(const image of await page.getByRole('img').all())await expect(image).toHaveAttribute('alt',/\S/);
});
