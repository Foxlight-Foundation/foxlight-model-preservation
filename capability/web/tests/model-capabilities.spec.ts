import {test,expect} from '@playwright/test';

const id='a'.repeat(64);
const vision={workloads:['TextGeneration'],capabilities:['text','thinking'],purposes:['chat','vision','reasoning'],feature_ids:['vision','reasoning','text'],input_modalities:['text','image'],output_modalities:['text'],claim_sources:['upstream_structured','artifact_manifest'],artifact_notes:['vision-companion-unlisted'],metadata_status:'available'};
const listing=(name:string,details:object,index=0)=>({card_id:'card_'+String.fromCharCode(97+index).repeat(52),name,workloads:['TextGeneration'],details,format:'gguf',quantization:'Q4',size_bytes:1024,complete_file_map:true,download_support:'ready'});

async function setup(page:import('@playwright/test').Page,models:ReturnType<typeof listing>[],saved=false){
 const copies=saved?[{copy_id:id,manifest_id:id,name:models[0].name,size_bytes:1024,member_count:2,details:models[0].details}]:[];
 await page.route('**/api/library*',route=>route.fulfill({json:{host_kind:'sovereign',copies,jobs:[],uploads:[],next_offset:null}}));
 await page.route('**/api/desktop-copy-search',route=>route.fulfill({json:{results:copies,total:copies.length,stored_total:copies.length,next_offset:null}}));
 await page.route('**/api/desktop-source-read',route=>route.fulfill({json:{sources:[]}}));
 await page.route('**/api/desktop-source-search',route=>route.fulfill({json:{results:[],total:0,next_offset:null}}));
 await page.route('**/api/desktop-search',route=>{const purpose=route.request().postDataJSON().purpose;const results=models.filter(model=>!purpose||(model.details as {purposes?:string[]}).purposes?.includes(purpose));return route.fulfill({json:{catalog_id:id,results,total:results.length,next_offset:null}});});
}

test('multimodal uses, input direction, missing companion and attribution are readable',async({page})=>{
 const effects:string[]=[];page.on('request',request=>{if(/desktop-(start|plan|refresh|source-follow)/.test(request.url()))effects.push(request.url());});
 await setup(page,[listing('A text and image model',vision)]);
 await page.goto('/');await page.getByRole('button',{name:'Find models',exact:true}).click();
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:'A text and image model',exact:true})});
 await expect(card.getByText('Understanding images',{exact:true})).toBeVisible();
 await expect(card.getByText('Reasoning & problem solving',{exact:true})).toBeVisible();
 await expect(card.getByText('Can work with pictures as well as text',{exact:false})).toBeVisible();
 await expect(card.locator('.sk-model-io')).toContainText('Works with: written text, pictures');
 await expect(card.locator('.sk-model-io')).toContainText('Produces: written text');
 await expect(card.getByText('its image companion file is not listed with this download',{exact:false})).toBeVisible();
 await card.getByRole('button',{name:'About model inputs and outputs',exact:true}).focus();
 await expect(page.getByRole('tooltip')).toContainText('Understanding a picture and creating a picture are different');
 await card.getByRole('button',{name:'About model inputs and outputs',exact:true}).press('Escape');
 await card.getByText('About this model',{exact:true}).click();
 await expect(card.getByText('Source model information; Files listed by the source',{exact:true})).toBeVisible();
 await card.getByRole('button',{name:'About capability information',exact:true}).click();
 await expect(page.getByRole('tooltip')).toContainText('without proving this selected download includes every file');
 expect(effects).toEqual([]);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('saved Library exposes the same multimodal description without a Core',async({page})=>{
 await setup(page,[listing('Saved image reader',vision)],true);await page.goto('/');
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Saved image reader',exact:true})});
 await expect(page.getByRole('heading',{name:'Your Library',exact:true})).toBeVisible();
 await expect(card.getByText('Understanding images',{exact:true})).toBeVisible();
 await expect(card.locator('.sk-model-io')).toContainText('written text, pictures');
 await expect(card.getByRole('button',{name:'Restore files',exact:true})).toBeVisible();
 await expect(card.getByRole('button',{name:'Save recovery kit',exact:true})).toBeVisible();
});

test('older metadata can describe image-text-to-text without a new host contract',async({page})=>{
 await setup(page,[listing('Portable image reader',{workloads:['image-text-to-text'],purposes:[],metadata_status:'available'})]);
 await page.goto('/');await page.getByRole('button',{name:'Find models',exact:true}).click();
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Portable image reader',exact:true})});
 await expect(card.getByText('Chat & writing',{exact:true})).toBeVisible();
 await expect(card.getByText('Understanding images',{exact:true})).toBeVisible();
 await expect(card.getByText('Useful for asking questions about images',{exact:false})).toBeVisible();
});

test('video reading, image editing and embeddings keep their actual directions',async({page})=>{
 await setup(page,[listing('Clip reader',{purposes:['video-understanding'],feature_ids:['video-understand'],input_modalities:['video'],output_modalities:['text']},0),listing('Picture editor',{purposes:['images'],feature_ids:['image-edit'],input_modalities:['text','image'],output_modalities:['image']},1),listing('Document finder',{purposes:['search'],feature_ids:['embed'],workloads:['TextEmbedding'],capabilities:['text'],input_modalities:['text'],output_modalities:['embedding']},2)]);
 await page.goto('/');await page.getByRole('button',{name:'Find models',exact:true}).click();
 const clip=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Clip reader',exact:true})});
 await expect(clip.getByText('Understanding video',{exact:true})).toBeVisible();
 await expect(clip.getByText('Video generation',{exact:true})).toHaveCount(0);
 const editor=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Picture editor',exact:true})});
 await expect(editor.getByText('Understanding images',{exact:true})).toHaveCount(0);
 await expect(editor.getByText('Changes an existing picture',{exact:false})).toBeVisible();
 const embed=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Document finder',exact:true})});
 await expect(embed.getByText('Chat & writing',{exact:true})).toHaveCount(0);
 await expect(embed.getByText('Helps an app find documents with similar meaning',{exact:false})).toBeVisible();
 await page.getByLabel('What would you like to do?',{exact:true}).selectOption('video-understanding');
 await expect(page.getByRole('heading',{name:'Clip reader',exact:true})).toBeVisible();
 await expect(page.getByRole('heading',{name:'Picture editor',exact:true})).toHaveCount(0);
 expect(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth)).toBe(false);
});

test('unknown descriptor keys stay inert and automated interpretation is named',async({page})=>{
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 await setup(page,[listing('Unfamiliar metadata',{purposes:['vision','constructor'],feature_ids:['vision'],input_modalities:['image','constructor'],output_modalities:['text','toString'],claim_sources:['agent_analysis','constructor']})]);
 await page.goto('/');await page.getByRole('button',{name:'Find models',exact:true}).click();
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Unfamiliar metadata',exact:true})});
 await expect(card.locator('.sk-model-io')).toContainText('Works with: pictures');
 await expect(card.locator('.sk-model-io')).not.toContainText('constructor');
 await card.getByText('About this model',{exact:true}).click();
 await expect(card.getByText('Automated interpretation',{exact:true})).toBeVisible();
 expect(errors).toEqual([]);
});

test('automated component notes stay separate from a model’s usable capabilities',async({page})=>{
 await setup(page,[listing('Image maker',{purposes:['images'],feature_ids:['image-generate'],input_modalities:['text'],output_modalities:['image'],claim_sources:['upstream_structured','agent_analysis'],analysis_notes:['An automated note about a visual component.']})]);
 await page.goto('/');await page.getByRole('button',{name:'Find models',exact:true}).click();
 const card=page.getByRole('article').filter({has:page.getByRole('heading',{name:'Image maker',exact:true})});
 await expect(card.getByText('Understanding images',{exact:true})).toHaveCount(0);
 await expect(card.getByText('Creates pictures from written descriptions.',{exact:true})).toBeVisible();
 await card.getByText('About this model',{exact:true}).click();
 await expect(card.getByText('The capability labels above use the model card and structured source information.',{exact:false})).toBeVisible();
 await card.getByText('Automated review notes',{exact:true}).click();
 await expect(card.getByText('unverified interpretations',{exact:false})).toBeVisible();
 await expect(card.getByText('An automated note about a visual component.',{exact:true})).toBeVisible();
});
