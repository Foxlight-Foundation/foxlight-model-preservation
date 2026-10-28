import {test,expect} from '@playwright/test';

const id='a'.repeat(64),key='ed25519:'+ 'b'.repeat(64);
const copy={copy_id:id,manifest_id:id,name:'Small writing model',size_bytes:1024,member_count:2};
const empty={host_kind:'sovereign',copies:[],jobs:[],next_offset:null};
test.beforeEach(async({page})=>{
  await page.route('**/api/library*',r=>r.fulfill({json:empty}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[],total:0,stored_total:0,next_offset:null}}));
});

test('first-launch choices and help start no network job or trust grant',async({page})=>{
  const effects:string[]=[];
  page.on('request',r=>{if(/\/api\/desktop-(start|refresh|plan|source-follow|sharing-review)/.test(r.url()))effects.push(r.url());});
  await page.goto('/');
  await expect(page.getByRole('heading',{name:'What would you like to do?',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Choose a recovery kit',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Choose a sharing file',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Getting started',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Your model, kept safe',exact:true})).toBeVisible();
  await expect(page.getByText('Software signing, model verification and permission to run code are separate.',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'Close getting started'}).click();
  expect(effects).toEqual([]);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

for(const [button,input] of [['Choose a recovery kit','Open recovery kit'],['Choose a sharing file','Open sharing file']])test(`${button} reaches the existing native file choice without a job`,async({page})=>{
  await page.goto('/');
  const chooser=page.waitForEvent('filechooser');
  await page.getByRole('button',{name:button,exact:true}).click();
  expect(await (await chooser).element().getAttribute('aria-label')).toBe(input);
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('share offers a complete offline kit before separate public publication',async({page})=>{
  let publicReviews=0;
  await page.route('**/api/library*',r=>r.fulfill({json:{...empty,copies:[copy]}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[copy],total:1,stored_total:1,next_offset:null}}));
  await page.route('**/api/desktop-sharing-review',r=>{publicReviews++;return r.fulfill({json:{}});});
  await page.goto('/');await page.getByRole('button',{name:'Share',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Send a complete recovery kit',exact:true})).toBeVisible();
  await expect(page.getByRole('button',{name:'Save kit to send',exact:true})).toBeVisible();
  await expect(page.getByText('The kit is not encrypted and keeps all captured metadata',{exact:false})).toBeVisible();
  expect(publicReviews).toBe(0);
  await page.getByRole('button',{name:'Close sharing choices'}).click();
  await expect(page.getByRole('heading',{name:'Your Library',exact:true})).toBeVisible();
});

test('recipient instructions copy exact keys but never accept them or start work',async({page,context})=>{
  let reads=0;
  const effects:string[]=[];
  page.on('request',r=>{if(/\/api\/desktop-(start|plan|source-follow|sharing-review)/.test(r.url()))effects.push(r.url());});
  await context.grantPermissions(['clipboard-read','clipboard-write']);
  await page.route('**/api/library*',r=>r.fulfill({json:{...empty,copies:[copy]}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[copy],total:1,stored_total:1,next_offset:null}}));
  await page.route('**/api/desktop-copy-recovery-info',r=>{reads++;expect(r.request().postDataJSON()).toEqual({copy_id:id});return r.fulfill({json:{...copy,keys:[key],threshold:1,revoked_keys:[]}});});
  await page.goto('/');await page.getByRole('button',{name:'Recovery instructions',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Compare the exact copy and keys',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Copy recovery instructions',exact:true}).click();
  const instructions=await page.evaluate(()=>navigator.clipboard.readText());
  expect(instructions).toContain(id);expect(instructions).toContain(key);expect(instructions).toContain('separately trusted channel');
  expect(instructions).toContain('Restore files');expect(reads).toBe(1);expect(effects).toEqual([]);
  await expect(page.getByRole('checkbox')).toHaveCount(0);
  await page.getByRole('button',{name:'Library',exact:true}).click();
  await expect(page.getByRole('dialog')).toHaveCount(0);
});

test('low working space is explained before an explicit download can start',async({page})=>{
  let starts=0;
  const model={card_id:'card_'+ 'c'.repeat(52),name:copy.name,format:'gguf',quantization:'Q4',size_bytes:1024,workloads:['TextGeneration'],complete_file_map:true};
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:id,results:[model],total:1,next_offset:null}}));
  await page.route('**/api/desktop-plan',r=>r.fulfill({json:{plan_id:id,kind:'receive',name:copy.name,size_bytes:1024,required_free_bytes:4096,free_bytes:2048,expires_at:9999999999}}));
  await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
  await page.goto('/');await page.getByRole('button',{name:'Keep a copy',exact:true}).click();
  await expect(page.getByText('Free space needed during download',{exact:true})).toBeVisible();
  await expect(page.getByText('There is not enough working space on this device.',{exact:false})).toBeVisible();
  await page.getByLabel('Proceed with this exact private selection.',{exact:true}).check();
  await expect(page.getByRole('button',{name:'Download & preserve',exact:true})).toBeDisabled();expect(starts).toBe(0);
});

test('Getting started covers the active review and closing it preserves consent',async({page})=>{
  const model={card_id:'card_'+ 'c'.repeat(52),name:copy.name,format:'gguf',quantization:'Q4',size_bytes:1024,workloads:['TextGeneration'],complete_file_map:true};
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:id,results:[model],total:1,next_offset:null}}));
  await page.route('**/api/desktop-plan',r=>r.fulfill({json:{plan_id:id,kind:'receive',name:copy.name,size_bytes:1024,expires_at:9999999999}}));
  await page.goto('/');await page.getByRole('button',{name:'Keep a copy',exact:true}).click();
  await page.getByLabel('Proceed with this exact private selection.',{exact:true}).check();
  await page.getByRole('button',{name:'Getting started',exact:true}).click();
  // A real pointer click must reach the visible guide above the older review.
  await page.getByRole('button',{name:'Close getting started',exact:true}).click();
  await expect(page.getByLabel('Proceed with this exact private selection.',{exact:true})).toBeChecked();
  await page.getByRole('button',{name:'Getting started',exact:true}).click();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('heading',{name:'Your model, kept safe',exact:true})).toHaveCount(0);
  await expect(page.getByRole('dialog')).toHaveCount(1);
  await expect(page.getByLabel('Proceed with this exact private selection.',{exact:true})).toBeChecked();
});

test('a sharing-file receive review also refuses low working space after consent',async({page})=>{
  let starts=0;
  await page.route('**/api/desktop-upload-create',r=>r.fulfill({json:{upload_id:'b'.repeat(32),offset:0}}));
  await page.route('**/api/upload/*',r=>r.fulfill({json:{upload_id:'b'.repeat(32),offset:16}}));
  await page.route('**/api/desktop-upload-remove',r=>r.fulfill({json:{removed:true}}));
  await page.route('**/api/desktop-handoff-review',r=>r.fulfill({json:{reference_job_id:id,manifest_id:id,name:copy.name,size_bytes:1024,member_count:2,members:['example/model','example/companion'],keys:[key],threshold:1,revoked_keys:[],mirrors:['https://mirror.example'],from_handoff:true}}));
  await page.route('**/api/desktop-plan',r=>r.fulfill({json:{plan_id:id,kind:'source_receive',name:copy.name,size_bytes:1024,required_free_bytes:4096,free_bytes:2048,expires_at:9999999999}}));
  await page.route('**/api/desktop-start',r=>{starts++;return r.fulfill({json:{}});});
  await page.goto('/');await page.getByLabel('Open sharing file',{exact:true}).setInputFiles({name:'model.foxhandoff',mimeType:'application/json',buffer:Buffer.alloc(16)});
  await page.getByRole('checkbox',{name:/I independently checked/}).check();
  await page.getByRole('button',{name:'Review download & preserve',exact:true}).click();
  await expect(page.getByText('Free space needed during download',{exact:true})).toBeVisible();
  await page.getByLabel('Preserve this exact copy privately. This grants no registry authority or permission to run code.',{exact:true}).check();
  await expect(page.getByRole('button',{name:'Download & preserve',exact:true})).toBeDisabled();expect(starts).toBe(0);
});

test('Getting started also covers public-copy reviews portaled outside the app',async({page})=>{
  await page.route('**/api/library*',r=>r.fulfill({json:{...empty,copies:[copy]}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[copy],total:1,stored_total:1,next_offset:null}}));
  await page.route('**/api/desktop-sharing-read',r=>r.fulfill({json:{public_copies:[]}}));
  await page.route('**/api/desktop-sharing-review',r=>r.fulfill({json:{name:copy.name,already_public:true,members:[]}}));
  await page.goto('/');await page.getByRole('button',{name:'Share',exact:true}).click();
  await page.getByRole('button',{name:'Review public mirror',exact:true}).click();
  await page.getByRole('button',{name:'Review exact public files',exact:true}).waitFor();
  await page.getByRole('button',{name:'Getting started',exact:true}).click();
  await page.getByRole('button',{name:'Close getting started',exact:true}).click();
  await expect(page.getByRole('button',{name:'Review exact public files',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'Getting started',exact:true}).click();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('button',{name:'Review exact public files',exact:true})).toBeVisible();
});
