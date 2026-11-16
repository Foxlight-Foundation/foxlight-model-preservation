import {readFileSync} from 'node:fs';
import {test,expect,type Locator,type Page} from '@playwright/test';
const families=JSON.parse(readFileSync(new URL('../../src/foxlight_preservation_library/licence_families_v1.json',import.meta.url),'utf8')) as {families:{id:string;default:boolean;licences:{id:string;default?:boolean}[]}[]};
const defaults=families.families.flatMap(family=>family.licences.filter(item=>item.default??family.default).map(item=>item.id));
/** A click resolves before the route handler records its body; wait for the reply. */
async function sent(page:Page,button:Locator,action:string){const reply=page.waitForResponse(response=>response.url().endsWith('/api/'+action));await button.click();await reply;}
const id='a'.repeat(64),other='b'.repeat(64);
const saved={copy_id:id,manifest_id:id,name:'Open text model',size_bytes:1024,member_count:2};
const job={job_id:'1'.repeat(32),kind:'receive',state:'succeeded',name:'Open text model',error_code:null,output:null,copy_id:id,sources:{origin_files:2,mirror_files:0,mirrors:[]}};
const policy={enabled:false,share:'eligible',licenses:defaults,chosen:[] as string[],withheld:[],daily_upload_bytes:null,monthly_upload_bytes:null,port:8743,consent_expires:null,public_url:null,announce_dht:true,publish_nostr:true,nostr_relays:['wss://relay.one.example']};
const copies=[
 {copy_id:id,name:'Open text model',size_bytes:1024,status:'eligible',licenses:['Apache-2.0'],public_manifest_id:null},
 {copy_id:other,name:'Community image model',size_bytes:2048,status:'license_not_selected',licenses:['CC-BY-4.0'],public_manifest_id:null},
];
const reach={status:'private_network',local_address:'192.168.1.20',port:8743,mirror_url:'http://192.168.1.20:8743'};
function view(on:boolean,share='eligible'){return {policy:{...policy,share,enabled:on,consent_expires:on?2000000000:null},consented:on,default_licenses:defaults,licence_families:families,copies,sharing_count:0,running:on,http_port:null,reachability:reach,last_error:null,bucket:null,bucket_providers:[],dht_available:true,announce_reasons:{dht:[],nostr:[]}};}

async function setup(page:Page,{ask=true,copy=id}:{ask?:boolean;copy?:string}={}){
 const state={ask,on:false,share:'eligible',answered:0,configured:null as Record<string,unknown>|null,chosen:null as Record<string,unknown>|null};
 await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',copies:[saved],jobs:[{...job,copy_id:copy}],next_offset:null,keep_available_ask:state.ask}}));
 await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[saved],stored_total:1,total:1,next_offset:null}}));
 await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
 await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-backup-read',r=>r.fulfill({json:{destinations:[]}}));
 await page.route('**/api/desktop-sharing-read',r=>r.fulfill({json:{public_copies:[]}}));
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(state.on,state.share)}));
 await page.route('**/api/desktop-community-asked',r=>{state.answered++;state.ask=false;return r.fulfill({json:{keep_available_ask:false}});});
 await page.route('**/api/desktop-community-configure',r=>{state.configured=r.request().postDataJSON();state.on=state.configured?.enabled!==false;state.share=String(state.configured?.share??state.share);return r.fulfill({json:view(state.on,state.share)});});
 await page.route('**/api/desktop-community-choose',r=>{state.chosen=r.request().postDataJSON();return r.fulfill({json:view(state.on,state.share)});});
 await page.goto('/');
 // The question checks the model's standing first; wait for that answer.
 const read=ask?page.waitForResponse(response=>response.url().endsWith('/api/desktop-community-read')):null;
 await page.getByRole('button',{name:'Activity',exact:true}).click();
 await read;
 return state;
}

test('a finished save asks once, and agreeing opens the same sharing review for that model',async({page})=>{
 const state=await setup(page);
 const ask=page.locator('.sk-job').getByText('Help keep this model available?');
 await expect(ask).toBeVisible();
 // Asking shares nothing: only the answer is recorded.
 await sent(page,page.getByRole('button',{name:'Review sharing',exact:true}),'desktop-community-asked');
 expect(state.answered).toBe(1);
 expect(state.configured).toBeNull();
 await expect(page.getByRole('dialog').getByText('Open text model is saved.',{exact:false})).toBeVisible();
 const start=page.getByRole('button',{name:'Start sharing',exact:true});
 await expect(start).toBeDisabled();
 // Sharing only chosen backups starts with the one the question was about.
 await page.getByRole('radio',{name:/Only the backups I choose/}).check();
 await page.getByRole('checkbox',{name:/^Keep sharing these models from this computer until I turn it off/}).check();
 const chose=page.waitForResponse(response=>response.url().endsWith('/api/desktop-community-choose'));
 await start.click();
 await chose;
 expect(state.configured).toMatchObject({enabled:true,consent:true,share:'chosen'});
 expect(state.chosen).toEqual({copy_id:id,chosen:true,withheld:false});
 await expect(page.getByRole('dialog')).toHaveCount(0);
 // Answered: Activity no longer asks.
 await page.getByRole('button',{name:'Activity',exact:true}).click();
 await expect(page.locator('.sk-job').getByText('Done',{exact:true})).toBeVisible();
 await expect(ask).toHaveCount(0);
});

test('not now is remembered and shares nothing',async({page})=>{
 const state=await setup(page);
 await sent(page,page.getByRole('button',{name:'Not now',exact:true}),'desktop-community-asked');
 await expect(page.getByText('Help keep this model available?')).toHaveCount(0);
 expect(state.answered).toBe(1);
 expect(state.configured).toBeNull();
 await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('a model whose licence doesn’t allow sharing, or an answered question, is never asked',async({page})=>{
 await setup(page,{copy:other});
 await expect(page.locator('.sk-job').getByText('Done',{exact:true})).toBeVisible();
 await expect(page.getByText('Help keep this model available?')).toHaveCount(0);
 await page.unrouteAll({behavior:'ignoreErrors'});
 await setup(page,{ask:false});
 await expect(page.locator('.sk-job').getByText('Done',{exact:true})).toBeVisible();
 await expect(page.getByText('Help keep this model available?')).toHaveCount(0);
});
