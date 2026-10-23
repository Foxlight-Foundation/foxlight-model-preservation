import {test,expect,type Page} from '@playwright/test';
test.beforeEach(async({page})=>{await page.route('**/api/core-features',route=>route.fulfill({json:{'Portable handoffs':false}}));});

const location={location_id:'device',label:'This device',purpose:'private',reachable:true,free_bytes:1000000,publisher_key_id:'ed25519:'+'7'.repeat(64)};
const model={model_id:'example/alpha',generation_id:'a'.repeat(64),size_bytes:100,verification:'custom'};
const copy={copy_id:'1'.repeat(64),model_id:model.model_id,location_id:'device',manifest_id:'3'.repeat(64),audience:'private',size_bytes:100,checked_at:1,member_count:2};
const publicCopy={...copy,copy_id:'2'.repeat(64),model_id:'community/alpha',location_id:'public-1',manifest_id:'4'.repeat(64),audience:'public',publisher_key_ids:['ed25519:'+'5'.repeat(64)]};
async function library(page:Page){await page.route('**/api/library?offset=*',route=>route.fulfill({json:{locations:[location],default_location_id:'device',models:[model],copies:[copy],public_copies:[publicCopy],jobs:[],uploads:[],publishing:null,bittorrent_available:true,next_offset:null}}));}
const voidColor=(page:Page)=>page.evaluate(()=>getComputedStyle(document.documentElement).getPropertyValue('--void').trim());

test('Night by default and in dark mode, Noon follows a light system, and a manual choice persists',async({page})=>{
  await library(page);
  await page.emulateMedia({colorScheme:'dark'});
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'Your models',exact:true})).toBeVisible();
  expect(await voidColor(page)).toBe('#070a14');
  await page.emulateMedia({colorScheme:'light'});
  expect(await voidColor(page)).toBe('#eef4fb');
  await page.getByRole('button',{name:'Switch to Night theme'}).click();
  await expect(page.locator('html')).toHaveAttribute('data-theme','night');
  expect(await voidColor(page)).toBe('#070a14');
  await page.reload();
  await expect(page.getByRole('button',{name:'Switch to Noon theme'})).toBeVisible();
  expect(await voidColor(page)).toBe('#070a14');
});

test('Escape closes a review sheet like Back: no effect starts and the review is discarded',async({page})=>{
  await library(page);
  let starts=0;
  await page.route('**/api/start',route=>{starts++;return route.fulfill({json:{}});});
  await page.route('**/api/plan',route=>route.fulfill({json:{plan_id:'f'.repeat(64),kind:'preserve',model_id:model.model_id,location_id:'device',size_bytes:100,members:[model.model_id],member_count:1,expires_at:9999999999}}));
  await page.goto('/');
  const preserve=page.getByRole('button',{name:'Preserve',exact:true});
  await preserve.click();
  const sheet=page.getByRole('dialog',{name:'Keep a verified copy'});
  await expect(sheet).toBeVisible();
  await expect(page.locator('main')).toHaveAttribute('inert','');
  await expect(sheet.getByRole('heading',{name:'Keep a verified copy'})).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(sheet).toHaveCount(0);
  await expect(preserve).toBeFocused();
  await page.reload();
  await expect(page.getByRole('heading',{name:'Your models',exact:true})).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(starts).toBe(0);
});

test('the quiet copy menu is keyboard operable and returns focus',async({page})=>{
  await library(page);
  await page.goto('/');
  const trigger=page.getByRole('button',{name:'More actions for the copy on This device'});
  await trigger.focus();
  await page.keyboard.press('Enter');
  const menu=page.getByRole('menu');
  await expect(menu).toBeVisible();
  await expect(trigger).toHaveAttribute('aria-expanded','true');
  await expect(page.getByRole('menuitem',{name:'Save recovery kit'})).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitem',{name:'Publish'})).toBeFocused();
  await page.keyboard.press('ArrowDown');
  await expect(page.getByRole('menuitem',{name:'Save recovery kit'})).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu).toHaveCount(0);
  await expect(trigger).toBeFocused();
  await trigger.click();
  await expect(menu).toBeVisible();
  await page.getByRole('heading',{name:'Your models',exact:true}).click();
  await expect(menu).toHaveCount(0);
});

test('machine values copy exactly and the screen loads only its own bundled assets',async({page,context,baseURL})=>{
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  const requests:string[]=[];page.on('request',request=>requests.push(request.url()));
  await library(page);
  await page.goto('/');
  await page.getByRole('button',{name:'Sharing',exact:true}).click();
  await page.getByText('Bundle identity & verification').click();
  await page.getByRole('button',{name:'Copy manifest ID'}).click();
  await expect(page.getByRole('button',{name:'manifest ID copied'})).toBeVisible();
  expect(await page.evaluate(()=>navigator.clipboard.readText())).toBe(publicCopy.manifest_id);
  expect(await page.evaluate(async()=>{await document.fonts.ready;return document.fonts.check('16px "Instrument Sans"')&&document.fonts.check('12px "JetBrains Mono"');})).toBe(true);
  const origin=new URL(baseURL!).origin;
  expect(requests.filter(url=>!url.startsWith(origin)&&!url.startsWith('data:'))).toEqual([]);
  expect(requests.filter(url=>url.endsWith('.woff2')).map(url=>url.split('/').pop()).sort()).toEqual(['instrument-sans-latin-wght-normal.woff2','jetbrains-mono-latin-wght-normal.woff2']);
});

test('sheets stay within the viewport and the header still navigates away',async({page})=>{
  await library(page);
  await page.goto('/');
  await page.getByRole('button',{name:'Restore from recovery kit'}).click();
  await expect(page.getByRole('dialog',{name:'Restore from a recovery kit'})).toBeVisible();
  await page.waitForFunction(()=>document.querySelector('.sk-sheet')?.getAnimations().every(animation=>animation.playState!=='running'));
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
  const box=await page.getByRole('dialog').boundingBox();
  const viewport=page.viewportSize()!;
  expect(box!.x+box!.width).toBeLessThanOrEqual(viewport.width+1);
  await page.getByRole('button',{name:'Activity',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();
});
