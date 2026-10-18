//! Creation-bound title gesture ownership. Labels alone are not native identity.
use std::{
    collections::HashMap,
    sync::{
        atomic::{AtomicUsize, Ordering},
        Arc, Mutex,
    },
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Capabilities {
    pub minimizable: bool,
    pub maximizable: bool,
}
pub struct Session {
    pub capabilities: Capabilities,
    identity: AtomicUsize,
}
impl Session {
    pub fn bind(&self, identity: usize) {
        self.identity.store(identity, Ordering::Release);
    }
    pub fn owns(&self, identity: usize) -> bool {
        identity != 0 && self.identity.load(Ordering::Acquire) == identity
    }
}
#[derive(Default)]
pub struct Registry(Mutex<HashMap<String, Arc<Session>>>);
impl Registry {
    pub fn register(
        &self,
        label: &str,
        capabilities: Capabilities,
    ) -> Result<Arc<Session>, String> {
        let session = Arc::new(Session {
            capabilities,
            identity: AtomicUsize::new(0),
        });
        self.0
            .lock()
            .map_err(|e| e.to_string())?
            .insert(label.into(), session.clone());
        Ok(session)
    }
    pub fn get(&self, label: &str) -> Result<Arc<Session>, String> {
        self.0
            .lock()
            .map_err(|e| e.to_string())?
            .get(label)
            .cloned()
            .ok_or_else(|| "Window has no registered application titlebar".into())
    }
    pub fn current(&self, label: &str, session: &Arc<Session>) -> bool {
        self.0
            .lock()
            .ok()
            .and_then(|entries| entries.get(label).cloned())
            .is_some_and(|active| Arc::ptr_eq(&active, session))
    }
    pub fn retire(&self, label: &str, session: &Arc<Session>) {
        if let Ok(mut entries) = self.0.lock() {
            if entries
                .get(label)
                .is_some_and(|active| Arc::ptr_eq(active, session))
            {
                entries.remove(label);
            }
        }
    }
}
