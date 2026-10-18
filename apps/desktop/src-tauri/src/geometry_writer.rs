//! Bounded latest-value persistence. The UI never performs filesystem I/O.
use std::{
    fs::{self, OpenOptions},
    io::{self, Write},
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicU64, Ordering},
        Arc, Condvar, Mutex,
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};
const DEBOUNCE: Duration = Duration::from_millis(120);
const MAX_DELAY: Duration = Duration::from_secs(1);
const RETRY: Duration = Duration::from_millis(250);
#[derive(Default)]
struct State {
    latest: Option<(u64, String)>,
    last_published: Option<String>,
    revision: u64,
    completed: u64,
    first: Option<Instant>,
    last: Option<Instant>,
    retry: Option<Instant>,
    force: bool,
    stopping: bool,
    stopped: bool,
    error: Option<String>,
}
struct Shared {
    state: Mutex<State>,
    wake: Condvar,
}
pub struct GeometryWriter {
    shared: Arc<Shared>,
    worker: Option<JoinHandle<()>>,
}
/// A label has one stable geometry path within an application. Reuse its writer
/// while a destroyed window's final write is in flight, so a reopened window
/// cannot be overwritten later by that older write.
pub fn writer_for_window(
    writers: &Mutex<Vec<(String, Arc<GeometryWriter>)>>,
    label: &str,
    path: PathBuf,
) -> io::Result<Arc<GeometryWriter>> {
    let mut entries = writers.lock().unwrap();
    entries.retain(|(_, writer)| !writer.is_finished());
    if let Some((_, writer)) = entries
        .iter()
        .rev()
        .find(|(name, writer)| name == label && writer.resume())
    {
        return Ok(writer.clone());
    }
    let writer = Arc::new(GeometryWriter::new(path)?);
    entries.push((label.to_owned(), writer.clone()));
    Ok(writer)
}
/// Restore from the last observed normal geometry when a close-time write has
/// not reached disk yet. Reading this cache never waits for filesystem I/O.
pub fn cached_record(
    writers: &Mutex<Vec<(String, Arc<GeometryWriter>)>>,
    label: &str,
) -> Option<String> {
    writers
        .lock()
        .unwrap()
        .iter()
        .rev()
        .find(|(name, _)| name == label)
        .and_then(|(_, writer)| writer.shared.state.lock().unwrap().last_published.clone())
}
impl GeometryWriter {
    pub fn new(path: PathBuf) -> io::Result<Self> {
        Self::with_sink(move |contents| atomic_write(&path, contents))
    }
    fn with_sink(
        mut sink: impl FnMut(&str) -> io::Result<()> + Send + 'static,
    ) -> io::Result<Self> {
        let shared = Arc::new(Shared {
            state: Mutex::new(State::default()),
            wake: Condvar::new(),
        });
        let worker_shared = shared.clone();
        let worker = thread::Builder::new()
            .name("window-geometry-writer".into())
            .spawn(move || {
                let mut state = worker_shared.state.lock().unwrap();
                loop {
                    if state.latest.is_none() {
                        if state.stopping {
                            break;
                        }
                        state = worker_shared.wake.wait(state).unwrap();
                        continue;
                    }
                    let now = Instant::now();
                    // Lifecycle flushes may skip debounce, but must respect I/O
                    // failure backoff. Stopping still permits one final attempt.
                    let due = if state.force {
                        now
                    } else {
                        (state.last.unwrap() + DEBOUNCE).min(state.first.unwrap() + MAX_DELAY)
                    };
                    let due = state.retry.map_or(due, |retry| due.max(retry));
                    if !state.stopping && now < due {
                        state = worker_shared.wake.wait_timeout(state, due - now).unwrap().0;
                        continue;
                    }
                    let (revision, contents) = state.latest.take().unwrap();
                    state.first = None;
                    state.last = None;
                    state.force = false;
                    drop(state); // A blocked disk leaves the latest-value slot available to publishers.
                    let result = sink(&contents);
                    state = worker_shared.state.lock().unwrap();
                    match result {
                        Ok(()) => {
                            state.completed = revision;
                            state.error = None;
                            state.retry = None;
                        }
                        Err(error) => {
                            // Retain the failed record only when no newer one supersedes it.
                            let newer_pending = state.latest.is_some();
                            if !newer_pending {
                                state.latest = Some((revision, contents));
                                let now = Instant::now();
                                state.first = Some(now);
                                state.last = Some(now);
                            }
                            state.error = Some(error.to_string());
                            state.retry = Some(Instant::now() + RETRY);
                            // An older in-flight failure must not skip a newer
                            // pending value during retirement. If that latest
                            // value also fails with no successor, stop boundedly.
                            if state.stopping && !newer_pending {
                                break;
                            }
                        }
                    }
                    worker_shared.wake.notify_all();
                }
                state.stopped = true;
                worker_shared.wake.notify_all();
            })?;
        Ok(Self {
            shared,
            worker: Some(worker),
        })
    }
    pub fn schedule(&self, contents: String) {
        let mut state = self.shared.state.lock().unwrap();
        if state.stopping {
            return;
        }
        state.revision += 1;
        state.last_published = Some(contents.clone());
        state.latest = Some((state.revision, contents));
        let now = Instant::now();
        state.first.get_or_insert(now);
        state.last = Some(now);
        self.shared.wake.notify_one();
    }
    pub fn flush_soon(&self) {
        let mut state = self.shared.state.lock().unwrap();
        state.force = state.latest.is_some();
        self.shared.wake.notify_one();
    }
    /// Retire a destroyed window without joining its worker on the event thread.
    pub fn finish_soon(&self) {
        let mut state = self.shared.state.lock().unwrap();
        state.stopping = true;
        self.shared.wake.notify_one();
    }
    pub fn is_finished(&self) -> bool {
        self.worker.as_ref().map_or(true, JoinHandle::is_finished)
    }
    fn resume(&self) -> bool {
        let mut state = self.shared.state.lock().unwrap();
        if state.stopped {
            return false;
        }
        state.stopping = false;
        true
    }
    /// Only use at explicit exit/test boundaries, never on move/resize.
    pub fn flush(&self) -> io::Result<()> {
        let mut state = self.shared.state.lock().unwrap();
        let target = state.revision;
        state.error = None;
        state.force = state.latest.is_some();
        self.shared.wake.notify_one();
        while state.completed < target && state.error.is_none() && !state.stopped {
            state = self.shared.wake.wait(state).unwrap();
        }
        if let Some(error) = &state.error {
            Err(io::Error::other(error.clone()))
        } else if state.completed < target {
            Err(io::Error::other("geometry writer stopped before flush"))
        } else {
            Ok(())
        }
    }
}
impl Drop for GeometryWriter {
    fn drop(&mut self) {
        {
            let mut state = self.shared.state.lock().unwrap();
            state.stopping = true;
            self.shared.wake.notify_one();
        }
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}
pub(crate) fn atomic_write(path: &Path, contents: &str) -> io::Result<()> {
    static NEXT: AtomicU64 = AtomicU64::new(0);
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("geometry path has no parent"))?;
    fs::create_dir_all(parent)?;
    let name = path
        .file_name()
        .ok_or_else(|| io::Error::other("geometry path has no filename"))?
        .to_string_lossy();
    for _ in 0..16 {
        let temporary = parent.join(format!(
            ".{name}.{}.{}.tmp",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        let mut file = match OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temporary)
        {
            Ok(file) => file,
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        };
        let result = file
            .write_all(contents.as_bytes())
            .and_then(|_| file.sync_all());
        drop(file);
        let result = result.and_then(|_| fs::rename(&temporary, path));
        if result.is_err() {
            let _ = fs::remove_file(&temporary);
        }
        return result;
    }
    Err(io::Error::new(
        io::ErrorKind::AlreadyExists,
        "cannot allocate geometry temporary file",
    ))
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;
    fn path(name: &str) -> PathBuf {
        static NEXT: AtomicU64 = AtomicU64::new(0);
        std::env::temp_dir()
            .join(format!(
                "geometry-writer-{}-{}-{name}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ))
            .join("geometry.txt")
    }
    #[test]
    fn burst_and_exit_keep_only_latest() {
        let p = path("burst");
        let w = GeometryWriter::new(p.clone()).unwrap();
        for i in 0..1000 {
            w.schedule(i.to_string());
        }
        assert!(!p.exists());
        w.flush().unwrap();
        assert_eq!(fs::read_to_string(&p).unwrap(), "999");
        w.schedule("exit".into());
        drop(w);
        assert_eq!(fs::read_to_string(&p).unwrap(), "exit");
        fs::remove_dir_all(p.parent().unwrap()).unwrap();
    }
    #[test]
    fn publication_during_blocked_write_survives() {
        let (started_tx, started_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let values = Arc::new(Mutex::new(Vec::new()));
        let output = values.clone();
        let mut first = true;
        let w = GeometryWriter::with_sink(move |s| {
            if first {
                first = false;
                started_tx.send(()).unwrap();
                release_rx.recv().unwrap();
            }
            output.lock().unwrap().push(s.to_owned());
            Ok(())
        })
        .unwrap();
        w.schedule("old".into());
        w.flush_soon();
        started_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        for i in 0..1000 {
            w.schedule(i.to_string());
        }
        assert_eq!(
            w.shared.state.lock().unwrap().latest.as_ref().unwrap().1,
            "999"
        );
        release_tx.send(()).unwrap();
        w.flush().unwrap();
        assert_eq!(*values.lock().unwrap(), vec!["old", "999"]);
    }
    #[test]
    fn destroyed_window_retires_without_waiting_for_disk() {
        let (started_tx, started_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let w = GeometryWriter::with_sink(move |_| {
            started_tx.send(()).unwrap();
            release_rx.recv().unwrap();
            Ok(())
        })
        .unwrap();
        w.schedule("final normal geometry".into());
        w.flush_soon();
        started_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        w.finish_soon();
        w.schedule("destroyed window must not overwrite".into());
        assert!(!w.is_finished());
        assert!(w.shared.state.lock().unwrap().latest.is_none());
        release_tx.send(()).unwrap();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !w.is_finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        assert!(w.is_finished());
        w.flush().unwrap();
    }
    #[test]
    fn reopened_window_reuses_in_flight_writer_and_keeps_newest_geometry() {
        let p = path("reopen");
        let destination = p.clone();
        let (started_tx, started_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let mut first = true;
        let old = Arc::new(
            GeometryWriter::with_sink(move |contents| {
                if first {
                    first = false;
                    started_tx.send(()).unwrap();
                    release_rx.recv().unwrap();
                }
                atomic_write(&destination, contents)
            })
            .unwrap(),
        );
        let registry = Mutex::new(vec![("main".to_owned(), old.clone())]);
        old.schedule("old window".into());
        old.flush_soon();
        started_rx.recv_timeout(Duration::from_secs(2)).unwrap();
        old.finish_soon();
        assert_eq!(
            cached_record(&registry, "main").as_deref(),
            Some("old window")
        );
        let reopened = writer_for_window(&registry, "main", p.clone()).unwrap();
        let reused = Arc::ptr_eq(&old, &reopened);
        reopened.schedule("new window".into());
        assert_eq!(
            cached_record(&registry, "main").as_deref(),
            Some("new window")
        );
        release_tx.send(()).unwrap();
        reopened.flush().unwrap();
        old.flush().unwrap();
        let actual = fs::read_to_string(&p).unwrap();
        fs::remove_dir_all(p.parent().unwrap()).unwrap();
        assert!(
            reused,
            "one label must serialize writes across window generations"
        );
        assert_eq!(actual, "new window");
        assert_eq!(registry.lock().unwrap().len(), 1);
    }
    #[test]
    fn reopened_window_replaces_finished_writer() {
        let p = path("reopen-finished");
        let registry = Mutex::new(Vec::new());
        let old = writer_for_window(&registry, "main", p.clone()).unwrap();
        old.schedule("old".into());
        old.finish_soon();
        let deadline = Instant::now() + Duration::from_secs(2);
        while !old.is_finished() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(1));
        }
        assert!(old.is_finished());
        let new = writer_for_window(&registry, "main", p.clone()).unwrap();
        assert!(!Arc::ptr_eq(&old, &new));
        new.schedule("new".into());
        new.flush().unwrap();
        assert_eq!(fs::read_to_string(&p).unwrap(), "new");
        assert_eq!(registry.lock().unwrap().len(), 1);
        fs::remove_dir_all(p.parent().unwrap()).unwrap();
    }
    #[test]
    fn cache_keeps_normal_geometry_for_close_and_other_labels() {
        let p = path("cache");
        let registry = Mutex::new(Vec::new());
        let writer = writer_for_window(&registry, "main", p.clone()).unwrap();
        writer.schedule("normal geometry".into());
        writer.flush_soon();
        writer.flush().unwrap();
        // Maximize/hide must not publish their non-normal surface. A reopen can
        // still restore this record after the pending write slot is drained.
        assert_eq!(
            cached_record(&registry, "main").as_deref(),
            Some("normal geometry")
        );
        assert!(cached_record(&registry, "other").is_none());
        assert!(writer.shared.state.lock().unwrap().latest.is_none());
        fs::remove_dir_all(p.parent().unwrap()).unwrap();
    }
    #[test]
    fn failure_is_reported_and_latest_is_retryable() {
        let fail = Arc::new(std::sync::atomic::AtomicBool::new(true));
        let control = fail.clone();
        let p = path("retry");
        let dest = p.clone();
        let w = GeometryWriter::with_sink(move |s| {
            if control.load(Ordering::SeqCst) {
                Err(io::Error::other("injected disk failure"))
            } else {
                atomic_write(&dest, s)
            }
        })
        .unwrap();
        w.schedule("retained".into());
        assert!(w.flush().is_err());
        fail.store(false, Ordering::SeqCst);
        w.flush().unwrap();
        assert_eq!(fs::read_to_string(&p).unwrap(), "retained");
        drop(w);
        fs::remove_dir_all(p.parent().unwrap()).unwrap();
    }
    #[test]
    fn permanent_error_does_not_hang_shutdown() {
        let w = GeometryWriter::with_sink(|_| Err(io::Error::other("permanent"))).unwrap();
        w.schedule("final".into());
        assert!(w.flush().is_err());
        drop(w);
    }
    #[test]
    fn concurrent_atomic_writers_leave_one_complete_record() {
        let p = path("concurrent");
        let mut workers = vec![];
        for i in 0..8 {
            let p = p.clone();
            workers.push(thread::spawn(move || {
                for _ in 0..20 {
                    atomic_write(&p, &i.to_string().repeat(8192)).unwrap();
                }
            }));
        }
        for worker in workers {
            worker.join().unwrap();
        }
        let contents = fs::read_to_string(&p).unwrap();
        assert_eq!(contents.len(), 8192);
        assert!(contents.bytes().all(|b| b == contents.as_bytes()[0]));
        assert_eq!(fs::read_dir(p.parent().unwrap()).unwrap().count(), 1);
        fs::remove_dir_all(p.parent().unwrap()).unwrap();
    }
    #[test]
    fn sustained_updates_reach_disk_before_idle() {
        let (tx, rx) = mpsc::channel();
        let w = Arc::new(
            GeometryWriter::with_sink(move |_| {
                tx.send(()).unwrap();
                Ok(())
            })
            .unwrap(),
        );
        let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let publisher = {
            let w = w.clone();
            let stop = stop.clone();
            thread::spawn(move || {
                while !stop.load(Ordering::SeqCst) {
                    w.schedule("moving".into());
                    thread::sleep(Duration::from_millis(10));
                }
            })
        };
        let result = rx.recv_timeout(Duration::from_secs(3));
        stop.store(true, Ordering::SeqCst);
        publisher.join().unwrap();
        assert!(result.is_ok());
        w.flush().unwrap();
    }
}

#[cfg(test)]
mod failure_boundary_tests {
    use super::*;
    use std::sync::mpsc;
    #[test]
    fn repeated_flush_must_respect_actual_retry_deadline() {
        let mut calls = 0;
        let (tx, rx) = mpsc::channel();
        let writer = GeometryWriter::with_sink(move |_| {
            calls += 1;
            if calls == 1 {
                Err(io::Error::other("synthetic first write failure"))
            } else {
                tx.send(Instant::now()).unwrap();
                Ok(())
            }
        })
        .unwrap();
        writer.schedule("latest".into());
        writer.flush_soon();
        let state = writer.shared.state.lock().unwrap();
        let (state, timeout) = writer
            .shared
            .wake
            .wait_timeout_while(state, Duration::from_secs(3), |s| s.retry.is_none())
            .unwrap();
        assert!(!timeout.timed_out());
        let deadline = state.retry.unwrap();
        drop(state);
        for _ in 0..100 {
            writer.flush_soon();
        }
        let actual = rx.recv_timeout(Duration::from_secs(3)).unwrap();
        drop(writer);
        println!(
            "synthetic_failure retry_deadline_violated={} early_by_us={}",
            actual < deadline,
            deadline.saturating_duration_since(actual).as_micros()
        );
        assert!(
            actual >= deadline,
            "lifecycle flush bypassed the actual retry deadline"
        );
    }
    #[test]
    fn stopping_must_attempt_newer_pending_after_old_inflight_failure() {
        let (started_tx, started_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let values = Arc::new(Mutex::new(Vec::new()));
        let output = values.clone();
        let writer = GeometryWriter::with_sink(move |value| {
            if value == "old" {
                started_tx.send(()).unwrap();
                release_rx.recv_timeout(Duration::from_secs(5)).unwrap();
                Err(io::Error::other(
                    "synthetic old inflight failure during retirement",
                ))
            } else {
                output.lock().unwrap().push(value.to_owned());
                Ok(())
            }
        })
        .unwrap();
        writer.schedule("old".into());
        writer.flush_soon();
        started_rx.recv_timeout(Duration::from_secs(3)).unwrap();
        writer.schedule("new".into());
        let shared = writer.shared.clone();
        writer.finish_soon(); // Production Destroyed/retirement entry point.
        let join = thread::spawn(move || drop(writer));
        let state = shared.state.lock().unwrap();
        let (state, timeout) = shared
            .wake
            .wait_timeout_while(state, Duration::from_secs(3), |s| !s.stopping)
            .unwrap();
        let stopping = state.stopping;
        drop(state);
        release_tx.send(()).unwrap();
        join.join().unwrap();
        assert!(!timeout.timed_out() && stopping);
        let state = shared.state.lock().unwrap();
        let pending = state.latest.as_ref().map(|(_, s)| s.clone());
        println!("synthetic_failure stopped={} completed={} revision={} still_pending={pending:?} saved={:?}",state.stopped,state.completed,state.revision,values.lock().unwrap());
        drop(state);
        assert_eq!(
            *values.lock().unwrap(),
            vec!["new".to_owned()],
            "newer pending value was never attempted before stop"
        );
    }

    #[test]
    fn stopping_latest_failure_is_bounded_after_attempting_newer_pending() {
        let (started_tx, started_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let calls = Arc::new(Mutex::new(Vec::new()));
        let attempts = calls.clone();
        let writer = GeometryWriter::with_sink(move |value| {
            attempts.lock().unwrap().push(value.to_owned());
            if value == "old" {
                started_tx.send(()).unwrap();
                release_rx.recv_timeout(Duration::from_secs(5)).unwrap();
            }
            Err(io::Error::other("synthetic permanent failure"))
        })
        .unwrap();
        writer.schedule("old".into());
        writer.flush_soon();
        started_rx.recv_timeout(Duration::from_secs(3)).unwrap();
        writer.schedule("new".into());
        let shared = writer.shared.clone();
        writer.finish_soon();
        let (done_tx, done_rx) = mpsc::channel();
        let join = thread::spawn(move || {
            drop(writer);
            done_tx.send(()).unwrap();
        });
        let state = shared.state.lock().unwrap();
        let (state, timeout) = shared
            .wake
            .wait_timeout_while(state, Duration::from_secs(3), |s| !s.stopping)
            .unwrap();
        let stopping = state.stopping;
        drop(state);
        release_tx.send(()).unwrap();
        assert!(!timeout.timed_out() && stopping);
        let finished = done_rx.recv_timeout(Duration::from_secs(3)).is_ok();
        assert!(
            finished,
            "stopping retried a final failed value without a bound"
        );
        join.join().unwrap();
        assert_eq!(
            *calls.lock().unwrap(),
            vec!["old".to_owned(), "new".to_owned()]
        );
        assert!(shared.state.lock().unwrap().stopped);
    }
}
