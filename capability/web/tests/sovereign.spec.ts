import {test,expect} from '@playwright/test';
const id='a'.repeat(64),card='card_'+ 'a'.repeat(52);
const model={card_id:card,name:'example/Portable model',format:'gguf',quantization:'Q4',size_bytes:2048,workloads:['TextGeneration'],complete_file_map:true};
const empty={host_kind:'sovereign',locations:[],models:[],copies:[],jobs:[],next_offset:null};

test.beforeEach(async({page})=>{
 await page.route('**/api/desktop-source-read',route=>route.fulfill({json:{sources:[]}}));
 await page.route('**/api/desktop-source-search',route=>route.fulfill({json:{results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-copy-search',route=>route.fulfill({json:{results:[],total:0,stored_total:0,next_offset:null}}));
});

test('returning users start in Library and can release private kit transfers',async({page})=>{
 const copy={copy_id:id,manifest_id:id,name:'My saved model',size_bytes:16,member_count:2};
 await page.route('**/api/desktop-copy-search',route=>route.fulfill({json:{results:[copy],total:1,stored_total:1,next_offset:null}}));
 let removed=false;
 await page.route('**/api/library*',route=>route.fulfill({json:{...empty,copies:[copy],uploads:removed?[]:[{upload_id:'b'.repeat(32),size_bytes:16,offset:16,complete:true}]}}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-upload-remove',route=>{expect(route.request().postDataJSON()).toEqual({upload_id:'b'.repeat(32)});removed=true;return route.fulfill({json:{removed:true}});});
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'Your Library',exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Activity',exact:true}).click();
 await expect(page.getByText('Removing a transfer keeps your original file, saved models and completed kits.',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'Remove transfer',exact:true}).click();
 await expect(page.getByRole('button',{name:'Remove transfer',exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'Library',exact:true}).click();
 await expect(page.getByRole('heading',{name:copy.name,exact:true})).toBeVisible();
});

test('a kit selected from older activity still opens its trust review',async({page})=>{
 const calls:number[]=[];
 await page.route('**/api/library*',route=>{const offset=Number(new URL(route.request().url()).searchParams.get('offset')??0);calls.push(offset);return route.fulfill({json:{...empty,next_offset:offset===0?48:null}});});
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-upload-create',route=>route.fulfill({json:{upload_id:'b'.repeat(32),offset:0,size_bytes:16}}));
 await page.route('**/api/upload/*',route=>route.fulfill({json:{upload_id:'b'.repeat(32),offset:16,size_bytes:16,complete:true}}));
 await page.route('**/api/desktop-plan',route=>route.fulfill({json:{plan_id:id,kind:'kit_import',name:'From older activity',manifest_id:id,size_bytes:16,member_count:2,threshold:1,keys:['ed25519:'+id],expires_at:9999999999}}));
 await page.goto('/');await page.getByRole('button',{name:'Activity',exact:true}).click();
 await page.getByRole('button',{name:'Older activity',exact:true}).click();
 await expect(page.getByRole('button',{name:'Newer activity',exact:true})).toBeVisible();
 await page.getByLabel('Open recovery kit',{exact:true}).setInputFiles({name:'saved.foxkit',mimeType:'application/octet-stream',buffer:Buffer.alloc(16)});
 await expect(page.getByRole('button',{name:'Import verified copy',exact:true})).toBeDisabled();
 await expect(page.getByText('From older activity',{exact:true})).toBeVisible();
 expect(calls).toContain(48);expect(calls.at(-1)).toBe(0);
});
test('standalone first launch browses preset without Core or bootstrap hunting',async({page})=>{
 let ready=false,updates=0;const requests:string[]=[];
 page.on('request',request=>{if(request.url().includes('/api/'))requests.push(request.url());});
 await page.route('**/api/library*',route=>route.fulfill({json:empty}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:ready?id:null,results:ready?[model]:[],total:ready?1:0,next_offset:null}}));
 await page.route('**/api/desktop-refresh',route=>{ready=true;updates++;return route.fulfill({json:{}});});
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'Find a model. Keep your own copy.'})).toBeVisible();
 await expect(page.getByText('No account or source-file setup.')).toBeVisible();
 expect(updates).toBe(0);
 await page.getByRole('button',{name:'Browse public models'}).first().click();
 // No leftover notice: the listings themselves are the feedback.
 await expect(page.getByText('Listing update started',{exact:false})).toHaveCount(0);
 await page.getByLabel('Search models',{exact:true}).fill('Portable');
 await expect(page.getByRole('heading',{name:model.name,exact:true})).toBeVisible();
 expect(updates).toBe(1);expect(requests.some(url=>url.includes('core-features')||url.includes('source-follow'))).toBe(false);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});
test('Keep a copy in the review is the confirmation and stays receive-only',async({page})=>{
 let submitted:Record<string,unknown>|null=null;
 await page.route('**/api/library*',route=>route.fulfill({json:empty}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:id,results:[model],total:1,next_offset:null}}));
 await page.route('**/api/desktop-plan',route=>route.fulfill({json:{plan_id:id,kind:'receive',name:model.name,card_id:card,size_bytes:4096,file_count:2,projector_included:true,members:['example/model','example/required-companion'],expires_at:9999999999}}));
 await page.route('**/api/desktop-start',route=>{submitted=route.request().postDataJSON();return route.fulfill({json:{}});});
 await page.goto('/');
 // Each file version says, in plain words, which apps can open it.
 await expect(page.getByText('For apps such as LM Studio, Ollama and Jan, on most computers.',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Keep a copy',exact:true}).click();
 await expect(page.getByText('example/required-companion',{exact:true})).toBeVisible();
 await expect(page.getByText('Included with this copy · needed for picture inputs',{exact:true})).toBeVisible();
 await expect(page.getByText('Files',{exact:true})).toBeVisible();
 // No second "proceed" box: choosing Keep a copy in the review is the confirmation.
 await expect(page.getByRole('dialog').getByRole('checkbox')).toHaveCount(0);
 const start=page.getByRole('dialog').getByRole('button',{name:'Keep a copy',exact:true});await expect(start).toBeEnabled();
 expect(submitted).toBeNull();await start.click();
 await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();
 expect(submitted).toEqual({plan_id:id,operation_id:expect.any(String),confirmed:true});
});
test('a missing signed companion stays browsable with an explained disabled action',async({page})=>{
 let plans=0;
 await page.route('**/api/library*',route=>route.fulfill({json:empty}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:id,results:[{...model,download_support:'complete_signed_companion_map_required'}],total:1,next_offset:null}}));
 await page.route('**/api/desktop-plan',route=>{plans++;return route.fulfill({json:{}});});
 await page.goto('/');
 await expect(page.getByRole('heading',{name:model.name,exact:true})).toBeVisible();
 await expect(page.getByRole('button',{name:'Keep a copy',exact:true})).toBeDisabled();
 await expect(page.getByText('Sovereign will not save an incomplete copy.',{exact:false})).toBeVisible();
 expect(plans).toBe(0);
});
test('source authority is not granted by accepting an imported kit key',async({page})=>{
 await page.route('**/api/library*',route=>route.fulfill({json:empty}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-upload-create',route=>route.fulfill({json:{upload_id:'b'.repeat(32),offset:0,size_bytes:16}}));
 await page.route('**/api/upload/*',route=>route.fulfill({json:{upload_id:'b'.repeat(32),offset:16,size_bytes:16,complete:true}}));
 await page.route('**/api/desktop-plan',route=>route.fulfill({json:{plan_id:id,kind:'kit_import',name:'A portable kit',manifest_id:id,size_bytes:16,member_count:2,threshold:2,keys:['ed25519:'+id],expires_at:9999999999}}));
 await page.goto('/');await page.getByLabel('Open recovery kit',{exact:true}).setInputFiles({name:'saved.foxkit',mimeType:'application/octet-stream',buffer:Buffer.alloc(16)});
 await expect(page.getByText('A kit can verify itself without proving who created it.',{exact:false})).toBeVisible();
 await expect(page.getByRole('button',{name:'Import verified copy'})).toBeDisabled();
 await page.getByLabel('I checked and accept these keys for this exact capture.').check();
 await expect(page.getByRole('button',{name:'Import verified copy'})).toBeEnabled();
});

test('saved-copy kit action prepares a download without regranting model trust',async({page})=>{
 let started=false;
 const copy={copy_id:id,manifest_id:id,name:'Kept model',size_bytes:16,member_count:2};
 await page.route('**/api/desktop-copy-search',route=>route.fulfill({json:{results:[copy],total:1,stored_total:1,next_offset:null}}));
 const job={job_id:id,kind:'kit_export',state:'succeeded',name:copy.name,error_code:null,output:null,copy_id:id};
 // A busy or size-bounded Activity page need not include this exact job.
 await page.route('**/api/library*',route=>route.fulfill({json:{...empty,copies:[copy],jobs:[]}}));
 await page.route('**/api/desktop-job-read',route=>{expect(started).toBe(true);expect(route.request().postDataJSON()).toEqual({job_id:id});return route.fulfill({json:job});});
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-plan',route=>route.fulfill({json:{plan_id:id,kind:'kit_export',name:copy.name,size_bytes:16,member_count:2,expires_at:9999999999}}));
 await page.route('**/api/desktop-start',route=>{started=true;expect(route.request().postDataJSON()).toEqual({plan_id:id,operation_id:expect.any(String),confirmed:true});return route.fulfill({json:job});});
 await page.route('**/api/desktop-kit/*',route=>route.fulfill({headers:{'Content-Disposition':'attachment; filename="saved.foxkit"'},contentType:'application/octet-stream',body:Buffer.alloc(16)}));
 await page.goto('/');await page.getByRole('button',{name:'Library',exact:true}).click();
 const download=page.waitForEvent('download');await page.getByRole('button',{name:'Save recovery kit',exact:true}).click();
 await download;await expect(page.getByRole('heading',{name:'Activity',exact:true})).toBeVisible();
 await expect(page.getByRole('status')).toContainText('Your recovery kit is ready.');
 await expect(page.getByRole('status')).not.toContainText('Preparing your complete recovery kit.');
 await expect(page.getByLabel('Proceed with this exact private selection.')).toHaveCount(0);
});

const shot=(name:string,info:{project:{name:string};outputPath:(name:string)=>string})=>process.env.LIBRARY_SCREENSHOTS?`${process.env.LIBRARY_SCREENSHOTS}/${info.project.name}-${name}.png`:info.outputPath(`${name}.png`);
const gatedTerms={definition:1,accept:['llama3.1'],licences:[{license_id:'llama3.1',name:'Llama 3.1 Community License',family:'custom',summary:'Meta’s acceptable use policy applies; attribution “Built with Llama” required; copies must include the licence.',requires_acceptance:true,references:[{members:['weights'],reference:'LICENSE',url:'https://huggingface.co/meta-llama/Llama-3.1-8B/blob/'+'b'.repeat(40)+'/LICENSE'}]},{license_id:'mit',name:'MIT',family:'permissive',summary:'Anyone may copy and share, keeping the licence and copyright notice.',requires_acceptance:false,references:[{members:['tokenizer'],reference:'LICENSE',url:null}]}]};
test('a model under use-restricted terms is kept only after I accept these terms',async({page},info)=>{
 let submitted:Record<string,unknown>|null=null;
 await page.route('**/api/library*',route=>route.fulfill({json:empty}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:id,results:[model],total:1,next_offset:null}}));
 await page.route('**/api/desktop-plan',route=>route.fulfill({json:{plan_id:id,kind:'receive',name:model.name,card_id:card,size_bytes:4096,file_count:2,members:['example/model'],expires_at:9999999999,terms:gatedTerms}}));
 await page.route('**/api/desktop-start',route=>{submitted=route.request().postDataJSON();return route.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Keep a copy',exact:true}).click();
 const terms=page.getByLabel('Licence terms');
 await expect(terms.getByText('Llama 3.1 Community License',{exact:true})).toBeVisible();
 await expect(terms.getByText(/Built with Llama/)).toBeVisible();
 await expect(terms.getByRole('link',{name:/Read the full terms \(LICENSE, carried with the model\)/})).toHaveAttribute('href',/huggingface\.co\/meta-llama\/Llama-3\.1-8B\/blob\/b{40}\/LICENSE$/);
 await expect(terms.getByText('isn’t legal advice',{exact:false})).toBeVisible();
 const start=page.getByRole('dialog').getByRole('button',{name:'Keep a copy',exact:true});
 // Terms that need acceptance gate the download, as on Hugging Face.
 await expect(start).toBeDisabled();
 // The acceptance is its own section, names the licence, and only gated entries carry the badge.
 const accept=terms.getByRole('region',{name:'Terms to accept'});
 await expect(accept.getByRole('heading',{name:'Terms to accept'})).toBeVisible();
 await expect(terms.getByText('Needs your acceptance',{exact:true})).toHaveCount(1);
 await expect(terms.getByRole('listitem').filter({hasText:'Llama 3.1 Community License'}).getByText('Needs your acceptance')).toBeVisible();
 await expect(terms.getByRole('listitem').filter({hasText:'MIT'}).getByText('Needs your acceptance')).toHaveCount(0);
 await accept.getByRole('checkbox',{name:'I accept the Llama 3.1 Community License.'}).scrollIntoViewIfNeeded();
 await page.screenshot({path:shot('terms-acceptance',info)});
 await accept.getByRole('checkbox',{name:'I accept the Llama 3.1 Community License.'}).check();
 await start.click();
 expect(submitted).toEqual({plan_id:id,operation_id:expect.any(String),confirmed:true,accepted_terms:['llama3.1']});
});
test('plain permissive terms are shown but ask for no acceptance',async({page})=>{
 let submitted:Record<string,unknown>|null=null;
 const permissive={definition:1,accept:[],licences:[{license_id:'apache-2.0',name:'Apache License 2.0',family:'permissive',summary:'Anyone may copy and share, keeping the licence and copyright notice.',requires_acceptance:false,references:[{members:['weights'],reference:'LICENSE',url:null}]}]};
 await page.route('**/api/library*',route=>route.fulfill({json:empty}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:id,results:[model],total:1,next_offset:null}}));
 await page.route('**/api/desktop-plan',route=>route.fulfill({json:{plan_id:id,kind:'receive',name:model.name,card_id:card,size_bytes:4096,members:['example/model'],expires_at:9999999999,terms:permissive}}));
 await page.route('**/api/desktop-start',route=>{submitted=route.request().postDataJSON();return route.fulfill({json:{}});});
 await page.goto('/');await page.getByRole('button',{name:'Keep a copy',exact:true}).click();
 await expect(page.getByLabel('Licence terms').getByRole('checkbox')).toHaveCount(0);
 await expect(page.getByText('Needs your acceptance',{exact:true})).toHaveCount(0);
 await page.getByRole('dialog').getByRole('button',{name:'Keep a copy',exact:true}).click();
 expect(submitted).toEqual({plan_id:id,operation_id:expect.any(String),confirmed:true});
});

test('getting listings for the first time shows a loader, not a silent wait',async({page})=>{
 let refreshing=false;
 const job={job_id:'c'.repeat(32),kind:'catalog_refresh',state:'running',created_at:0,updated_at:0};
 await page.route('**/api/library*',route=>route.fulfill({json:{host_kind:'sovereign',copies:[],jobs:refreshing?[job]:[],next_offset:null}}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-refresh',route=>{refreshing=true;return route.fulfill({json:{}});});
 await page.goto('/');
 await page.getByRole('button',{name:'Browse public models'}).first().click();
 await expect(page.getByRole('status').filter({hasText:'Getting model listings from Foxlight’s registry'})).toBeVisible();
});

test('a listing that names no licence asks to follow the model’s own terms, not to accept a non-licence',async({page})=>{
 const unnamed={definition:1,accept:['see-original-card'],licences:[{license_id:'see-original-card',name:'Licence not named',family:null,summary:'This listing doesn’t say which licence applies. Read the model’s own terms before using it.',requires_acceptance:true,references:[{members:['example/model'],reference:'https://huggingface.co/example/model/tree/'+'b'.repeat(40),url:'https://huggingface.co/example/model/tree/'+'b'.repeat(40)}]}]};
 await page.route('**/api/library*',route=>route.fulfill({json:empty}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:id,results:[model],total:1,next_offset:null}}));
 await page.route('**/api/desktop-plan',route=>route.fulfill({json:{plan_id:id,kind:'receive',name:model.name,card_id:card,size_bytes:4096,file_count:2,members:['example/model'],expires_at:9999999999,terms:unnamed}}));
 await page.goto('/');await page.getByRole('button',{name:'Keep a copy',exact:true}).click();
 const terms=page.getByLabel('Licence terms');
 await expect(terms.getByText('Licence not named',{exact:true})).toBeVisible();
 // The internal marker is never shown as if it were a licence identifier.
 await expect(terms.getByText('see-original-card')).toHaveCount(0);
 await expect(terms.getByRole('checkbox',{name:'I’ll read and follow the model’s own terms before using it.'})).toBeVisible();
 await expect(page.getByText('I accept the Licence not named',{exact:false})).toHaveCount(0);
});

test('an Activity entry named after its job does not repeat that name',async({page})=>{
 const job={job_id:'d'.repeat(32),kind:'catalog_refresh',state:'succeeded',name:'Update public model listings',error_code:null,output:null,copy_id:null};
 await page.route('**/api/library*',route=>route.fulfill({json:{...empty,jobs:[job]}}));
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:id,results:[model],total:1,next_offset:null}}));
 await page.goto('/');await page.getByRole('button',{name:'Activity',exact:true}).click();
 await expect(page.getByText('Update public model listings',{exact:true})).toHaveCount(1);
});
