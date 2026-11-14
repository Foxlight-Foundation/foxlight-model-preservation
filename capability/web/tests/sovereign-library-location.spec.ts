import {test,expect} from '@playwright/test';

const home={path:null,system:true,available:true,private:true,free_bytes:40e9,previous:[],elsewhere_copies:0,elsewhere_bytes:0,move:null};
const drive='/Volumes/Models/Sovereign Library';

async function common(page:import('@playwright/test').Page){
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:'c'.repeat(64),results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[],total:0,stored_total:0,next_offset:null}}));
}

test('model copies can be kept on another drive, and older copies moved there',async({page})=>{
  let library:Record<string,unknown>=home;const calls:{path:string;body:unknown}[]=[];
  await common(page);
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',library,copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/desktop-library-*',async r=>{
    const path=new URL(r.request().url()).pathname.split('/').at(-1)!;const body=r.request().postDataJSON();calls.push({path,body});
    if(path==='desktop-library-folder-choose')return r.fulfill({json:{path:drive}});
    if(path==='desktop-library-review')return r.fulfill({json:{review_id:'r'.repeat(64),path:drive,exists:false,reattach:false,same_drive:false,free_bytes:18e12,expires_at:Math.floor(Date.now()/1000)+600}});
    if(path==='desktop-library-setup'){library={...home,path:drive,system:false,free_bytes:18e12,elsewhere_copies:2,elsewhere_bytes:30e9};return r.fulfill({json:library});}
    if(path==='desktop-library-move'){library={...library,move:{state:'moving',items_done:0,items_total:2,bytes_done:0,bytes_total:30e9,error:null}};return r.fulfill({json:library});}
    return r.fulfill({json:library});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'Library'}).first().click();
  const card=page.getByRole('region',{name:'Where model copies are kept'});
  await expect(card.getByText('This computer’s drive')).toBeVisible();
  await card.getByRole('button',{name:'Change location'}).click();
  await page.getByRole('button',{name:'Choose folder'}).click();
  await expect(page.getByLabel('Folder path')).toHaveValue(drive);
  await page.getByRole('button',{name:'Review folder'}).click();
  await expect(page.getByText('16.4 TB',{exact:false})).toBeVisible();
  const keep=page.getByRole('button',{name:'Keep new copies here'});
  await expect(keep).toBeDisabled();
  await page.getByText('Keep new model copies in this folder.').click();
  await keep.click();
  await expect.poll(()=>calls.find(call=>call.path==='desktop-library-setup')?.body).toEqual({review_id:'r'.repeat(64),confirmed:true});
  await expect(card.getByText(drive)).toBeVisible();
  await expect(card.getByText('2 copies are',{exact:false})).toBeVisible();
  await card.getByRole('button',{name:'Move them to this location'}).click();
  await expect(card.getByRole('status')).toContainText('0 of 2');
  await expect(card.getByRole('button',{name:'Stop moving'})).toBeVisible();
});

test('an unplugged library drive is reported, never replaced',async({page})=>{
  await common(page);
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',library:{...home,path:drive,system:false,available:false,free_bytes:null},copies:[],jobs:[],next_offset:null}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Library'}).first().click();
  await expect(page.getByRole('alert')).toContainText('isn’t connected');
  await expect(page.getByRole('alert')).toContainText(drive);
});

test('shared copies and the pool left behind can still be moved',async({page})=>{
  await common(page);
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',library:{...home,path:drive,system:false,elsewhere_copies:0,elsewhere_other:2},copies:[],jobs:[],next_offset:null}}));
  await page.goto('/');
  await page.getByRole('button',{name:'Library'}).first().click();
  const card=page.getByRole('region',{name:'Where model copies are kept'});
  await expect(card.getByText('Shared copies and the pool Sovereign keeps for others',{exact:false})).toBeVisible();
  await expect(card.getByRole('button',{name:'Move them to this location'})).toBeVisible();
});
