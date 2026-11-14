import {readFileSync} from 'node:fs';
import {test,expect,type Locator,type Page} from '@playwright/test';
// The core's licence-family definition, exactly as the host sends it.
const families=JSON.parse(readFileSync(new URL('../../src/foxlight_preservation_library/licence_families_v1.json',import.meta.url),'utf8')) as {families:{id:string;default:boolean;licences:{id:string;default?:boolean}[]}[]};
const familyIds=(id:string)=>families.families.find(family=>family.id===id)!.licences.map(item=>item.id);
/** A click resolves before the route handler records its body; wait for the reply. */
async function sent(page:Page,button:Locator,action:string){const reply=page.waitForResponse(response=>response.url().endsWith('/api/'+action));await button.click();await reply;}
const id='a'.repeat(64),other='b'.repeat(64),none='c'.repeat(64);
const copy={copy_id:id,manifest_id:id,name:'Open text model',size_bytes:1024,member_count:2};
// A licence's own default overrides its family's, as in the core definition.
const defaults=families.families.flatMap(family=>family.licences.filter(item=>item.default??family.default).map(item=>item.id));
const order=families.families.flatMap(family=>family.licences.map(item=>item.id));
const policy={enabled:false,share:'eligible',licenses:defaults,chosen:[],withheld:[],daily_upload_bytes:null as number|null,monthly_upload_bytes:null as number|null,port:8743,consent_expires:null,public_url:null,announce_dht:true,publish_nostr:true,nostr_relays:['wss://relay.one.example','wss://relay.two.example']};
const copies=[
 {copy_id:id,name:'Open text model',size_bytes:1024,status:'eligible',licenses:['Apache-2.0'],public_manifest_id:null},
 {copy_id:other,name:'Community image model',size_bytes:2048,status:'license_not_selected',licenses:['CC-BY-4.0'],public_manifest_id:null},
 {copy_id:none,name:'Unlabelled model',size_bytes:4096,status:'no_license',licenses:[],public_manifest_id:null},
];
const providers=[
 {id:'r2',name:'Cloudflare R2',asks:['account_id','jurisdiction','bucket','public_url'],optional:['jurisdiction'],choices:{jurisdiction:[{value:'',label:'Default (no jurisdiction)'},{value:'eu',label:'European Union'},{value:'fedramp',label:'FedRAMP'},{value:'us',label:'United States'}]},note:'Recommended: R2 charges nothing for downloads.'},
 {id:'s3',name:'Amazon S3',asks:['region','bucket','public_url'],optional:['public_url'],note:'With CloudFront in front, keep the bucket private and paste its address.'},
 {id:'b2',name:'Backblaze B2',asks:['region','bucket'],note:'Create a public bucket.'},
 {id:'hetzner',name:'Hetzner Object Storage',asks:['region','bucket'],note:'Set the bucket to Public.'},
 {id:'other',name:'Other S3-compatible storage',asks:['endpoint','region','bucket','public_url'],note:'For MinIO.'},
];
const connected={provider:'r2',provider_name:'Cloudflare R2',bucket:'my-models',public_url:'https://pub-abc.r2.dev',model_count:1,last_synced:2000000000,last_error:null};
const reach={status:'private_network',local_address:'192.168.1.20',port:8743,mirror_url:'http://192.168.1.20:8743'};
function view(on:boolean,overrides:Record<string,unknown>={}){return {policy:{...policy,enabled:on,consent_expires:on?2000000000:null},consented:on,default_licenses:defaults,licence_families:families,copies:on?copies.map(item=>item.status==='eligible'?{...item,status:'sharing',public_manifest_id:'d'.repeat(64)}:item):copies,sharing_count:on?1:0,running:on,http_port:on?8743:null,reachability:reach,last_error:null,bucket:null,bucket_providers:providers,uploaded_today_bytes:0,uploaded_this_month_bytes:0,dht_available:true,dht_announcing:false,nostr_publishing:false,announce_reasons:{dht:['no_reachable_address'],nostr:['no_reachable_address']},...overrides};}

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
 // The consent sheet is modal: the page behind it is inert until it closes.
 await expect(page.locator('#sovereign-main')).toHaveAttribute('inert','');
 await expect(page.getByRole('radio',{name:/Every backup whose licence allows it/})).toBeChecked();
 const start=page.getByRole('button',{name:'Start sharing',exact:true});
 await expect(start).toBeDisabled();
 await expect(page.getByText('will see this computer’s network address',{exact:false})).toBeVisible();
 // Licence families are one choice for every model, not a per-model review.
 // Permissive, attribution, copyleft and responsible-AI licences start on.
 await expect(page.getByText('2 of your 3 backups match.',{exact:false})).toBeVisible();
 await expect(page.getByRole('switch',{name:/^Permissive/})).toHaveAccessibleName(/1 of your backups uses one of these\./);
 for(const name of ['Permissive','Attribution','Copyleft','Responsible AI','Non-commercial'])await expect(page.getByRole('switch',{name:new RegExp('^'+name)})).toBeChecked();
 // Non-commercial says when to switch it off.
 await expect(page.getByRole('switch',{name:/^Non-commercial/})).toHaveAccessibleName(/Switch this off if you run the mirror commercially/);
 // Makers' terms Hugging Face hosts are on; three not known to allow redistribution are off.
 const custom=page.getByRole('switch',{name:/^Custom model terms/});
 await expect(custom).not.toBeChecked();
 await expect(custom).toHaveAccessibleName(/9 of 12/);
 // Llama 3.2 and Llama 4 are on, so the EU notice shows; each has its own switch.
 // Both are on: one sentence names both, not a notice per licence.
 await expect(page.locator('.sk-callout').getByText('Meta’s terms don’t license the Llama 3.2 vision models or Llama 4 to people or companies based in the EU. If you’re in the EU, switch off Llama 3.2 and Llama 4.',{exact:true})).toBeVisible();
 await expect(page.locator('.sk-callout').getByText(/based in the EU/)).toHaveCount(1);
 await page.getByRole('switch',{name:/^Copyleft/}).uncheck();
 await page.getByText('Custom model terms licences · 9 of 12 on',{exact:false}).click();
 await expect(page.getByText('Covers Gemma 1 to 3',{exact:false})).toBeVisible();
 await expect(page.getByText('we couldn\'t confirm these terms allow anyone to redistribute',{exact:false}).first()).toBeVisible();
 await page.getByRole('checkbox',{name:/Llama 4 Community License/}).uncheck();
 // Only the switch that is still on is named.
 await expect(page.locator('.sk-callout').getByText('Meta’s terms don’t license the Llama 3.2 vision models to people or companies based in the EU. If you’re in the EU, switch off Llama 3.2.',{exact:true})).toBeVisible();
 await page.getByRole('checkbox',{name:/Grok 2 Community License/}).check();
 // Upload limits are off unless the owner turns them on.
 await expect(page.getByRole('switch',{name:/^Limit uploads/})).not.toBeChecked();
 await expect(page.getByText('No upload limit. Uploads are counted',{exact:false})).toBeVisible();
 await page.getByRole('checkbox',{name:'Keep sharing these models from this computer until I turn it off, and announce it publicly on the BitTorrent DHT and 2 Nostr relays.'}).check();
 await sent(page,start,'desktop-community-configure');
 const copyleft=familyIds('copyleft');
 const chosen=new Set([...defaults.filter(item=>!copyleft.includes(item)&&item!=='llama4'),'grok2-community']);
 expect(configured).toEqual({enabled:true,consent:true,share:'eligible',licenses:order.filter(item=>chosen.has(item)),public_url:null,announce_dht:true,publish_nostr:true,announce_disclosure:1});
 await expect(page.locator('#sovereign-main')).not.toHaveAttribute('inert','');
 await expect(panel.getByText('Sharing 1 model',{exact:true})).toBeVisible();
 // Reachability is stated plainly; the router is never changed.
 await expect(panel.getByText('behind a router',{exact:false})).toBeVisible();
 await expect(panel.getByText('http://192.168.1.20:8743',{exact:true})).toBeVisible();
 await sent(page,panel.getByRole('button',{name:'Turn off',exact:true}),'desktop-community-configure');
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
 await sent(page,panel.getByRole('button',{name:'Keep this one back',exact:true}),'desktop-community-choose');
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

test('a backup that failed to join can still be kept back before its retry',async({page})=>{
 let chosen:Record<string,unknown>|null=null;
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{copies:copies.map(item=>item.copy_id===id?{...item,status:'failed',error_code:'insufficient_space'}:item)})}));
 await page.route('**/api/desktop-community-choose',r=>{chosen=r.request().postDataJSON();return r.fulfill({json:view(true)});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await panel.getByText(/Your backups ·/).click();
 await expect(panel.getByText('Couldn’t join · will retry',{exact:true})).toBeVisible();
 await sent(page,panel.getByRole('button',{name:'Keep this one back',exact:true}),'desktop-community-choose');
 expect(chosen).toEqual({copy_id:id,chosen:false,withheld:true});
});

test('a failed status refresh keeps an open setup sheet usable',async({page})=>{
 let reads=0;
 await page.route('**/api/desktop-community-read',r=>{reads++;return reads===1?r.fulfill({json:view(false)}):r.fulfill({status:503,json:{error:'host_unavailable'}});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await panel.getByRole('button',{name:'Turn on',exact:true}).click();
 await expect.poll(()=>reads,{timeout:12000}).toBeGreaterThan(1);
 await expect(page.getByRole('heading',{name:'Share your backups with everyone'})).toBeVisible();
 await page.getByRole('button',{name:'Close sharing setup'}).click();
 await expect(page.locator('#sovereign-main')).not.toHaveAttribute('inert','');
 await expect(panel).toBeVisible();
});

test('cloud storage is checked end to end before it is saved, then shown plainly',async({page})=>{
 let bucket:Record<string,unknown>|null=null;const sent:Record<string,unknown>[]=[];let disconnected=false;
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{bucket})}));
 await page.route('**/api/desktop-community-bucket-connect',r=>{const body=r.request().postDataJSON();sent.push(body);
  // The first try fails the public download; the second passes.
  if(sent.length===1)return r.fulfill({json:{...view(true),status:'public'}});
  bucket=connected;return r.fulfill({json:{...view(true,{bucket}),status:'ready'}});});
 await page.route('**/api/desktop-community-bucket-disconnect',r=>{disconnected=true;bucket=null;return r.fulfill({json:{...view(true),status:'disconnected'}});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await expect(panel.getByText('people anywhere can download from it',{exact:false})).toBeVisible();
 await panel.getByRole('button',{name:'Set up cloud storage',exact:true}).click();
 // R2 is preselected, and only its three questions plus the key are asked.
 await expect(page.getByRole('radio',{name:/Cloudflare R2/})).toBeChecked();
 await expect(page.getByLabel('Region')).toHaveCount(0);
 const go=page.getByRole('button',{name:'Check and connect',exact:true});
 await expect(go).toBeDisabled();
 await page.getByLabel('Account ID').fill('0'.repeat(32));
 await page.getByLabel('Bucket name').fill('my-models');
 await page.getByLabel('Public address').fill('https://pub-abc.r2.dev');
 await page.getByLabel('Access key ID').fill('AKIAEXAMPLE');
 await expect(page.getByLabel('Secret access key')).toHaveAttribute('type','password');
 await page.getByLabel('Secret access key').fill('s3cret');
 await go.click();
 await expect(page.getByText('nobody could download from the public address',{exact:false})).toBeVisible();
 expect(sent[0]).toEqual({provider:'r2',account_id:'0'.repeat(32),bucket:'my-models',public_url:'https://pub-abc.r2.dev',access_key:'AKIAEXAMPLE',secret_key:'s3cret'});
 await go.click();
 await expect(page.getByRole('button',{name:'Check and connect'})).toHaveCount(0);
 await expect(panel.getByText('Cloudflare R2 · my-models',{exact:true})).toBeVisible();
 await expect(panel.getByText('https://pub-abc.r2.dev',{exact:true})).toBeVisible();
 // The secret never appears on the page once connected.
 expect(await page.content()).not.toContain('s3cret');
 await panel.getByRole('button',{name:'Disconnect',exact:true}).click();
 expect(disconnected).toBe(true);
 await expect(panel.getByRole('button',{name:'Set up cloud storage',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('choosing another provider asks only what that provider needs',async({page})=>{
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true)}));
 await page.route('**/api/desktop-community-bucket-connect',r=>r.fulfill({status:400,json:{error:'bucket_details_invalid'}}));
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await page.getByRole('region',{name:'Help keep models available'}).getByRole('button',{name:'Set up cloud storage',exact:true}).click();
 await page.getByRole('radio',{name:/Backblaze B2/}).check();
 await expect(page.getByLabel('Account ID')).toHaveCount(0);
 await expect(page.getByLabel('Public address')).toHaveCount(0);
 await expect(page.getByLabel('Region')).toHaveAttribute('placeholder','For example us-west-004');
 await page.getByRole('radio',{name:/Hetzner Object Storage/}).check();
 await expect(page.getByLabel('Region')).toHaveAttribute('placeholder','fsn1, nbg1 or hel1');
 await page.getByRole('radio',{name:/Backblaze B2/}).check();
 await page.getByLabel('Region').fill('us-west-004');await page.getByLabel('Bucket name').fill('Bad Name');
 await page.getByLabel('Access key ID').fill('k');await page.getByLabel('Secret access key').fill('s');
 await page.getByRole('button',{name:'Check and connect',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('Bucket names use lowercase letters');
});

test('a bucket waiting on the upload limit says so plainly',async({page})=>{
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{bucket:{...connected,model_count:0,last_error:'bucket_upload_limit_reached'}})}));
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await expect(panel.getByText('the rest uploads when it renews',{exact:false})).toBeVisible();
 await expect(panel.getByText('didn’t finish',{exact:false})).toHaveCount(0);
});

test('a connected bucket stays visible after sharing is turned off, until it is empty and disconnected',async({page})=>{
 let bucket:Record<string,unknown>|null={...connected,model_count:2};
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(false,{bucket})}));
 await page.route('**/api/desktop-community-bucket-disconnect',r=>{bucket=null;return r.fulfill({json:{...view(false),status:'disconnected'}});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await expect(panel.getByText('Off',{exact:true})).toBeVisible();
 await expect(panel.getByText('taking your models out of the bucket',{exact:false})).toBeVisible();
 // An upload whose outcome was never confirmed may still be public.
 bucket={...connected,model_count:0,withdraw_count:1,last_error:'bucket_request_failed'};
 await expect(panel.getByText('The bucket refused the last attempt',{exact:false})).toBeVisible({timeout:10000});
 await expect(panel.getByText('your models are out of the bucket',{exact:false})).toHaveCount(0);
 bucket={...connected,model_count:0,withdraw_count:0};
 await expect(panel.getByText('your models are out of the bucket',{exact:false})).toBeVisible({timeout:10000});
 await expect(panel.getByRole('button',{name:'Set up cloud storage'})).toHaveCount(0);
 await panel.getByRole('button',{name:'Disconnect',exact:true}).click();
 await expect(panel.getByRole('button',{name:'Disconnect'})).toHaveCount(0);
});

test('each public announcement is disclosed before consent and named in it',async({page},info)=>{
 let configured:Record<string,unknown>|null=null;
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(false)}));
 await page.route('**/api/desktop-community-configure',r=>{configured=r.request().postDataJSON();return r.fulfill({json:view(true)});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await page.getByRole('region',{name:'Help keep models available'}).getByRole('button',{name:'Turn on',exact:true}).click();
 const dht=page.getByRole('checkbox',{name:/Announce on the BitTorrent DHT/});
 const nostr=page.getByRole('checkbox',{name:/Publish to Nostr relays/});
 // On by default, which is why both are disclosed here, before the consent.
 await expect(dht).toBeChecked();await expect(nostr).toBeChecked();
 await expect(page.getByText('public IP address and mirror port on the public BitTorrent DHT as a Sovereign mirror',{exact:false})).toBeVisible();
 await expect(page.getByText('relay.one.example, relay.two.example',{exact:false})).toBeVisible();
 await expect(page.getByText('it isn’t anonymous',{exact:false})).toHaveCount(2);
 await dht.scrollIntoViewIfNeeded();
 await page.screenshot({path:info.outputPath('community-sheet-announcements.png')});
 const start=page.getByRole('button',{name:'Start sharing',exact:true});
 const field=page.getByLabel('Public address (optional)');
 await field.fill('models.example.org');
 await expect(page.getByText('Start the address with https://',{exact:false})).toBeVisible();
 await field.fill('http://203.0.113.7:8743');
 await expect(page.getByText('Plain http:// isn’t encrypted',{exact:false})).toBeVisible();
 await field.fill('https://models.example.org');
 await expect(page.getByText('Plain http:// isn’t encrypted',{exact:false})).toHaveCount(0);
 await dht.uncheck();
 const consent=page.getByRole('checkbox',{name:'Keep sharing these models from this computer until I turn it off, and announce it publicly on 2 Nostr relays.'});
 await consent.check();
 await nostr.uncheck();
 await expect(page.getByRole('checkbox',{name:'Keep sharing these models from this computer until I turn it off, without announcing it publicly.'})).toBeChecked();
 await sent(page,start,'desktop-community-configure');
 expect(configured).toEqual({enabled:true,consent:true,share:'eligible',licenses:defaults,public_url:'https://models.example.org',announce_dht:false,publish_nostr:false,announce_disclosure:1});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('status says plainly how the mirror is found, and the address check is labelled best effort',async({page},info)=>{
 let checks=0;
 const state=view(true,{policy:{...policy,enabled:true,consent_expires:2000000000,public_url:'http://203.0.113.7:8743'},dht_announcing:true,nostr_publishing:false,announce_reasons:{dht:[],nostr:['turned_off']}});
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:state}));
 await page.route('**/api/desktop-community-check-address',r=>{checks++;return r.fulfill({json:checks===1?{address:'http://203.0.113.7:8743',outcome:'unreachable',http_status:null}:{address:'http://203.0.113.7:8743',outcome:'this_mirror',http_status:null}});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 const findable=panel.getByRole('list',{name:'How people find this mirror'});
 await expect(findable).toContainText('Announced on the BitTorrent DHT.');
 await expect(findable).toContainText('Not published to Nostr relays: you turned it off.');
 await expect(panel.getByText('This address uses plain http://',{exact:false})).toBeVisible();
 // Behind a router, a declared address is only as good as its forwarding.
 await expect(panel.getByText('only through the public address below',{exact:false})).toBeVisible();
 await panel.getByRole('button',{name:'Check address',exact:true}).click();
 await expect(panel.getByRole('status')).toContainText('Many routers can’t loop back');
 await panel.getByRole('button',{name:'Check address',exact:true}).click();
 await expect(panel.getByRole('status')).toContainText('found this mirror there');
 await expect(panel.getByRole('status')).toContainText('best-effort check from this computer');
 await expect(panel.getByRole('status')).toContainText('can’t prove that people elsewhere can reach it');
 expect(checks).toBe(2);
 await page.screenshot({path:info.outputPath('community-status-reachability.png'),fullPage:true});
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('reasons the mirror is not announced are spelled out',async({page})=>{
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{dht_available:false,announce_reasons:{dht:['dht_unavailable','nothing_shared'],nostr:['no_reachable_address']}})}));
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const findable=page.getByRole('region',{name:'Help keep models available'}).getByRole('list',{name:'How people find this mirror'});
 await expect(findable).toContainText('Not announced on the BitTorrent DHT: this copy of Sovereign doesn’t include the BitTorrent component and nothing is being shared yet.');
 await expect(findable).toContainText('no public address is set and there’s no cloud storage');
 // Behind a router with no address of its own, cloud storage is the way to reach people anywhere.
 await expect(page.getByText('Cloud storage below lets people anywhere download',{exact:false})).toBeVisible();
});

test('a consent given before the disclosure announces nothing until the owner chooses',async({page},info)=>{
 let configured:Record<string,unknown>|null=null;
 let state=view(true,{announce_review_required:true,announce_reasons:{dht:['awaiting_review'],nostr:['awaiting_review']}});
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:state}));
 await page.route('**/api/desktop-community-configure',r=>{configured=r.request().postDataJSON();state=view(true,{policy:{...policy,enabled:true,consent_expires:2000000000,announce_dht:false,publish_nostr:true},nostr_publishing:true,announce_reasons:{dht:['turned_off'],nostr:[]}});return r.fulfill({json:state});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 // Sharing itself carries on; only the announcements wait.
 await expect(panel.getByText('Sharing 1 model',{exact:true})).toBeVisible();
 const findable=panel.getByRole('list',{name:'How people find this mirror'});
 await expect(findable).toContainText('Not announced on the BitTorrent DHT: waiting for your review.');
 await expect(findable).toContainText('Not published to Nostr relays: waiting for your review.');
 await expect(panel.getByText('isn’t announced on the BitTorrent DHT or Nostr relays until you choose',{exact:false})).toBeVisible();
 await panel.getByRole('button',{name:'Choose how people find this computer',exact:true}).click();
 await expect(page.locator('#sovereign-main')).toHaveAttribute('inert','');
 await expect(page.getByRole('heading',{name:'Choose how people find this computer'})).toBeVisible();
 // The same disclosure as at first consent, and no new consent to sharing.
 await expect(page.getByText('public IP address and mirror port on the public BitTorrent DHT as a Sovereign mirror',{exact:false})).toBeVisible();
 await expect(page.getByRole('checkbox',{name:/Keep sharing these models/})).toHaveCount(0);
 await page.screenshot({path:info.outputPath('community-announce-review.png')});
 await page.getByRole('checkbox',{name:/Announce on the BitTorrent DHT/}).uncheck();
 await sent(page,page.getByRole('button',{name:'Save these choices',exact:true}),'desktop-community-configure');
 expect(configured).toEqual({public_url:null,announce_dht:false,publish_nostr:true,announce_disclosure:1});
 await expect(page.locator('#sovereign-main')).not.toHaveAttribute('inert','');
 await expect(panel.getByRole('button',{name:'Choose how people find this computer'})).toHaveCount(0);
 await expect(findable).toContainText('Not announced on the BitTorrent DHT: you turned it off.');
 await expect(findable).toContainText('Published to 2 Nostr relays.');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('an unusable saved address and a slow address are explained, not fatal',async({page})=>{
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{address_invalid:true,announce_reasons:{dht:['address_invalid'],nostr:['address_invalid','no_reachable_address']}})}));
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await expect(panel.getByRole('list',{name:'How people find this mirror'})).toContainText('Not announced on the BitTorrent DHT: the public address saved earlier has a port that can’t be used, so it was set aside; set it again.');
 await page.unroute('**/api/desktop-community-read');
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{policy:{...policy,enabled:true,consent_expires:2000000000,public_url:'https://models.example.org'}})}));
 await page.route('**/api/desktop-community-check-address',r=>r.fulfill({json:{address:'https://models.example.org',outcome:'too_slow',http_status:null}}));
 await page.reload();await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await panel.getByRole('button',{name:'Check address',exact:true}).click();
 await expect(panel.getByRole('status')).toContainText('answered too slowly, so the check stopped');
 await expect(panel.getByRole('status')).toContainText('best-effort check');
});

const screenshot=(name:string,info:{project:{name:string};outputPath:(name:string)=>string})=>process.env.LIBRARY_SCREENSHOTS?`${process.env.LIBRARY_SCREENSHOTS}/${info.project.name}-${name}.png`:info.outputPath(`${name}.png`);

test('upload limits are opt-in and a custom licence id is checked before it is added',async({page},info)=>{
 let configured:Record<string,unknown>|null=null;
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(false)}));
 await page.route('**/api/desktop-community-configure',r=>{configured=r.request().postDataJSON();return r.fulfill({json:view(true,{policy:{...policy,enabled:true,consent_expires:2000000000,daily_upload_bytes:5*1024**3,monthly_upload_bytes:300*1024**3}})});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await page.getByRole('region',{name:'Help keep models available'}).getByRole('button',{name:'Turn on',exact:true}).click();
 const field=page.getByLabel('Custom licence id');
 await field.fill('Other');await page.getByRole('button',{name:'Add',exact:true}).click();
 await expect(page.getByText('That doesn’t name a licence',{exact:false})).toBeVisible();
 await field.fill('EUPL 1.2');await page.getByRole('button',{name:'Add',exact:true}).click();
 await expect(page.getByRole('button',{name:'Remove eupl-1.2'})).toBeVisible();
 await page.getByText('Licences that allow sharing').scrollIntoViewIfNeeded();
 await page.screenshot({path:screenshot('community-licences',info)});
 await page.getByText('Custom model terms licences',{exact:false}).click();
 await page.locator('.sk-callout').getByText('switch off Llama 3.2 and Llama 4',{exact:false}).scrollIntoViewIfNeeded();
 await page.screenshot({path:screenshot('community-families',info)});
 await page.getByRole('switch',{name:/^Non-commercial/}).scrollIntoViewIfNeeded();
 await page.screenshot({path:screenshot('community-noncommercial',info)});
 const limit=page.getByRole('switch',{name:/^Limit uploads/});
 await limit.check();
 await expect(page.getByLabel('Upload limit per day (GB)')).toHaveValue('20');
 await expect(page.getByLabel('Upload limit per month (GB)')).toHaveValue('300');
 await page.getByLabel('Upload limit per day (GB)').fill('500');
 await expect(page.getByText('the daily limit can’t be larger than the monthly one',{exact:false})).toBeVisible();
 await page.getByRole('checkbox',{name:/Keep sharing these models from this computer/}).check();
 await expect(page.getByRole('button',{name:'Start sharing',exact:true})).toBeDisabled();
 await page.getByLabel('Upload limit per day (GB)').fill('5');
 await limit.scrollIntoViewIfNeeded();
 await page.screenshot({path:screenshot('community-limits',info)});
 await sent(page,page.getByRole('button',{name:'Start sharing',exact:true}),'desktop-community-configure');
 expect(configured).toMatchObject({licenses:[...defaults,'eupl-1.2'],daily_upload_bytes:5*1024**3,monthly_upload_bytes:300*1024**3});
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await expect(panel.getByText(/of 5(\.0)? GB/)).toBeVisible();
 await expect(panel.getByText('Permissive, Attribution, Copyleft, Responsible AI, Non-commercial and some custom model terms',{exact:true})).toBeVisible();
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('saved limits from an earlier version are kept and never block other changes',async({page})=>{
 let configured:Record<string,unknown>|null=null;
 // Earlier versions saved any positive limits, even a daily one above the monthly one.
 const saved={...policy,enabled:true,consent_expires:2000000000,daily_upload_bytes:50*1024**3,monthly_upload_bytes:10*1024**3};
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{policy:saved})}));
 await page.route('**/api/desktop-community-configure',r=>{configured=r.request().postDataJSON();return r.fulfill({json:view(true,{policy:saved})});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await expect(panel.getByText(/of 50(\.0)? GB/)).toBeVisible();
 await panel.getByRole('button',{name:'Change what’s shared',exact:true}).click();
 await expect(page.getByRole('switch',{name:/^Limit uploads/})).toBeChecked();
 await page.getByRole('switch',{name:/^Copyleft/}).uncheck();
 await page.getByRole('checkbox',{name:/Keep sharing these models from this computer/}).check();
 const save=page.getByRole('button',{name:'Save changes',exact:true});
 await expect(save).toBeEnabled();
 await sent(page,save,'desktop-community-configure');
 expect(configured).not.toHaveProperty('daily_upload_bytes');
 expect(configured).not.toHaveProperty('monthly_upload_bytes');
 // Changing the limits themselves asks for daily within monthly.
 await panel.getByRole('button',{name:'Change what’s shared',exact:true}).click();
 await page.getByLabel('Upload limit per day (GB)').fill('40');
 await page.getByRole('checkbox',{name:/Keep sharing these models from this computer/}).check();
 await expect(page.getByRole('button',{name:'Save changes',exact:true})).toBeDisabled();
});

const shots=process.env.BUCKET_SHOTS;
const handoffs=(ids:string[])=>ids.map(item=>`api/desktop-bucket-handoff/${item}`);

test('Amazon S3 takes an optional CloudFront address; empty keeps the public bucket',async({page},info)=>{
 const sent:Record<string,unknown>[]=[];
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true)}));
 await page.route('**/api/desktop-community-bucket-connect',r=>{const body=r.request().postDataJSON();sent.push(body);
  return body.public_url==='http://d1234abcd.cloudfront.net'?r.fulfill({status:409,json:{error:'bucket_public_url_invalid'}}):r.fulfill({json:{...view(true),status:'upload'}});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await page.getByRole('region',{name:'Help keep models available'}).getByRole('button',{name:'Set up cloud storage',exact:true}).click();
 await page.getByRole('radio',{name:/Amazon S3/}).check();
 const address=page.getByLabel('Public address');
 await expect(address).toHaveAttribute('placeholder','https://d1234abcd.cloudfront.net (optional)');
 await expect(page.getByText('(optional)',{exact:false}).first()).toBeVisible();
 await page.getByLabel('Region').fill('eu-central-1');await page.getByLabel('Bucket name').fill('sovereign-mirror-example');
 await page.getByLabel('Access key ID').fill('AKIAEXAMPLE');await page.getByLabel('Secret access key').fill('s3cret');
 const go=page.getByRole('button',{name:'Check and connect',exact:true});
 // The public address is optional, so the form is complete without it.
 await expect(go).toBeEnabled();
 await sent_(page,go);
 expect(sent[0]).toEqual({provider:'s3',region:'eu-central-1',bucket:'sovereign-mirror-example',access_key:'AKIAEXAMPLE',secret_key:'s3cret'});
 await address.fill('http://d1234abcd.cloudfront.net');
 await sent_(page,go);
 await expect(page.getByRole('alert')).toContainText('must start with https://');
 await address.fill('https://d1234abcd.cloudfront.net');
 if(shots&&info.project.name==='desktop'){await page.setViewportSize({width:1280,height:1500});await page.screenshot({path:`${shots}/s3-cloudfront-form.png`});}
 await sent_(page,go);
 expect(sent[2]).toEqual({provider:'s3',region:'eu-central-1',bucket:'sovereign-mirror-example',public_url:'https://d1234abcd.cloudfront.net',access_key:'AKIAEXAMPLE',secret_key:'s3cret'});
});
async function sent_(page:Page,button:Locator){await sent(page,button,'desktop-community-bucket-connect');}

test('an R2 bucket in a jurisdiction is connected at that jurisdiction',async({page},info)=>{
 let bucket:Record<string,unknown>|null=null;const sentBodies:Record<string,unknown>[]=[];
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{bucket})}));
 await page.route('**/api/desktop-community-bucket-contents',r=>r.fulfill({json:{models:[],total:0,offset:0,next_offset:null,public_url:'https://mirror.example.org'}}));
 await page.route('**/api/desktop-community-bucket-connect',r=>{sentBodies.push(r.request().postDataJSON());bucket={...connected,public_url:'https://mirror.example.org',jurisdiction:'eu',model_count:0};return r.fulfill({json:{...view(true,{bucket}),status:'ready'}});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await panel.getByRole('button',{name:'Set up cloud storage',exact:true}).click();
 const jurisdiction=page.getByLabel('Jurisdiction');
 await expect(jurisdiction).toHaveValue('');
 await expect(jurisdiction.locator('option')).toHaveText(['Default (no jurisdiction)','European Union','FedRAMP','United States']);
 await jurisdiction.selectOption('eu');
 await page.getByLabel('Account ID').fill('0'.repeat(32));await page.getByLabel('Bucket name').fill('my-models');
 await page.getByLabel('Public address').fill('https://mirror.example.org');
 await page.getByLabel('Access key ID').fill('AKIAEXAMPLE');await page.getByLabel('Secret access key').fill('s3cret');
 if(shots&&info.project.name==='desktop'){await page.setViewportSize({width:1280,height:1500});await page.screenshot({path:`${shots}/r2-jurisdiction-form.png`});}
 await sent_(page,page.getByRole('button',{name:'Check and connect',exact:true}));
 expect(sentBodies[0]).toEqual({provider:'r2',account_id:'0'.repeat(32),jurisdiction:'eu',bucket:'my-models',public_url:'https://mirror.example.org',access_key:'AKIAEXAMPLE',secret_key:'s3cret'});
 await expect(panel.getByText('Cloudflare R2 (EU jurisdiction) · my-models',{exact:true})).toBeVisible();
});

test('the bucket lists what it serves and exports verification info for recipients',async({page},info)=>{
 const first='e'.repeat(64),second='f'.repeat(64),key='ed25519:'+'1'.repeat(64);
 const models=[
  {manifest_id:first,name:'Open text model',size_bytes:3*1024**3,synced_at:1999990000},
  {manifest_id:second,name:null,size_bytes:null,synced_at:null},
 ];
 const offsets:number[]=[];
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{bucket:{...connected,model_count:3}})}));
 await page.route('**/api/desktop-community-bucket-contents',r=>{const {offset}=r.request().postDataJSON();offsets.push(offset);
  return r.fulfill({json:offset===0?{models,total:3,offset:0,next_offset:2,public_url:connected.public_url}:{models:[{manifest_id:'9'.repeat(64),name:'Third model',size_bytes:1024,synced_at:1999999000}],total:3,offset:2,next_offset:null,public_url:connected.public_url}});});
 // Signing keys are read per model, only for the export.
 const asked:string[]=[];
 await page.route('**/api/desktop-community-bucket-keys',r=>{const {manifest_id}=r.request().postDataJSON();asked.push(manifest_id);return r.fulfill({json:{manifest_id,keys:manifest_id===second?[]:[key],threshold:1}});});
 await page.route('**/api/desktop-bucket-handoff/*',r=>r.fulfill({body:'{"schema_version":"foxlight.model-preservation.handoff.v1"}',headers:{'Content-Type':'application/octet-stream','Content-Disposition':'attachment; filename="Sovereign-bucket-eeeeeeeeeeee.foxhandoff"'}}));
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 const list=panel.getByRole('list',{name:'Models in the bucket'});
 await expect(list.getByRole('listitem')).toHaveCount(2);
 await expect(list.getByText('Open text model')).toBeVisible();
 await expect(list.getByText('3.0 GB')).toBeVisible();
 await expect(list.getByText(first,{exact:true})).toBeVisible();
 await expect(list.getByText('Unnamed model')).toBeVisible();
 // A model whose upload time predates this version shows the bucket's check time.
 await expect(list.getByRole('listitem').nth(1)).toContainText('checked');
 await panel.getByRole('button',{name:/Show more \(1 left\)/}).click();
 await expect(list.getByRole('listitem')).toHaveCount(3);
 expect(offsets).toEqual([0,2]);
 if(shots&&info.project.name==='desktop'){await page.setViewportSize({width:1280,height:1500});await panel.getByText('Cloud storage',{exact:true}).scrollIntoViewIfNeeded();await page.screenshot({path:`${shots}/bucket-contents-list.png`});}
 expect(asked).toEqual([]);
 await panel.getByRole('button',{name:'Export verification info',exact:true}).click();
 const sheet=page.getByRole('dialog');
 await expect(sheet.getByText('https://pub-abc.r2.dev',{exact:true})).toBeVisible();
 await expect(sheet.getByText(key,{exact:true})).toHaveCount(1);
 expect(asked.sort()).toEqual([first,second,'9'.repeat(64)].sort());
 const links=sheet.getByRole('link',{name:/Save sharing file for/});
 await expect(links).toHaveCount(3);
 const hrefs=await links.evaluateAll(items=>items.map(item=>new URL((item as HTMLAnchorElement).href).pathname.replace(/^\//,'')));
 expect(hrefs).toEqual(handoffs([first,second,'9'.repeat(64)]));
 await expect(links.first()).toHaveAttribute('download','Open-text-model.foxhandoff');
 if(shots&&info.project.name==='desktop')await page.screenshot({path:`${shots}/bucket-export-verification-info.png`});
 const download=page.waitForEvent('download');
 await links.first().click();
 expect((await download).suggestedFilename()).toBe('Open-text-model.foxhandoff');
 // Nothing secret or private appears in the export screen.
 expect(await sheet.textContent()).not.toMatch(/secret|keychain|\/Users\//i);
});

test('seeding on Pirate Face is off until chosen, disclosed, and reported plainly',async({page},info)=>{
 let on=false,seeding=false,configured:Record<string,unknown>|null=null;
 const pirate=()=>({pirate_face:{available:true,on:seeding,checked:seeding?3:0,listed:seeding?2:0,matched:seeding?2:0,seeding:seeding?1:0,differs:seeding?1:0}});
 const current=()=>{const value=view(on,pirate());return {...value,policy:{...value.policy,seed_pirate_face:seeding}};};
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:current()}));
 await page.route('**/api/desktop-community-configure',r=>{configured=r.request().postDataJSON();on=configured?.enabled!==false;if(typeof configured?.seed_pirate_face==='boolean')seeding=configured.seed_pirate_face;return r.fulfill({json:current()});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await page.getByRole('region',{name:'Help keep models available'}).getByRole('button',{name:'Turn on',exact:true}).click();
 const seed=page.getByRole('checkbox',{name:/Also seed on Pirate Face/});
 await expect(seed).not.toBeChecked();
 await expect(page.getByText('straight from those files: no second copy',{exact:false})).toBeVisible();
 await expect(page.getByText('Peers in the public BitTorrent swarm see this computer’s address',{exact:false})).toBeVisible();
 await seed.check();
 await page.getByRole('checkbox',{name:/, and seed matching Pirate Face torrents\.$/}).check();
 await seed.scrollIntoViewIfNeeded();
 await page.screenshot({path:info.outputPath('community-sheet-pirate-face.png')});
 await sent(page,page.getByRole('button',{name:'Start sharing',exact:true}),'desktop-community-configure');
 expect(configured).toMatchObject({enabled:true,consent:true,seed_pirate_face:true});
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await expect(panel.getByText('Seeding 1 of 2 matching torrents · 2 of 3 looked-up models listed · 1 listed with different files, not seeded')).toBeVisible();
 // Changing something else never resends the choice.
 await panel.getByRole('button',{name:'Change what’s shared'}).click();
 await expect(page.getByRole('checkbox',{name:/Also seed on Pirate Face/})).toBeChecked();
 await page.getByRole('checkbox',{name:/Keep sharing these models/}).check();
 await sent(page,page.getByRole('button',{name:'Save changes',exact:true}),'desktop-community-configure');
 expect(configured).not.toHaveProperty('seed_pirate_face');
});

test('a core without BitTorrent shows Pirate Face seeding but cannot turn it on',async({page})=>{
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(false,{pirate_face:{available:false,on:false,checked:0,listed:0,matched:0,seeding:0,differs:0}})}));
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 await page.getByRole('region',{name:'Help keep models available'}).getByRole('button',{name:'Turn on',exact:true}).click();
 await expect(page.getByRole('checkbox',{name:/Also seed on Pirate Face/})).toBeDisabled();
 await expect(page.getByText('so this can’t be turned on',{exact:false})).toBeVisible();
});
