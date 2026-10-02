import {test,expect} from '@playwright/test';
test('UI04/07 obsolete retry cannot replace the newly selected profile',async({page})=>{
  const first='20000000-0000-4000-8000-000000000001';
  let calls=0,release!:()=>void,handled!:()=>void;
  const held=new Promise<void>(resolve=>{release=resolve;});
  const finished=new Promise<void>(resolve=>{handled=resolve;});
  await page.route('**/api/profiles/'+first,async route=>{
    calls++;
    if(calls===1){await route.fulfill({status:503,contentType:'application/json',body:'{"error":{"code":"service_unavailable","message":"No disponible"},"request_id":"fixture"}'});return;}
    const real=await route.fetch();
    await held;
    try {await route.fulfill({response:real});} catch { /* Aborting an obsolete fetch is also correct. */ }
    finally {handled();}
  });
  await page.goto('/fixtures/profile-race.html');
  await page.getByRole('button',{name:'Reintentar',exact:true}).click();
  await expect.poll(()=>calls).toBe(2);
  await page.getByRole('button',{name:'Mostrar otro perfil'}).click();
  await expect(page.getByRole('heading',{name:'Básico A',exact:true})).toBeVisible();
  release();await finished;await page.waitForLoadState('networkidle');
  await expect(page.getByRole('heading',{name:'Básico A',exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Promovido A',exact:true})).toHaveCount(0);
});
