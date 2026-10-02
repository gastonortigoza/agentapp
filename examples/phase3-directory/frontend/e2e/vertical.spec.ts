import {test,expect} from '@playwright/test';
test.beforeEach(async({page})=>{
  page.on('pageerror',error=>console.log('PAGE_ERROR='+error.message));
  page.on('console',message=>{if(message.type()==='error')console.log('CONSOLE_ERROR='+message.text());});
  page.on('response',async response=>{if(response.status()>=400)console.log('HTTP_ERROR='+response.status()+' '+response.url()+' '+(await response.text()).slice(0,1500));});
});
const c='00000000-0000-4000-8000-000000000001',a='00000000-0000-4000-8000-000000000011',b='00000000-0000-4000-8000-000000000012';
test('UI01/02 real browser → Vite → Fastify → PostgreSQL; both plans filtered',async({page})=>{
  await page.goto('/');await expect(page.getByRole('link',{name:'Registrarse'})).toBeVisible();
  const promoted=page.getByRole('region',{name:'Promocionados'}),basic=page.getByRole('region',{name:'Listado básico'});
  await expect(promoted.getByRole('listitem')).toHaveCount(2);await expect(basic.getByRole('listitem')).toHaveCount(2);
  await expect.poll(()=>page.getByRole('img').evaluateAll(images=>images.filter(img=>(img as HTMLImageElement).naturalWidth>0).length)).toBe(4);
  console.log('DIRECTORY_SCREENSHOT_JPEG='+Buffer.from(await page.screenshot({type:'jpeg',quality:35,fullPage:true})).toString('base64'));
  await page.getByLabel('País',{exact:true}).selectOption(c);await page.getByLabel('Provincia',{exact:true}).selectOption(a);
  await expect(promoted.getByRole('listitem')).toHaveCount(1);await expect(basic.getByRole('listitem')).toHaveCount(1);
  await expect(promoted).toContainText('Promovido A');await expect(basic).toContainText('Básico A');
  await page.getByLabel('Zona',{exact:true}).selectOption('00000000-0000-4000-8000-000000000021');
  await page.getByLabel('Provincia',{exact:true}).selectOption(b);await expect(page.getByLabel('Zona',{exact:true})).toHaveValue('');
  await expect(promoted).toContainText('Promovido B');await expect(basic).toContainText('Básico B');
  await page.getByLabel('País',{exact:true}).selectOption('');await expect(page.getByLabel('Provincia',{exact:true})).toHaveValue('');
});
test('UI03/05 empty, retry keeps filters and description is escaped',async({page})=>{
  await page.goto('/');await expect(page.getByText('<script>window.__injected=true</script>',{exact:true})).toBeVisible();
  expect(await page.evaluate(()=>('__injected' in window))).toBe(false);
  await page.getByLabel('País',{exact:true}).selectOption(c);await page.getByLabel('Provincia',{exact:true}).selectOption(a);
  let fail=true;await page.route('**/api/profiles?**',async route=>{if(fail){fail=false;await route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'service_unavailable',message:'No disponible'}})});}else await route.continue();});
  await page.getByLabel('Zona',{exact:true}).selectOption('00000000-0000-4000-8000-000000000021');
  await expect(page.getByRole('button',{name:/Reintentar/})).toBeVisible();await page.getByRole('button',{name:/Reintentar/}).click();
  await expect(page.getByLabel('Provincia',{exact:true})).toHaveValue(a);await expect(page.getByRole('region',{name:'Listado básico'})).toContainText('Básico A');
  await page.route('**/api/profiles?**',route=>route.fulfill({status:200,contentType:'application/json',body:'{"items":[],"next_cursor":null}'}));
  await page.getByLabel('Zona',{exact:true}).selectOption('');await expect(page.getByText(/No se encontraron|Sin coincidencias|No hay coincidencias/i)).toBeVisible();
});
for(const width of [360,1280])test('UI10 no horizontal overflow at '+width,async({page})=>{
  await page.setViewportSize({width,height:900});await page.goto('/');await expect(page.getByRole('region',{name:'Listado básico'})).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});
