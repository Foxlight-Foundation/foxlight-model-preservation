import {test,expect,type Page} from '@playwright/test';

// Every area keeps stacked blocks apart: no card, note or group touches the one above it.
const copy={copy_id:'a'.repeat(64),manifest_id:'a'.repeat(64),name:'Open text model',size_bytes:1024,member_count:2};
const job={job_id:'b'.repeat(64),kind:'receive',state:'running',name:'big-model',error_code:null,output:null,copy_id:null,created_at:3,stage:'downloading',bytes_done:6_000_000_000,bytes_total:30_000_000_000};
const done={...job,job_id:'c'.repeat(64),state:'succeeded',name:'small-model',copy_id:copy.copy_id};
async function mock(page:Page){
  await page.route('**/api/library*',r=>r.fulfill({json:{host_kind:'sovereign',hub_search:false,pirate_face_search:false,pirate_face_swarm:false,hub_token:null,library:{path:null,system:true,available:true,private:true,free_bytes:100_000_000_000,previous:[],elsewhere_copies:0},copies:[copy],copy_count:1,jobs:[job,done],next_offset:null}}));
  await page.route('**/api/desktop-search',r=>r.fulfill({json:{catalog_id:null,results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-source-read',r=>r.fulfill({json:{sources:[]}}));
  await page.route('**/api/desktop-source-search',r=>r.fulfill({json:{results:[],total:0,next_offset:null}}));
  await page.route('**/api/desktop-copy-search',r=>r.fulfill({json:{results:[copy],total:1,stored_total:1,next_offset:null}}));
  await page.route('**/api/desktop-backup-read',r=>r.fulfill({json:{destinations:[],next_offset:null}}));
  await page.route('**/api/desktop-sharing-read',r=>r.fulfill({json:{public_copies:[]}}));
}
/** Pairs of vertically stacked, visible siblings in main that are under 12px apart. */
async function touching(page:Page,without=false){
  // Without the page rhythm rule: shows the audit catches what Tom saw.
  if(without)await page.evaluate(()=>{const s=document.createElement('style');s.id='no-rhythm';s.textContent='.sk-main > section > * + * {margin-top:0 !important}';document.head.append(s);});
  const found=await page.evaluate(()=>{
    const out:string[]=[];
    const describe=(e:Element)=>`${e.tagName.toLowerCase()}.${[...e.classList].join('.')} “${(e.textContent??'').trim().slice(0,40)}”`;
    for(const parent of document.querySelectorAll('main *, main')){
      const style=getComputedStyle(parent);
      if(style.display.includes('flex')&&!style.flexDirection.startsWith('column'))continue;
      if(style.display.includes('inline'))continue;
      const kids=[...parent.children].filter(e=>{const s=getComputedStyle(e);const r=e.getBoundingClientRect();return r.height>0&&r.width>0&&!s.display.startsWith('inline')&&s.position!=='absolute'&&s.position!=='fixed';});
      for(let i=1;i<kids.length;i++){
        const a=kids[i-1].getBoundingClientRect(),b=kids[i].getBoundingClientRect();
        const stacked=b.top>=a.bottom-1&&Math.min(a.right,b.right)-Math.max(a.left,b.left)>20;
        // Card-like blocks: rounded, framed on every side or filled, and tall.
        const framed=(e:Element)=>{const s=getComputedStyle(e);const r=e.getBoundingClientRect();return !['INPUT','TEXTAREA','SELECT','BUTTON','LABEL'].includes(e.tagName)&&parseFloat(s.borderTopLeftRadius)>=8&&r.height>=40&&((s.borderTopWidth!=='0px'&&s.borderBottomWidth!=='0px')||s.backgroundColor!=='rgba(0, 0, 0, 0)');};
        if(stacked&&b.top-a.bottom<12&&(framed(kids[i-1])||framed(kids[i])))out.push(`${Math.round(b.top-a.bottom)}px: ${describe(kids[i-1])} → ${describe(kids[i])}`);
      }
    }
    return out;
  });
  await page.evaluate(()=>document.getElementById('no-rhythm')?.remove());
  return found;
}
for(const area of ['Find','Library','Activity','Sharing','Settings']){
  test(`${area} keeps framed blocks apart`,async({page})=>{
    await mock(page);
    await page.goto('/');
    if(area!=='Find')await page.getByRole('button',{name:area,exact:true}).first().click();
    await page.waitForTimeout(800);
    const found=await touching(page);
    console.log(`${area}:`,found.length?'\n  '+found.join('\n  '):'ok');
    expect(found).toEqual([]);
  });
}

test('the audit catches cards stacked flush, as they were before the rhythm rule',async({page})=>{
  await mock(page);
  await page.emulateMedia({colorScheme:'dark'});
  await page.goto('/');
  await page.getByRole('button',{name:'Library',exact:true}).first().click();
  await expect(page.getByRole('heading',{name:'Your Library',exact:true})).toBeVisible();
  expect((await touching(page,true)).length).toBeGreaterThan(0);
  expect(await touching(page)).toEqual([]);
});
