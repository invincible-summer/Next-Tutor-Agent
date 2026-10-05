/** Project-authored open book and learning path, shared by the mobile app icons. */
import {createRequire} from "node:module";
import {mkdir,writeFile} from "node:fs/promises";
import path from "node:path";
import {fileURLToPath} from "node:url";
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),"../..");
const require=createRequire(path.join(root,"apps/mobile/package.json"));
const sharp=require("sharp");
const out=path.join(root,"apps/mobile/assets/brand");
function bookSvg(size,scale=0.7){
 const margin=(size-size*scale)/2;
 return `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"><g transform="translate(${margin},${margin}) scale(${size*scale/100})">
 <rect x="8" y="7" width="84" height="86" rx="26" fill="#E3EFEB"/>
 <path d="M50 79C40 71 28 70 15 73V34C29 31 42 34 50 41C58 34 71 31 85 34V73C72 70 60 71 50 79Z" fill="#256D66"/>
 <path d="M50 43V72M25 44C31 43 38 45 42 48M25 54C31 53 38 55 42 58" stroke="#FFFDF8" stroke-width="4.5" fill="none" stroke-linecap="round"/>
 <path d="M60 59C73 53 68 31 80 22" stroke="#73B8A6" stroke-width="6" fill="none" stroke-linecap="round"/>
 <circle cx="81" cy="21" r="6" fill="#256D66"/>
 </g></svg>`;
}
await mkdir(out,{recursive:true});
await writeFile(path.join(out,"mark.svg"),bookSvg(512,0.84));
const icon=await sharp({create:{width:1024,height:1024,channels:4,background:"#F6F5F1"}}).composite([{input:Buffer.from(bookSvg(1024,0.68))}]).png().toBuffer();
await writeFile(path.join(out,"icon.png"),icon);
await sharp(Buffer.from(bookSvg(1024,0.6))).png().toFile(path.join(out,"adaptive-icon.png"));
await sharp(Buffer.from(bookSvg(512,0.84))).png().toFile(path.join(out,"splash-icon.png"));
console.log("Generated mobile book and learning path assets.");
