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
                    let due =
                        (state.last.unwrap() + DEBOUNCE).min(state.first.unwrap() + MAX_DELAY);
                    let due = state.retry.map_or(due, |retry| due.max(retry));
                    if !state.force && !state.stopping && now < due {
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
                            if state.latest.is_none() {
                                state.latest = Some((revision, contents));
                                let now = Instant::now();
                                state.first = Some(now);
                                state.last = Some(now);
                            }
                            state.error = Some(error.to_string());
                            state.retry = Some(Instant::now() + RETRY);
                            if state.stopping {
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
        }).unwrap();
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
