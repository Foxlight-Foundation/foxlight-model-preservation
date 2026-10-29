import {test,expect} from '@playwright/test';
const id='a'.repeat(64),other='b'.repeat(64),none='c'.repeat(64);
const copy={copy_id:id,manifest_id:id,name:'Open text model',size_bytes:1024,member_count:2};
const defaults=['apache-2.0','mit','bsd-3-clause','openrail-m'];
const policy={enabled:false,share:'eligible',licenses:defaults,chosen:[],withheld:[],daily_upload_bytes:20*1024**3,monthly_upload_bytes:300*1024**3,port:8743,consent_expires:null};
const copies=[
 {copy_id:id,name:'Open text model',size_bytes:1024,status:'eligible',licenses:['Apache-2.0'],public_manifest_id:null},
 {copy_id:other,name:'Community image model',size_bytes:2048,status:'license_not_selected',licenses:['CC-BY-4.0'],public_manifest_id:null},
 {copy_id:none,name:'Unlabelled model',size_bytes:4096,status:'no_license',licenses:[],public_manifest_id:null},
];
const reach={status:'private_network',local_address:'192.168.1.20',port:8743,mirror_url:'http://192.168.1.20:8743'};
function view(on:boolean,overrides:Record<string,unknown>={}){return {policy:{...policy,enabled:on,consent_expires:on?2000000000:null},consented:on,default_licenses:defaults,copies:on?copies.map(item=>item.status==='eligible'?{...item,status:'sharing',public_manifest_id:'d'.repeat(64)}:item):copies,sharing_count:on?1:0,running:on,http_port:on?8743:null,reachability:reach,last_error:null,uploaded_today_bytes:0,uploaded_this_month_bytes:0,...overrides};}

test.beforeEach(async({page})=>{
 await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',copies:[copy],jobs:[],next_offset:null}}));
 await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[copy],stored_total:1,total:1,next_offset:null}}));
 await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
 await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-backup-read',r=>r.fulfill({json:{destinations:[]}}));
 await page.route('**/api/desktop-sharing-read',r=>r.fulfill({json:{public_copies:[]}}));
});

test('one switch shares every eligible backup after a single standing consent',async({page})=>{
 let on=false,configured:Record<string,unknown>|null=null;
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(on)}));
 await page.route('**/api/desktop-community-configure',r=>{configured=r.request().postDataJSON();on=configured?.enabled!==false;return r.fulfill({json:view(on)});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await expect(panel.getByText('1 backup can be shared',{exact:false})).toBeVisible();
 await panel.getByRole('button',{name:'Turn on',exact:true}).click();
 await expect(page.getByRole('radio',{name:/Every backup whose licence allows it/})).toBeChecked();
 const start=page.getByRole('button',{name:'Start sharing',exact:true});
 await expect(start).toBeDisabled();
 await expect(page.getByText('will see this computer’s network address',{exact:false})).toBeVisible();
 // Widening the licence policy is one choice for every model, not a per-model review.
 await page.getByLabel('Add a licence').fill('CC-BY-4.0');await page.getByRole('button',{name:'Add',exact:true}).click();
 await page.getByRole('checkbox',{name:'Keep sharing these models from this computer until I turn it off.'}).check();
 await start.click();
 expect(configured).toEqual({enabled:true,consent:true,share:'eligible',licenses:[...defaults,'CC-BY-4.0']});
 await expect(panel.getByText('Sharing 1 model',{exact:true})).toBeVisible();
 // Reachability is stated plainly; the router is never changed.
 await expect(panel.getByText('behind a router',{exact:false})).toBeVisible();
 await expect(panel.getByText('http://192.168.1.20:8743',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Turn off',exact:true}).click();
 expect(configured).toEqual({enabled:false});
 await expect(panel.getByText('Off',{exact:true})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('a backup can be kept back and a backup without a licence stays private',async({page})=>{
 let chosen:Record<string,unknown>|null=null;
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true)}));
 await page.route('**/api/desktop-community-choose',r=>{chosen=r.request().postDataJSON();return r.fulfill({json:view(true,{copies:copies.map(item=>item.copy_id===id?{...item,status:'withheld'}:item)})});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await panel.getByText('Your backups · 1 of 3 can be shared',{exact:true}).click();
 await expect(panel.getByText('No licence found · stays private',{exact:true})).toBeVisible();
 await expect(panel.getByText('Licence not in your list',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Keep this one back',exact:true}).click();
 expect(chosen).toEqual({copy_id:id,chosen:false,withheld:true});
 await expect(panel.getByRole('button',{name:'Share again',exact:true})).toBeVisible();
 await expect(panel.getByText('Nothing is deleted.',{exact:false})).toBeVisible();
});

test('an older core without community sharing hides the switch',async({page})=>{
 await page.route('**/api/desktop-community-read',r=>r.fulfill({status:400,json:{error:'invalid_control'}}));
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await expect(page.getByRole('heading',{name:'Sharing',exact:true})).toBeVisible();
 await expect(page.getByRole('region',{name:'Help keep models available'})).toHaveCount(0);
});
