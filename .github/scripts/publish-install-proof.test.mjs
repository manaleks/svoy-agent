import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtempSync,writeFileSync,readFileSync,readdirSync,rmSync,realpathSync,symlinkSync,mkdirSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {publicInstallSummary,publishInstallProof} from './publish-install-proof.mjs';
const privateText='PRIVATE_SOURCE_AND_TOKEN_MUST_STAY_LOCAL';
const input={status:'failed',commit:'a'.repeat(40),sourceSHA256:'b'.repeat(64),platform:'linux-x64',workingTree:false,error:{message:privateText},logs:[privateText],checks:[{name:'clean-install',result:'passed',detail:privateText}]};
test('public result contains only closed fields and never arbitrary error or detail text',()=>{
  const result=publicInstallSummary(input);
  assert.equal(JSON.stringify(result).includes(privateText),false);
  assert.deepEqual(result.checks,[{name:'clean-install',result:'passed'}]);
  for(const delta of [{checks:[{name:'private-secret',result:'passed'}]},{checks:[...input.checks,...input.checks]},{commit:privateText},{sourceSHA256:privateText},{platform:privateText},{checks:[{name:privateText,result:'passed'}]}])assert.throws(()=>publicInstallSummary({...input,...delta}));
});
test('export ignores diagnostic files even when they contain private source',()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'install-public-proof-')));
 try{
  writeFileSync(join(root,'install-cycle-linux.json'),JSON.stringify(input));
  writeFileSync(join(root,'install-diagnostic-linux.json'),JSON.stringify({logs:privateText}));
  const target=join(root,'public');publishInstallProof(root,target);
  assert.deepEqual(readdirSync(target),['install-cycle-linux.json']);
  assert.equal(readFileSync(join(target,'install-cycle-linux.json'),'utf8').includes(privateText),false);
 }finally{rmSync(root,{recursive:true,force:true});}
});
test('screenshots are decoded and reencoded without metadata or appended private bytes',()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'install-public-proof-')));
 try {
  writeFileSync(join(root,'install-cycle-linux.json'),JSON.stringify(input));
  const image=join(root,'install-cycle-linux-panel.png');
  execFileSync(process.platform==='win32'?'python':'python3',['-c', `from PIL import Image, PngImagePlugin\nimport sys\nmetadata=PngImagePlugin.PngInfo()\nmetadata.add_text('private',sys.argv[2])\nImage.new('RGBA',(2,2),(20,30,40,255)).save(sys.argv[1],pnginfo=metadata)\nwith open(sys.argv[1],'ab') as f: f.write(sys.argv[2].encode())`,image,privateText]);
  const target=join(root,'public');publishInstallProof(root,target);
  const clean=readFileSync(join(target,'install-cycle-linux-panel.png'));
  assert.equal(clean.includes(Buffer.from(privateText)),false);
  execFileSync(process.platform==='win32'?'python':'python3',['-c',"from PIL import Image; import sys; im=Image.open(sys.argv[1]); assert im.size==(2,2) and im.getpixel((0,0))==(20,30,40,255) and not im.info",join(target,'install-cycle-linux-panel.png')]);
 } finally {rmSync(root,{recursive:true,force:true});}
});
test('source and publisher parent symlinks are refused',()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'install-public-proof-')));
 try {
  mkdirSync(join(root,'real'));
  symlinkSync(join(root,'real'),join(root,'link'),process.platform==='win32'?'junction':'dir');
  assert.throws(()=>publishInstallProof(join(root,'link'),join(root,'out')));
  assert.throws(()=>publishInstallProof(root,join(root,'link','out')));
 } finally {rmSync(root,{recursive:true,force:true});}
});
test('preexisting output files cannot bypass the filter',()=>{
 const root=realpathSync(mkdtempSync(join(tmpdir(),'install-public-proof-')));
 try {
  mkdirSync(join(root,'public'));
  writeFileSync(join(root,'public','private.json'),privateText);
  assert.throws(()=>publishInstallProof(root,join(root,'public')),/must be empty/);
  assert.equal(readFileSync(join(root,'public','private.json'),'utf8'),privateText);
 } finally {rmSync(root,{recursive:true,force:true});}
});
