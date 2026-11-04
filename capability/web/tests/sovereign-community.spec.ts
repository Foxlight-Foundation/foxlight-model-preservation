import {test,expect} from '@playwright/test';
const id='a'.repeat(64),other='b'.repeat(64),none='c'.repeat(64);
const copy={copy_id:id,manifest_id:id,name:'Open text model',size_bytes:1024,member_count:2};
const defaults=['apache-2.0','mit','bsd-3-clause','openrail-m'];
const policy={enabled:false,share:'eligible',licenses:defaults,chosen:[],withheld:[],daily_upload_bytes:20*1024**3,monthly_upload_bytes:300*1024**3,port:8743,consent_expires:null,public_url:null,announce_dht:true,publish_nostr:true,nostr_relays:['wss://relay.one.example','wss://relay.two.example']};
const copies=[
 {copy_id:id,name:'Open text model',size_bytes:1024,status:'eligible',licenses:['Apache-2.0'],public_manifest_id:null},
 {copy_id:other,name:'Community image model',size_bytes:2048,status:'license_not_selected',licenses:['CC-BY-4.0'],public_manifest_id:null},
 {copy_id:none,name:'Unlabelled model',size_bytes:4096,status:'no_license',licenses:[],public_manifest_id:null},
];
const providers=[
 {id:'r2',name:'Cloudflare R2',asks:['account_id','bucket','public_url'],note:'Recommended: R2 charges nothing for downloads.'},
 {id:'b2',name:'Backblaze B2',asks:['region','bucket'],note:'Create a public bucket.'},
 {id:'other',name:'Other S3-compatible storage',asks:['endpoint','region','bucket','public_url'],note:'For MinIO.'},
];
const connected={provider:'r2',provider_name:'Cloudflare R2',bucket:'my-models',public_url:'https://pub-abc.r2.dev',model_count:1,last_synced:2000000000,last_error:null};
const reach={status:'private_network',local_address:'192.168.1.20',port:8743,mirror_url:'http://192.168.1.20:8743'};
function view(on:boolean,overrides:Record<string,unknown>={}){return {policy:{...policy,enabled:on,consent_expires:on?2000000000:null},consented:on,default_licenses:defaults,copies:on?copies.map(item=>item.status==='eligible'?{...item,status:'sharing',public_manifest_id:'d'.repeat(64)}:item):copies,sharing_count:on?1:0,running:on,http_port:on?8743:null,reachability:reach,last_error:null,bucket:null,bucket_providers:providers,uploaded_today_bytes:0,uploaded_this_month_bytes:0,dht_available:true,dht_announcing:false,nostr_publishing:false,announce_reasons:{dht:['no_reachable_address'],nostr:['no_reachable_address']},...overrides};}

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
 // Widening the licence policy is one choice for every model, not a per-model review.
 await page.getByLabel('Add a licence').fill('CC-BY-4.0');await page.getByRole('button',{name:'Add',exact:true}).click();
 await page.getByRole('checkbox',{name:'Keep sharing these models from this computer until I turn it off, and announce it publicly on the BitTorrent DHT and 2 Nostr relays.'}).check();
 await start.click();
 expect(configured).toEqual({enabled:true,consent:true,share:'eligible',licenses:[...defaults,'CC-BY-4.0'],public_url:null,announce_dht:true,publish_nostr:true,announce_disclosure:1});
 await expect(page.locator('#sovereign-main')).not.toHaveAttribute('inert','');
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

test('a backup that failed to join can still be kept back before its retry',async({page})=>{
 let chosen:Record<string,unknown>|null=null;
 await page.route('**/api/desktop-community-read',r=>r.fulfill({json:view(true,{copies:copies.map(item=>item.copy_id===id?{...item,status:'failed',error_code:'insufficient_space'}:item)})}));
 await page.route('**/api/desktop-community-choose',r=>{chosen=r.request().postDataJSON();return r.fulfill({json:view(true)});});
 await page.goto('/');await page.getByRole('button',{name:'Sharing',exact:true}).click();
 const panel=page.getByRole('region',{name:'Help keep models available'});
 await panel.getByText(/Your backups ·/).click();
 await expect(panel.getByText('Couldn’t join · will retry',{exact:true})).toBeVisible();
 await panel.getByRole('button',{name:'Keep this one back',exact:true}).click();
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
 await start.click();
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
 await page.getByRole('button',{name:'Save these choices',exact:true}).click();
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
