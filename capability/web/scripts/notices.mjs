import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
const web = new URL('../',import.meta.url);
const target = new URL('public/',web);
mkdirSync(target,{recursive:true});
let notices='Third-party notices for the compiled Model Preservation Library interface.\n\n';
for(const name of ['react','react-dom','scheduler']) {
  const base=new URL(`node_modules/${name}/`,web);
  const pkg=JSON.parse(readFileSync(new URL('package.json',base),'utf8'));
  notices+=`${name} ${pkg.version}\n${readFileSync(new URL('LICENSE',base),'utf8')}\n\n`;
}
notices+='Vite module-preload helper (Vite package license and notices):\n'+readFileSync(new URL('node_modules/vite/LICENSE.md',web),'utf8');
writeFileSync(new URL('THIRD_PARTY_NOTICES.txt',target),notices);
writeFileSync(new URL('LICENSE.txt',target),readFileSync(new URL('../LICENSE',web)));
