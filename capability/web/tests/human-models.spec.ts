import {test,expect} from '@playwright/test';

const id='a'.repeat(64),second='b'.repeat(64);
const writing={description:'A small writing model for drafting and summarizing text.',publisher:'Example publisher',repository:'example/writing-model',base_model:'Example Base',workloads:['TextGeneration'],purposes:['chat'],format:'gguf',quantization:'Q4',context_length:8192,languages:['English'],license:'Apache-2.0',metadata_status:'available'};
const imaging={description:'Creates images from a text description.',workloads:['TextToImage'],purposes:['images'],format:'safetensors',metadata_status:'available'};
const copies=[{copy_id:id,manifest_id:id,name:'My writing model',size_bytes:1024,member_count:2,details:writing},{copy_id:second,manifest_id:second,name:'My image model',size_bytes:2048,member_count:1,details:imaging}];
const cards=copies.map((copy,index)=>({card_id:'card_'+(index?'b':'a').repeat(52),name:copy.name,workloads:copy.details.workloads,format:copy.details.format,quantization:'',size_bytes:copy.size_bytes,publisher:'example',details:copy.details,download_support:'ready'}));

test.beforeEach(async({page})=>{
 await page.route('**/api/desktop-copy-search',route=>{const body=route.request().postDataJSON();const found=copies.filter(copy=>(!body.purpose||copy.details.purposes.includes(body.purpose))&&(!body.query||`${copy.name} ${copy.details.description} ${copy.details.purposes.includes('chat')?'email':''}`.toLowerCase().includes(body.query.toLowerCase())));return route.fulfill({json:{results:found,total:found.length,stored_total:copies.length,next_offset:null}});});
 await page.route('**/api/desktop-source-read',route=>route.fulfill({json:{sources:[]}}));
 await page.route('**/api/desktop-source-search',route=>route.fulfill({json:{results:[],total:0,next_offset:null}}));
 await page.route('**/api/library*',route=>route.fulfill({json:{host_kind:'sovereign',copies,jobs:[],uploads:[],next_offset:null}}));
 await page.route('**/api/desktop-search',route=>{const body=route.request().postDataJSON();const found=cards.filter(card=>!body.purpose||card.details.purposes.includes(body.purpose));return route.fulfill({json:{catalog_id:id,results:found,total:found.length,next_offset:null}});});
});

test('saved models explain their use, details, search and the two recovery actions',async({page})=>{
 const effects:string[]=[];
 page.on('request',request=>{if(/desktop-(start|plan|refresh|source-follow)/.test(request.url()))effects.push(request.url());});
 await page.goto('/');
 await expect(page.getByRole('heading',{name:'Your Library',exact:true})).toBeVisible();
 await expect(page.getByText(writing.description,{exact:true})).toBeVisible();
 await expect(page.getByText('Keep it safe:',{exact:true})).toBeVisible();
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:copies[0].name,exact:true})});
 await card.getByText('About this model',{exact:true}).click();
 await expect(card.getByText(writing.repository,{exact:true})).toBeVisible();
 await expect(card.getByText('8,192 tokens, stated in card',{exact:true})).toBeVisible();
 await expect(card.getByText(writing.license,{exact:true})).toBeVisible();
 await page.getByLabel('Search your saved models',{exact:true}).fill('email');
 await expect(page.getByRole('heading',{name:copies[0].name,exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:copies[1].name,exact:true})).toHaveCount(0);
 await page.getByLabel('Search your saved models',{exact:true}).fill('');
 await page.getByLabel('What would you like to do?',{exact:true}).selectOption('images');
 await expect(page.getByRole('heading',{name:copies[1].name,exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:copies[0].name,exact:true})).toHaveCount(0);
 await page.getByLabel('Search your saved models',{exact:true}).fill('no such model');
 await expect(page.getByRole('heading',{name:'No saved models match',exact:true})).toBeVisible();
 expect(effects).toEqual([]);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('technical identity is explained, exact, and help works with focus, Escape and touch',async({page})=>{
 await page.goto('/');
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:copies[0].name,exact:true})});
 await expect(card.getByText(id,{exact:true})).not.toBeVisible();
 await card.getByText('Verification details',{exact:true}).click();
 await expect(card.getByText('Digital fingerprint',{exact:true})).toBeVisible();
 await expect(card.getByText(id,{exact:true})).toBeVisible();
 const help=card.getByRole('button',{name:'About digital fingerprints',exact:true});
 await help.focus();
 await expect(page.getByRole('tooltip')).toContainText('You usually do not need to read it');
 await help.press('Escape');
 await expect(page.getByRole('tooltip')).toHaveCount(0);
 await help.click();
 await expect(page.getByRole('tooltip')).toBeVisible();
 const tip=await page.getByRole('tooltip').boundingBox(),viewport=page.viewportSize()!;
 expect(tip).not.toBeNull();expect(tip!.x).toBeGreaterThanOrEqual(0);expect(tip!.x+tip!.width).toBeLessThanOrEqual(viewport.width);expect(tip!.y+tip!.height).toBeLessThanOrEqual(viewport.height);
 await help.click();await expect(page.getByRole('tooltip')).toHaveCount(0);
 await help.click();await expect(page.getByRole('tooltip')).toBeVisible();
 await help.press('Tab');
 await expect(page.getByRole('tooltip')).toHaveCount(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('Find uses ordinary purposes without treating file size or a signature as device suitability',async({page})=>{
 await page.goto('/');await page.getByRole('button',{name:'Find',exact:true}).click();
 await expect(page.getByText(writing.description,{exact:true})).toBeVisible();
 await page.getByLabel('What would you like to do?',{exact:true}).selectOption('images');
 await expect(page.getByRole('heading',{name:copies[1].name,exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:copies[0].name,exact:true})).toHaveCount(0);
 await page.getByRole('button',{name:'About download size',exact:true}).focus();
 await expect(page.getByRole('tooltip')).toContainText('not the memory needed');
 await page.getByRole('button',{name:'About download size',exact:true}).press('Escape');
 await page.getByText('Verification details',{exact:true}).click();
 await expect(page.getByText('it does not rate model quality or prove it will run on your device.',{exact:false})).toBeVisible();
});

test('missing card metadata stays honest and untrusted descriptions render as text',async({page})=>{
 const malicious='<img src="https://unknown.invalid/pixel" onerror="alert(1)">';
 await page.route('**/api/desktop-copy-search',route=>route.fulfill({json:{results:[{...copies[0],details:{description:malicious,metadata_status:'available'}},{...copies[1],details:{metadata_status:'unavailable'}}],total:2,stored_total:2,next_offset:null}}));
 await page.route('**/api/library*',route=>route.fulfill({json:{host_kind:'sovereign',copies:[{...copies[0],details:{description:malicious,metadata_status:'available'}},{...copies[1],details:{metadata_status:'unavailable'}}],jobs:[],next_offset:null}}));
 await page.goto('/');
 await expect(page.getByText(malicious,{exact:true})).toBeVisible();
 expect(await page.locator('img[src*="unknown.invalid"]').count()).toBe(0);
 await expect(page.getByText('Purpose not described',{exact:true})).toHaveCount(2);
 await expect(page.getByText('This copy has no readable description yet.',{exact:false})).toBeVisible();
});

test('matching file variants share a description but retain their own exact review',async({page})=>{
 await page.addInitScript(()=>{const request=window.requestAnimationFrame.bind(window),cancel=window.cancelAnimationFrame.bind(window),frames=new Map<number,FrameRequestCallback>();window.requestAnimationFrame=callback=>{const id=request(()=>{});frames.set(id,callback);return id;};window.cancelAnimationFrame=id=>{frames.delete(id);cancel(id);};(window as Window&{testFocusFrames:{count:()=>number;flush:()=>void}}).testFocusFrames={count:()=>frames.size,flush:()=>{const callbacks=[...frames.values()];frames.clear();callbacks.forEach(callback=>callback(performance.now()));}};});
 const versions=[{...cards[0],quantization:'Q4',file_label:'writing-Q4.gguf',details:{...writing,quantization:'Q4'}},{...cards[0],card_id:'card_'+'b'.repeat(52),quantization:'Q8',size_bytes:4096,file_label:'writing-Q8.gguf',details:{...writing,quantization:'Q8'}}];
 let selected:string|undefined;
 await page.route('**/api/desktop-search',route=>route.fulfill({json:{catalog_id:id,results:versions,total:2,next_offset:null}}));
 await page.route('**/api/desktop-plan',route=>{selected=route.request().postDataJSON().card_id;return route.fulfill({json:{plan_id:id,kind:'receive',name:cards[0].name,card_id:selected,size_bytes:4096,members:['model'],expires_at:9999999999}});});
 await page.goto('/');await page.getByRole('button',{name:'Find',exact:true}).click();
 await expect(page.getByRole('heading',{name:cards[0].name,exact:true})).toHaveCount(1);
 await expect(page.getByText('2 file versions on this page',{exact:false})).toBeVisible();
 await page.getByText('Choose a file version · 2 on this page',{exact:true}).click();
 await expect(page.getByText('writing-Q4.gguf',{exact:true})).toBeVisible();
 await expect(page.getByText('writing-Q8.gguf',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'Keep a copy',exact:true}).nth(1).click();
 expect(selected).toBe(versions[1].card_id);
 await expect(page.getByRole('button',{name:'Download & preserve',exact:true})).toBeDisabled();
 const dialog=page.getByRole('dialog'),help=dialog.getByRole('button',{name:'About digital fingerprints',exact:true});
 await expect.poll(()=>page.evaluate(()=>(window as Window&{testFocusFrames:{count:()=>number}}).testFocusFrames.count())).toBeGreaterThan(0);
 await help.focus();await page.evaluate(()=>(window as Window&{testFocusFrames:{flush:()=>void}}).testFocusFrames.flush());await expect(help).toBeFocused();await expect(page.getByRole('tooltip')).toBeVisible();
 await help.press('Escape');await expect(dialog).toBeVisible();await expect(page.getByRole('tooltip')).toHaveCount(0);
 // Hover does not move focus. Escape dismisses the viewport-level help first.
 await dialog.getByRole('heading',{name:'Download & preserve',exact:true}).focus();
 await help.hover();await expect(page.getByRole('tooltip')).toBeVisible();
 const tip=await page.getByRole('tooltip').boundingBox(),viewport=page.viewportSize()!;
 expect(tip!.x).toBeGreaterThanOrEqual(0);expect(tip!.x+tip!.width).toBeLessThanOrEqual(viewport.width);expect(tip!.y+tip!.height).toBeLessThanOrEqual(viewport.height);
 await page.keyboard.press('Escape');await expect(dialog).toBeVisible();await expect(page.getByRole('tooltip')).toHaveCount(0);
 await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
});

test('all saved models remain searchable across size-bounded Library pages',async({page})=>{
 const offsets:number[]=[];
 await page.route('**/api/library*',route=>route.fulfill({json:{host_kind:'sovereign',copy_count:256,copies:[copies[0]],jobs:[],next_offset:null}}));
 await page.route('**/api/desktop-copy-search',route=>{const body=route.request().postDataJSON();offsets.push(body.offset);const filtered=Boolean(body.query);return route.fulfill({json:{results:[filtered?{...copies[1],name:'Copy255'}:copies[0]],total:filtered?1:256,stored_total:256,next_offset:filtered?null:body.offset<26?body.offset+13:null}});});
 await page.goto('/');
 await expect(page.getByText('256 matches among 256 saved models',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'More saved models',exact:true}).click();await expect.poll(()=>offsets.at(-1)).toBe(13);
 await page.getByRole('button',{name:'More saved models',exact:true}).click();await expect.poll(()=>offsets.at(-1)).toBe(26);
 await page.getByRole('button',{name:'Previous saved models',exact:true}).click();await expect.poll(()=>offsets.at(-1)).toBe(13);
 await page.getByLabel('Search your saved models',{exact:true}).fill('Copy255');
 await expect(page.getByRole('heading',{name:'Copy255',exact:true})).toBeVisible();await expect.poll(()=>offsets.at(-1)).toBe(0);
 await expect(page.getByRole('button',{name:'Restore files',exact:true})).toBeVisible();
 await expect(page.getByText('1 matches among 256 saved models',{exact:false})).toBeVisible();
});

test('size-bounded catalog pages return to their actual previous offset',async({page})=>{
 const offsets:number[]=[];
 await page.route('**/api/desktop-search',route=>{const offset=route.request().postDataJSON().offset;offsets.push(offset);return route.fulfill({json:{catalog_id:id,results:[cards[0]],total:60,next_offset:offset<26?offset+13:null}});});
 await page.goto('/');await page.getByRole('button',{name:'Find',exact:true}).click();
 await page.getByRole('button',{name:'More matching versions',exact:true}).click();
 await expect.poll(()=>offsets.at(-1)).toBe(13);
 await page.getByRole('button',{name:'More matching versions',exact:true}).click();
 await expect.poll(()=>offsets.at(-1)).toBe(26);
 await page.getByRole('button',{name:'Previous',exact:true}).click();
 await expect.poll(()=>offsets.at(-1)).toBe(13);
});

test('Activity describes the actual stage without inventing a percentage',async({page})=>{
 await page.route('**/api/library*',route=>route.fulfill({json:{host_kind:'sovereign',copies:[],jobs:[{job_id:id,kind:'receive',state:'running',stage:'checking',name:'Writing model',bytes_received:1024,error_code:null,output:null,copy_id:null}],active_count:1,next_offset:null}}));
 await page.goto('/');await page.getByRole('button',{name:/^Activity/}).click();
 await expect(page.getByRole('status')).toContainText('Checking the files and signatures');
 await expect(page.getByRole('progressbar')).not.toHaveAttribute('aria-valuenow');
 await expect(page.getByText('1.0 KB received',{exact:true})).toBeVisible();
});


test('other model sources are one visible jump away without any network enrollment',async({page})=>{
 const effects:string[]=[];page.on('request',request=>{if(/desktop-(source-follow|source-plan|refresh|start)/.test(request.url()))effects.push(request.url());});
 await page.goto('/');await page.getByRole('button',{name:'Find',exact:true}).click();
 await page.getByRole('link',{name:'Open sharing file or source',exact:true}).click();
 await expect(page.getByRole('region',{name:'Independent model sources',exact:true})).toBeInViewport();
 await expect(page.getByRole('region',{name:'Independent model sources',exact:true})).toBeFocused();
 expect(effects).toEqual([]);
});


test('hover help stays readable while the pointer crosses to its explanation',async({page})=>{
 await page.goto('/');const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:copies[0].name,exact:true})});
 await card.getByText('Verification details',{exact:true}).click();
 await page.getByRole('heading',{name:'Your Library',exact:true}).click();
 const help=card.getByRole('button',{name:'About digital fingerprints',exact:true});
 await help.hover();const tip=page.getByRole('tooltip');await tip.hover();
 // Outlast the intentional crossing delay while hovering the explanation itself.
 await page.waitForTimeout(300);await expect(tip).toBeVisible();
 await page.mouse.move(0,0);await expect(tip).toHaveCount(0);
});
