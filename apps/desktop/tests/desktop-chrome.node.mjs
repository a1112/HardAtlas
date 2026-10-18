// Node >= 22.18 supports these production TypeScript modules directly.
// Instrumented bridge/storage only: this does not validate native visual effects.
import test from 'node:test';
import assert from 'node:assert/strict';
import { createEffectsController } from '../src/window-chrome/native-effects.ts';
import { windowShortcut } from '../src/window-chrome/window-controls.ts';
const status = (enabled = true, applied = enabled) => ({ enabled, applied, reducedTransparency:false, highContrast:false, platform:'test', effect:'test', detail:'test host' });
const tick = () => new Promise(resolve => setImmediate(resolve));
function host(saved) {
  const values = new Map(saved === undefined ? [] : [['effects', saved]]);
  const calls = [], states = [], errors = [], events = new EventTarget();
  let current = status(false), failure = false;
  const storage = { getItem: key => values.get(key) ?? null, setItem: (key,value) => values.set(key,value), removeItem: key => values.delete(key) };
  const invoke = async (command, args) => {
    calls.push([command,args]);
    if(command === 'set_native_effects_enabled') { if(failure) throw Error('bridge unavailable'); current=status(args.enabled); }
    return current;
  };
  const controller = createEffectsController({invoke,storage,events,key:'effects',onStatus:s=>states.push(s),onError:e=>errors.push(e)});
  return { controller,calls,states,errors,events,values,storage, set current(s){current=s;},set failure(s){failure=s;} };
}
test('startup restores explicit off before the first queried status', async () => {
  const h=host('false'); await h.controller.start();
  assert.deepEqual(h.calls.map(c=>c[0]),['set_native_effects_enabled','native_effects_status']);
  assert.deepEqual(h.calls[0][1],{enabled:false}); assert.equal(h.states.at(-1).applied,false);
  h.controller.dispose();
});
test('fresh install enables requested native effects; manual off survives a new controller', async () => {
  const h=host(); await h.controller.start(); assert.equal(h.states.at(-1).enabled,true);
  await h.controller.setEnabled(false); assert.equal(h.values.get('effects'),'false'); h.controller.dispose();
  const next=host(h.values.get('effects')); await next.controller.start(); assert.equal(next.states.at(-1).applied,false);next.controller.dispose();
});
test('OS accessibility fallback updates appearance without overwriting the opt-in', async () => {
  const h=host('true'); await h.controller.start();
  h.current={...status(true,false),highContrast:true,reducedTransparency:true};
  h.events.dispatchEvent(new Event('native-backdrop-changed'));await tick();
  assert.equal(h.states.at(-1).applied,false);assert.equal(h.states.at(-1).highContrast,true);
  assert.equal(h.values.get('effects'),'true');assert.equal(h.calls.filter(c=>c[0]==='set_native_effects_enabled').length,1);h.controller.dispose();
});
test('failed native preference write rolls persisted choice back', async () => {
  const h=host('true');await h.controller.start();h.failure=true;
  await assert.rejects(h.controller.setEnabled(false),/bridge unavailable/);
  assert.equal(h.values.get('effects'),'true');assert.equal(h.states.at(-1).enabled,true);h.controller.dispose();
});
test('storage unavailable leaves backend in its initial solid policy', async () => {
  const h=host();h.storage.getItem=()=>{throw Error('blocked storage')};
  await assert.rejects(h.controller.start(),/blocked storage/);assert.equal(h.calls.length,0);h.controller.dispose();
});
test('native application failure is presented as solid without a retry storm', async () => {
  const h=host();await h.controller.start();h.current=status(true,false);
  h.events.dispatchEvent(new Event('native-backdrop-changed'));await tick();
  assert.equal(h.states.at(-1).applied,false);assert.equal(h.calls.filter(c=>c[0]==='set_native_effects_enabled').length,1);h.controller.dispose();
});
test('out-of-order status cannot restore stale transparency and dispose removes the listener', async () => {
  const requests=[],states=[],events=new EventTarget();let reads=0;
  const c=createEffectsController({key:'effects',storage:{getItem:()=>null,setItem(){},removeItem(){}},events,onError:()=>{},onStatus:s=>states.push(s),
    invoke:async command=> {assert.equal(command,'native_effects_status');reads++;return await new Promise(resolve=>requests.push(resolve));}});
  const a=c.refresh(),b=c.refresh();requests[1](status(false));await b;requests[0](status(true));await a;
  assert.deepEqual(states.map(s=>s.applied),[false]);
  const pending=c.refresh();c.dispose();requests[2](status(true));await pending;
  events.dispatchEvent(new Event('native-backdrop-changed'));await tick();assert.equal(reads,3);assert.equal(states.length,1);
});
test('disposed mounted controller no longer queries native change notifications', async () => {
  const h=host();await h.controller.start();h.controller.dispose();const before=h.calls.length;
  h.events.dispatchEvent(new Event('native-backdrop-changed'));await tick();assert.equal(h.calls.length,before);
});
const key = (name, extra={}) => ({key:name,ctrlKey:false,metaKey:false,altKey:false,shiftKey:false,defaultPrevented:false,isComposing:false,repeat:false,...extra});
test('desktop shortcut entry maps close/minimize/quit/fullscreen', () => {
  assert.equal(windowShortcut(key('w',{metaKey:true})),'close');
  assert.equal(windowShortcut(key('M',{metaKey:true})),'minimize');
  assert.equal(windowShortcut(key('q',{ctrlKey:true})),'quit');
  assert.equal(windowShortcut(key('f',{ctrlKey:true,metaKey:true})),'fullscreen');
  assert.equal(windowShortcut(key('F11')),'fullscreen');
});
test('typing, IME, repeat, consumed events and unrelated shortcuts are not intercepted', () => {
  for(const event of [key('w'),key('w',{metaKey:true,isComposing:true}),key('w',{metaKey:true,repeat:true}),key('w',{metaKey:true,defaultPrevented:true}),key('w',{metaKey:true,altKey:true}),key('w',{metaKey:true,shiftKey:true}),key('c',{metaKey:true})]) assert.equal(windowShortcut(event),undefined);
});
