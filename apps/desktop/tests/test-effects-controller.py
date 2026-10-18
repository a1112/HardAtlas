"""Compile the unmodified production controller with an instrumented host.

This checks event routing/cache semantics, not AppKit, desktop interaction or
real compositor performance. The platform preference/observer boundary is
explicitly replaced; the controller body and policy module are read verbatim.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile

root = Path(__file__).resolve().parent.parent
source = root / 'src-tauri/src/effects.rs'
policy = source.with_name('effects_policy.rs')
controller = source.read_text().split('#[cfg(target_os = "macos")]')[0]
host = r'''
#![allow(dead_code, unused_imports)]
extern crate self as tauri;
use std::{collections::HashMap, sync::{Arc,Mutex}};
type Handler = Arc<dyn Fn(&WindowEvent) + Send + Sync>;
type Work = Box<dyn FnOnce() + Send>;
#[derive(Clone,Default)] pub struct AppHandle(Arc<App>);
#[derive(Default)] struct App {
    effects: effects::EffectsState,
    windows: Mutex<HashMap<String,WebviewWindow>>,
    queue: Mutex<Vec<Work>>,
}
#[derive(Clone)] pub struct WebviewWindow(Arc<Window>);
struct Window { label:String, app:AppHandle, handlers:Mutex<Vec<Handler>>, stats:Mutex<Stats> }
#[derive(Clone,Default,Debug,PartialEq)] struct Stats { apply:usize, clear:usize, eval:usize, fail:bool }
pub enum WindowEvent { Focused(bool), ThemeChanged(()), Destroyed, Moved(()), Resized(()) }
pub trait Manager {
    fn handle(&self)->&AppHandle;
    fn state<T>(&self)->&effects::EffectsState { &self.handle().0.effects }
    fn get_webview_window(&self,label:&str)->Option<WebviewWindow> { self.handle().0.windows.lock().unwrap().get(label).cloned() }
    fn webview_windows(&self)->HashMap<String,WebviewWindow> { self.handle().0.windows.lock().unwrap().clone() }
}
impl Manager for AppHandle { fn handle(&self)->&AppHandle {self} }
impl Manager for WebviewWindow { fn handle(&self)->&AppHandle { &self.0.app } }
impl AppHandle {
    fn window(&self,label:&str)->WebviewWindow {
        let w=WebviewWindow(Arc::new(Window { label:label.into(),app:self.clone(),handlers:Mutex::new(vec![]),stats:Mutex::new(Stats::default()) }));
        self.0.windows.lock().unwrap().insert(label.into(),w.clone()); w
    }
    fn drain(&self) { loop {
        let work=std::mem::take(&mut *self.0.queue.lock().unwrap());
        if work.is_empty() {break;} for f in work {f();}
    } }
    fn queued(&self)->usize {self.0.queue.lock().unwrap().len()}
    fn remove(&self,label:&str) {self.0.windows.lock().unwrap().remove(label);}
}
impl WebviewWindow {
    fn app_handle(&self)->&AppHandle { &self.0.app }
    fn label(&self)->&str { &self.0.label }
    fn run_on_main_thread(&self,f:impl FnOnce()+Send+'static)->Result<(),String> { self.0.app.0.queue.lock().unwrap().push(Box::new(f)); Ok(()) }
    fn on_window_event(&self,f:impl Fn(&WindowEvent)+Send+Sync+'static) {self.0.handlers.lock().unwrap().push(Arc::new(f));}
    fn eval(&self,_script:&str)->Result<(),String> {self.0.stats.lock().unwrap().eval+=1;Ok(())}
    fn event(&self,event:WindowEvent) {let handlers=self.0.handlers.lock().unwrap().clone();for h in handlers {h(&event);}}
    fn stats(&self)->Stats {self.0.stats.lock().unwrap().clone()}
}
mod platform {
    use super::*;
    #[derive(Clone,Debug,Default)] pub struct NativeBackdropStatus { pub enabled:bool,pub applied:bool,pub reduced_transparency:bool,pub high_contrast:bool }
    impl NativeBackdropStatus {
        pub fn pending()->Self {Self::default()}
        pub fn unavailable(_platform:&str,_detail:&str)->Self {Self::default()}
    }
    pub fn current_platform()->&'static str {"instrumented test host"}
    pub fn clear_native_backdrop(w:&WebviewWindow) {w.0.stats.lock().unwrap().clear+=1;}
    pub fn apply_native_backdrop(w:&WebviewWindow)->NativeBackdropStatus {
        let mut stats=w.0.stats.lock().unwrap(); stats.apply+=1;
        NativeBackdropStatus {applied:!stats.fail,..Default::default()}
    }
}
thread_local! {static PREFERENCES:std::cell::Cell<(bool,bool)>=const {std::cell::Cell::new((false,false))};}
fn preferences(reduce:bool,contrast:bool) {PREFERENCES.with(|v|v.set((reduce,contrast)));}
'''
tests = r'''
#[cfg(test)] mod lifecycle_tests {
 use super::*;
 fn active()->(AppHandle,WebviewWindow) {preferences(false,false);let app=AppHandle::default();let w=app.window("main");effects::configure(&w);app.drain();effects::set_enabled(&app,true).unwrap();app.drain();(app,w)}
 #[test] fn startup_is_solid_until_main_restores_preference() {
  preferences(false,false);let app=AppHandle::default();let w=app.window("main");effects::configure(&w);app.drain();
  assert_eq!(w.stats().apply,0);assert!(!effects::status(&w).enabled);assert!(!effects::status(&w).applied);
  effects::set_enabled(&app,true).unwrap();app.drain();assert!(effects::status(&w).applied);assert_eq!(w.stats().apply,1);
 }
 #[test] fn moved_and_resized_events_schedule_no_material_work() {
  let (app,w)=active();let initial=w.stats();
  for _ in 0..500 {w.event(WindowEvent::Moved(()));w.event(WindowEvent::Resized(()));}
  assert_eq!(app.queued(),0);assert_eq!(w.stats(),initial);
 }
 #[test] fn unchanged_focus_and_theme_do_not_rebuild_or_publish() {
  let (app,w)=active();let initial=w.stats();
  for _ in 0..100 {w.event(WindowEvent::Focused(true));w.event(WindowEvent::ThemeChanged(()));}
  app.drain();assert_eq!(w.stats(),initial);
 }
 #[test] fn child_inherits_choice_and_off_clears_every_window() {
  let (app,main)=active();let child=app.window("child");effects::configure(&child);app.drain();assert!(effects::status(&child).applied);
  effects::set_enabled(&app,false).unwrap();app.drain();
  for w in [&main,&child] {assert!(!effects::status(w).enabled);assert!(!effects::status(w).applied);}
  let later=app.window("later");effects::configure(&later);app.drain();assert_eq!(later.stats().apply,0);assert!(!effects::status(&later).enabled);
 }
 #[test] fn accessibility_changes_clear_then_restore_only_opted_in_material() {
  let (app,w)=active();preferences(true,false);effects::refresh_all(&app);app.drain();
  let state=effects::status(&w);assert!(state.enabled&&state.reduced_transparency&&!state.applied);
  preferences(false,true);effects::refresh_all(&app);app.drain();assert!(effects::status(&w).high_contrast);assert!(!effects::status(&w).applied);
  preferences(false,false);effects::refresh_all(&app);app.drain();assert!(effects::status(&w).applied);
  effects::set_enabled(&app,false).unwrap();app.drain();preferences(true,true);effects::refresh_all(&app);app.drain();preferences(false,false);effects::refresh_all(&app);app.drain();assert!(!effects::status(&w).applied);
 }
 #[test] fn native_failure_is_bounded_and_explicit_refresh_retries() {
  let (app,w)=active();effects::set_enabled(&app,false).unwrap();app.drain();w.0.stats.lock().unwrap().fail=true;
  effects::set_enabled(&app,true).unwrap();app.drain();let attempts=w.stats().apply;assert!(!effects::status(&w).applied);
  for _ in 0..100 {w.event(WindowEvent::Focused(true));}app.drain();assert_eq!(w.stats().apply,attempts);
  w.0.stats.lock().unwrap().fail=false;effects::retry_all(&app);app.drain();assert_eq!(w.stats().apply,attempts+1);assert!(effects::status(&w).applied);
 }
 #[test] fn queued_update_for_removed_window_does_no_native_work() {
  preferences(false,false);let app=AppHandle::default();let w=app.window("child");effects::configure(&w);app.remove("child");w.event(WindowEvent::Destroyed);app.drain();assert_eq!(w.stats(),Stats::default());
 }
 #[test] fn destroyed_window_cache_does_not_suppress_reopened_window() {
  let (app,old)=active();app.remove("main");old.event(WindowEvent::Destroyed);
  let reopened=app.window("main");effects::configure(&reopened);app.drain();assert!(effects::status(&reopened).applied);assert_eq!(reopened.stats().apply,1);
 }
 #[test] fn queued_old_generation_cannot_suppress_reopened_window() {
  preferences(false,false);let app=AppHandle::default();effects::set_enabled(&app,true).unwrap();
  let old=app.window("child");effects::configure(&old);
  // The scheduled main-thread closure has not run when the old window closes.
  app.remove("child");old.event(WindowEvent::Destroyed);
  let reopened=app.window("child");effects::configure(&reopened);app.drain();
  assert_eq!(old.stats().apply,0,"closed generation must not apply material");
  assert_eq!(reopened.stats().apply,1,"new window must receive its own material");
 }
 #[test] fn late_destroyed_event_cannot_remove_reopened_window_cache() {
  let (app,old)=active();app.remove("main");
  let reopened=app.window("main");effects::configure(&reopened);app.drain();
  let initial=reopened.stats();assert!(effects::status(&reopened).applied);
  old.event(WindowEvent::Destroyed);
  assert!(effects::status(&reopened).applied,"old Destroyed must not clear new status");
  reopened.event(WindowEvent::Focused(true));app.drain();assert_eq!(reopened.stats(),initial);
 }
 #[test] fn late_focus_event_cannot_update_reopened_window_policy() {
  let (app,old)=active();app.remove("main");old.event(WindowEvent::Destroyed);
  let reopened=app.window("main");effects::configure(&reopened);app.drain();let initial=reopened.stats();
  preferences(true,false);old.event(WindowEvent::Focused(true));old.event(WindowEvent::ThemeChanged(()));app.drain();
  assert_eq!(reopened.stats(),initial);assert!(effects::status(&reopened).applied);
  effects::refresh_all(&app);app.drain();assert!(!effects::status(&reopened).applied);
 }
}
#[cfg(test)] mod independent_review_tests {
 use super::*;
 #[test] fn new_closure_before_old_closure_retains_new_state() {
  preferences(false,false);let app=AppHandle::default();effects::set_enabled(&app,true).unwrap();
  let old=app.window("child");effects::configure(&old);app.remove("child");
  let new=app.window("child");effects::configure(&new);
  let mut work=std::mem::take(&mut *app.0.queue.lock().unwrap());
  work.reverse();for f in work {f();}
  assert_eq!(old.stats(),Stats::default());assert_eq!(new.stats().apply,1);assert!(effects::status(&new).applied);
  old.event(WindowEvent::Destroyed);assert!(effects::status(&new).applied);
 }
 #[test] fn late_focus_without_old_destroyed_cannot_touch_pending_new_generation() {
  preferences(false,false);let app=AppHandle::default();effects::set_enabled(&app,true).unwrap();
  let old=app.window("child");effects::configure(&old);app.drain();app.remove("child");
  let new=app.window("child");effects::configure(&new);
  old.event(WindowEvent::Focused(true));old.event(WindowEvent::ThemeChanged(()));old.event(WindowEvent::Destroyed);
  app.drain();assert_eq!(new.stats().apply,1);assert!(effects::status(&new).applied);
  let before=new.stats();new.event(WindowEvent::Focused(true));app.drain();assert_eq!(new.stats(),before);
 }
}

'''
generated = host + '\nmod effects_policy {\n' + policy.read_text() + '\n}\n'
# Inner module docs remain valid only at the beginning of the inserted module.
generated = generated.replace('mod effects_policy {\n//!', 'mod effects_policy {\n//!')
generated += '\nmod effects {\n' + controller + '\nfn system_preferences()->(bool,bool) {crate::PREFERENCES.with(|v|v.get())}\n}\n' + tests
temporary = tempfile.TemporaryDirectory(prefix='native-effects-controller-')
output = Path(temporary.name)
out=output/'effects-controller.rs'
out.write_text(generated)
binary=out.with_suffix('')
subprocess.run(['rustc','--edition=2021','--test',str(out),'-o',str(binary)],check=True)
result=subprocess.run([str(binary)],capture_output=True,text=True)
(output/'effects-controller-tests.txt').write_text(result.stdout+result.stderr)
(output/'effects-controller-source.json').write_text(json.dumps({
    'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
    'boundary':'Verbatim production controller before the first platform cfg; actual policy module; instrumented queue/window/platform host. Native observer and preferences replaced. No AppKit/native performance validation.',
    'exitCode':result.returncode},indent=2)+'\n')
print('Production source:', source)
print('Source SHA256:', hashlib.sha256(source.read_bytes()).hexdigest())
print(result.stdout+result.stderr)
temporary.cleanup()
raise SystemExit(result.returncode)
