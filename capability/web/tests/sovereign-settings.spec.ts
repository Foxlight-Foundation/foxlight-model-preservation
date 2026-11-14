import {test,expect} from '@playwright/test';

test('settings gather search sources, privacy, storage and appearance',async({page})=>{
  const state={hub:false,pf:true,swarm:false};const calls:{path:string;body:unknown}[]=[];
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',hub_search:state.hub,pirate_face_search:state.pf,pirate_face_swarm:state.swarm,library:{path:null,system:true,available:true,private:true,free_bytes:40e9,previous:[],elsewhere_copies:0,elsewhere_bytes:0,move:null},copies:[],jobs:[],next_offset:null}}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-backup-read',r=>r.fulfill({json:{destinations:[],next_offset:null}}));
  for(const [path,key] of [['desktop-hub-consent','hub'],['desktop-pirate-face-consent','pf'],['desktop-pirate-face-swarm','swarm']] as const)
    await page.route('**/api/'+path,r=>{const body=r.request().postDataJSON();calls.push({path,body});state[key]=body.enabled;return r.fulfill({json:{}});});
  await page.goto('/');
  await page.getByRole('button',{name:'Settings',exact:true}).first().click();
  for(const heading of ['Storage','Search sources and privacy','Sharing','Appearance'])
    await expect(page.getByRole('heading',{name:heading,exact:true})).toBeVisible();
  await expect(page.getByRole('region',{name:'Where model copies are kept'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'Backup folders'})).toBeVisible();
  const hub=page.getByRole('article',{name:'Search Hugging Face'});
  await expect(hub.getByText('Off')).toBeVisible();
  await hub.getByRole('button',{name:'Search Hugging Face too'}).click();
  await expect.poll(()=>calls.at(-1)).toEqual({path:'desktop-hub-consent',body:{enabled:true}});
  await expect(hub.getByText('On')).toBeVisible();
  const swarm=page.getByRole('article',{name:'Swarm downloads'});
  await expect(swarm.getByText('peers in a public swarm can see this computer’s address',{exact:false})).toBeVisible();
  await page.getByRole('article',{name:'Search Pirate Face'}).getByRole('button',{name:'Stop searching Pirate Face'}).click();
  await expect.poll(()=>calls.at(-1)).toEqual({path:'desktop-pirate-face-consent',body:{enabled:false}});
  await expect(page.getByRole('article',{name:'Hugging Face access token'})).toBeVisible();
  await page.getByRole('button',{name:'Open sharing settings'}).click();
  await expect(page.getByRole('button',{name:'Sharing',exact:true}).first()).toHaveAttribute('aria-current','page');
});
