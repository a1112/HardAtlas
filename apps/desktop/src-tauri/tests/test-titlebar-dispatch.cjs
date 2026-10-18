const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const os=require('node:os'),assert=require('node:assert/strict'),{test}=require('node:test');
let producer='fixture',phase='none';
const sha=s=>crypto.createHash('sha256').update(s).digest('hex');
class Target{
 constructor(){this.listeners=new Map()}
 addEventListener(type,fn,capture=false){const list=this.listeners.get(type)||[];list.push({fn,producer,capture:typeof capture==='object'?!!capture.capture:!!capture});this.listeners.set(type,list)}
 removeEventListener(type,fn){this.listeners.set(type,(this.listeners.get(type)||[]).filter(x=>x.fn!==fn))}
 dispatchEvent(e){for(const {fn} of this.listeners.get(e.type)||[]){fn(e);if(e.stopped)break}}
}
class Element extends Target{
 constructor(attr=null,interactive=false){super();this.attrs=new Map();this.children=[];this.hidden=false;this.interactive=interactive;this.tagName=interactive?'BUTTON':'DIV';if(attr!==null)this.attrs.set('data-project-drag-region',attr);this.style={setProperty(k,v){this[k]=v},getPropertyValue(k){return this[k]||''},getPropertyPriority(){return ''},removeProperty(k){delete this[k]}}}
 getAttribute(k){return this.attrs.get(k)??null} hasAttribute(k){return this.attrs.has(k)} setAttribute(k,v){this.attrs.set(k,v)}removeAttribute(k){this.attrs.delete(k)}
 matches(){return this.interactive} closest(){return this.interactive?this:this.parent?.closest()??null}
 appendChild(c){this.children.push(c);c.parent=this}attachShadow(){this.shadow=new Element;return this.shadow}remove(){this.removed=true}
 getClientRects(){return this.hidden?[]:[{}]}
}
const tick=()=>new Promise(r=>setImmediate(r));
function balanced(source,marker){const a=source.indexOf(marker),start=source.indexOf('{',a);let depth=0;for(let i=start;i<source.length;i++){if(source[i]==='{')depth++;else if(source[i]==='}'&&!--depth)return source.slice(a,i+1)}throw Error(marker)}
function fixture(repo,{fullscreen=false,cancelled=false,modal=false,runtime=true,mobile=false,macos=true,label='main'}={}){
 const win=new Target,doc=new Target,calls=[];
 const normal=new Element('deep'),button=new Element(null,true),marked=new Element('deep');marked.parent=button;button.parent=normal;
 const state={decorated:false,resizable:true,maximized:false,fullscreen,focused:true,maximizable:true};
 const invoke=async(command,args)=>{calls.push({route:command,args,producer,phase});return state[command.split('|').at(-1).replace('is_','')]}
 Object.assign(win,{top:win,location:{protocol:'tauri:',hostname:'localhost'},requestAnimationFrame:fn=>setImmediate(fn),cancelAnimationFrame:clearImmediate,setTimeout,clearTimeout,CustomEvent:class{constructor(type,options){this.type=type;this.detail=options?.detail}},__TAURI_INTERNALS__:{metadata:{currentWindow:{label}},invoke}});
 const modalNode=new Element;modalNode.attrs.set('aria-modal','true');
 Object.assign(doc,{readyState:'complete',documentElement:new Element,createElement:()=>new Element,querySelectorAll:selector=>selector.includes('drag-region')?[normal]:modal?[modalNode]:[],querySelector:()=>modal?modalNode:null});
 const sandbox={module:{exports:{}}};vm.runInNewContext(repo.shared,sandbox);producer='project-shared';const controller=runtime&&!mobile?sandbox.module.exports.install(win,doc,macos):undefined;
 producer='tauri-builtin';if(runtime&&!mobile)vm.runInNewContext(repo.builtin,{window:win,document:doc,HTMLElement:Element,__TEMPLATE_os_name__:macos?'macos':'windows'});
 producer="windowbase-legacy-fallback";if(repo.fallback){
  // Only erase TypeScript type syntax; execute the published function body.
  let code=repo.fallback.replace('querySelectorAll<HTMLElement>','querySelectorAll').replace('event.target as HTMLElement | null','event.target');
  vm.runInNewContext(code+'\nbindDragRegions();',{window:win,document:doc,hasTauriRuntime:()=>runtime,isMobile:mobile,invoke,refreshState:async()=>{},console});
 }
 function event(type,eventPath=[normal],extra={}){
  const e={type,target:eventPath[0],button:0,detail:1,pointerType:'mouse',clientX:40,clientY:20,defaultPrevented:cancelled,composedPath:()=>eventPath,preventDefault(){this.defaultPrevented=true;this.prevented=true},stopImmediatePropagation(){this.stopped=true},...extra};
  const targets=[win,doc,...[...eventPath].reverse()];
  for(const capture of [true,false]){
   const ordered=capture?targets:[...targets].reverse();
   for(const target of ordered)for(const listener of target.listeners.get(type)||[]){
    if(listener.capture===capture){producer=listener.producer;phase=(target===win?'window':target===doc?'document':'element')+(capture?' capture':' bubble');listener.fn(e);}
    if(e.stopped)return e;
   }
  }
  return e;
 }
 return{controller,win,doc,state,event,normal,button,marked,calls,actions:()=>calls.filter(c=>!c.route.includes('|is_')).map(c=>c.route)};
}

const crate=path.resolve(__dirname,'..');
let lockDir=crate;while(!fs.existsSync(path.join(lockDir,'Cargo.lock'))){const parent=path.dirname(lockDir);if(parent===lockDir)throw Error('Cargo.lock missing');lockDir=parent;}
const lock=fs.readFileSync(path.join(lockDir,'Cargo.lock'),'utf8');
const version=lock.match(/\[\[package\]\]\s+name = "tauri"\s+version = "([^"]+)"/)[1];
const cache=path.join(process.env.CARGO_HOME||path.join(os.homedir(),'.cargo'),'registry','src');
const builtinPath=fs.readdirSync(cache).map(name=>path.join(cache,name,'tauri-'+version,'src/window/scripts/drag.js')).find(name=>fs.existsSync(name));
if(!builtinPath)throw Error('Locked Tauri drag.js missing from local registry; no download attempted');
const shared=fs.readFileSync(path.join(crate,'src/project-window-chrome.js'),'utf8');
const main=path.join(crate,'../src/main.ts');
const fallback=fs.existsSync(main)?balanced(fs.readFileSync(main,'utf8'),'function bindDragRegions()'):null;
const repo={shared,builtin:fs.readFileSync(builtinPath,'utf8'),fallback};
console.log(JSON.stringify({kind:'synthetic combined production listeners; not native proof',shared_sha256:sha(shared),builtin_version:version,builtin_sha256:sha(repo.builtin),fallback_sha256:fallback?sha(fallback):null}));
async function check(name,options,run,expected,source='project-shared'){
 const f=fixture(repo,options);await tick();try{run(f);await tick();assert.deepEqual(f.actions(),expected,name);for(const row of f.calls.filter(c=>!c.route.includes('|is_')))assert.equal(row.producer,source,name);console.log(JSON.stringify({scenario:name,actions:f.calls.filter(c=>!c.route.includes('|is_'))}));}finally{f.controller?.destroy();}
}
for(const macos of [true,false]){
 const prefix=macos?'mac':'win-linux';
 test(prefix+' normal title has one owner',()=>check(prefix+' normal',{macos},f=>{f.event('pointerdown');f.event('mousedown');},[macos?'native_titlebar_drag':'plugin:window|start_dragging']));
 test(prefix+' double click uses dedicated policy only on mac',()=>check(prefix+' double',{macos},f=>{f.event('mousedown',undefined,{detail:2});f.event('mouseup',undefined,{detail:2});f.event('dblclick',undefined,{detail:2})},[macos?'native_titlebar_double_click':'plugin:window|internal_toggle_maximize']));
 for(const [name,options,run] of [
  ['fullscreen',{fullscreen:true},f=>{f.event('pointerdown');f.event('mousedown');f.event('mousedown',undefined,{detail:2});f.event('mouseup',undefined,{detail:2})}],
  ['defaultPrevented',{cancelled:true},f=>{f.event('pointerdown');f.event('mousedown',undefined,{detail:2});f.event('mouseup',undefined,{detail:2})}],
  ['interactive ancestor',{},f=>{f.event('pointerdown',[f.marked,f.button,f.normal]);f.event('mousedown',[f.marked,f.button,f.normal]);f.event('mouseup',[f.marked,f.button,f.normal],{detail:2})}],
  ['HTML modal',{modal:true},f=>{f.event('pointerdown');f.event('mousedown');f.event('mousedown',undefined,{detail:2});f.event('mouseup',undefined,{detail:2})}],
  ['file drop',{},f=>{for(const type of ['dragstart','dragover','drop']){const e=f.event(type);assert.equal(e.defaultPrevented,false);assert.equal(e.stopped,undefined)}}],
  ['right button',{},f=>{for(const type of ['pointerdown','mousedown','mouseup'])f.event(type,undefined,{button:2})}],
 ])test(prefix+' '+name+' cannot fall through to a second owner',()=>check(prefix+' '+name,{macos,...options},run,[]));
}
for(const cancel of ['pointercancel','dragstart','keydown'])test('mac '+cancel+' clears pending double without builtin fallback',()=>check(cancel,{},f=>{f.event('mousedown',undefined,{detail:2});f.event(cancel,undefined,{key:'Escape'});f.event('mouseup',undefined,{detail:2})},[]));
test('mac cancelled release clears pending without builtin fallback',()=>check('cancelled release',{},f=>{f.event('mousedown',undefined,{detail:2});f.event('mouseup',undefined,{detail:2,defaultPrevented:true});f.event('mouseup',undefined,{detail:2});},[]));
test('mac pointer movement cancels double without builtin fallback',()=>check('moved double',{},f=>{f.event('mousedown',undefined,{detail:2});f.event('mouseup',undefined,{detail:2,clientX:50})},[]));
test('unrelated legacy Tauri region retains its builtin behavior',()=>check('non-owned legacy region',{},f=>{const legacy=new Element;legacy.setAttribute('data-tauri-drag-region','deep');f.event('mousedown',[legacy]);},['plugin:window|start_dragging'],'tauri-builtin'));
test('browser fallback remains inert without runtime',()=>check('browser',{runtime:false},f=>{f.event('pointerdown');f.event('mousedown')},[]));
if(fallback){
 test('mobile legacy path retains its prior runtime-only dispatch',()=>check('mobile legacy',{mobile:true},f=>f.event('pointerdown'),['start_drag_window'],'windowbase-legacy-fallback'));
 test('WindowBase numeric child uses same exclusive route',()=>check('numeric child',{label:'test-window-123'},f=>{f.event('pointerdown');f.event('mousedown')},['native_titlebar_drag']));
}else test('HardAtlas unrelated document has no application route',()=>check('excluded child',{label:'test-window-123'},f=>{f.event('pointerdown');f.event('mousedown')},[]));
