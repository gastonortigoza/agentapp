// Frozen product checks. NEVER executed against FixturePages/mock logout.
import {test,expect} from '@playwright/test';import {randomUUID} from 'node:crypto';
const password='Synthetic-Test-Password-1234';
async function fields(page:any,email:string,birth?:string){
 await page.getByLabel(/correo|email/i).fill(email);await page.getByLabel(/contraseña|password/i).fill(password);
 if(birth)await page.getByLabel(/nacimiento|birth/i).fill(birth);
}
test('public root remains available and auth navigation uses labelled controls',async({page})=>{
 await page.goto('/');await expect(page.getByRole('navigation',{name:'Navegación principal'}).getByRole('link',{name:'Registrarse',exact:true})).toBeVisible();
 await expect(page).not.toHaveURL(/\/ingresar/);
});
test('register rejects minor then accepts adult; real session stays in memory',async({page,context,baseURL})=>{
 await page.goto('/registro');const email='browser-'+randomUUID()+'@example.invalid';await fields(page,email,'2015-01-01');
 let response=page.waitForResponse(r=>r.url().endsWith('/api/auth/register'));
 await page.getByRole('button',{name:/registrar|crear.*cuenta|register/i}).click();expect((await response).status()).toBe(422);await expect(page.getByRole('alert').first()).toBeVisible();
 await page.getByLabel(/nacimiento|birth/i).fill('2000-01-01');response=page.waitForResponse(r=>r.url().endsWith('/api/auth/register'));
 await page.getByRole('button',{name:/registrar|crear.*cuenta|register/i}).click();expect((await response).status()).toBe(201);
 await expect(page.getByText('Sesión iniciada',{exact:true})).toBeVisible();await expect(page).toHaveURL(/\/mi-perfil$/);
 const refresh=(await context.cookies(baseURL+'/api/auth')).find(c=>c.name==='refresh');expect(Boolean(refresh)).toBe(true);
 expect(refresh?.secure).toBe(true);expect(refresh?.httpOnly).toBe(true);expect(refresh?.sameSite).toBe('Lax');expect(refresh?.path).toBe('/api/auth');
 expect(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length}))).toEqual({local:0,session:0});
});
test('unknown login displays generic401 and retry remains usable',async({page})=>{
 await page.goto('/ingresar');await fields(page,'absent-'+randomUUID()+'@example.invalid');
 const response=page.waitForResponse(r=>r.url().endsWith('/api/auth/login'));
 await page.getByRole('button',{name:/ingresar|iniciar.*sesión|login/i}).click();expect((await response).status()).toBe(401);
 await expect(page.getByRole('alert').first()).toBeVisible();await expect(page.getByRole('button',{name:/ingresar|iniciar.*sesión|login/i})).toBeEnabled();
 await expect(page.getByText('Sesión iniciada',{exact:true})).toHaveCount(0);
});
test('real logout204 clears in-memory session and expired refresh cookie',async({page,context,baseURL})=>{
 await page.goto('/registro');await fields(page,'logout-'+randomUUID()+'@example.invalid','2000-01-01');
 await page.getByRole('button',{name:/registrar|crear.*cuenta|register/i}).click();await expect(page.getByText('Sesión iniciada',{exact:true})).toBeVisible();
 const response=page.waitForResponse(r=>r.url().endsWith('/api/auth/logout'));await page.getByRole('button',{name:'Cerrar sesión',exact:true}).click();
 expect((await response).status()).toBe(204);await expect(page).toHaveURL(/\/ingresar$/);await expect(page.getByText('Sesión iniciada',{exact:true})).toHaveCount(0);
 expect((await context.cookies(baseURL+'/api/auth')).filter(c=>c.name==='refresh')).toHaveLength(0);
});
test('UI10 real auth forms fit360/1280 and support keyboard input',async({page})=>{
 for(const width of [360,1280]){
  await page.setViewportSize({width,height:800});await page.goto('/registro');await fields(page,'viewport-'+randomUUID()+'@example.invalid','2000-01-01');
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>window.innerWidth)).toBe(false);
  await page.getByLabel(/correo|email/i).focus();await page.keyboard.press('Tab');await expect(page.getByLabel(/contraseña|password/i)).toBeFocused();
 }
});
