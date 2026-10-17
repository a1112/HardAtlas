//! Keep normal user geometry, correcting only windows outside the current work area.
use super::window_geometry::{self, Bounds, Geometry, Screen};
use std::{
    fs,
    path::Path,
    sync::{Arc, Mutex},
};
#[path = "geometry_writer.rs"]
mod geometry_writer;
use geometry_writer::GeometryWriter;

#[derive(Clone, Copy)]
struct Placement {
    geometry: Geometry,
    outer: Option<Bounds>,
}
#[derive(Default)]
struct Writers(Mutex<Vec<(String, Arc<GeometryWriter>)>>);
use tauri::{Manager, WebviewWindow, WindowEvent};

fn snapshot(window: &WebviewWindow) -> Option<Placement> {
    if window.is_minimized().ok()? || window.is_maximized().ok()? || window.is_fullscreen().ok()? {
        return None;
    }
    let scale = window.scale_factor().ok()?;
    if !scale.is_finite() || scale <= 0.0 {
        return None;
    }
    let position = window.outer_position().ok()?;
    let size = window.inner_size().ok()?.to_logical::<f64>(scale);
    let outer = window.outer_size().ok()?;
    if size.width <= 0.0 || size.height <= 0.0 || outer.width == 0 || outer.height == 0 {
        return None;
    }
    Some(Placement {
        geometry: Geometry {
            x: position.x as f64,
            y: position.y as f64,
            width: size.width,
            height: size.height,
        },
        outer: Some(Bounds {
            x: position.x as f64,
            y: position.y as f64,
            width: outer.width as f64,
            height: outer.height as f64,
        }),
    })
}
fn read(path: &Path) -> Option<Placement> {
    let text = fs::read_to_string(path).ok()?;
    let values: Vec<f64> = text
        .split_whitespace()
        .map(str::parse)
        .collect::<Result<_, _>>()
        .ok()?;
    if ![4, 6].contains(&values.len()) || !values.iter().all(|v| v.is_finite()) {
        return None;
    }
    let g = Geometry {
        x: values[0],
        y: values[1],
        width: values[2],
        height: values[3],
    };
    if !g.valid() {
        return None;
    }
    let outer = if values.len() == 6 {
        if values[4] <= 0.0 || values[5] <= 0.0 {
            return None;
        }
        Some(Bounds {
            x: g.x,
            y: g.y,
            width: values[4],
            height: values[5],
        })
    } else {
        None
    };
    Some(Placement { geometry: g, outer })
}
fn publish(window: &WebviewWindow, writer: &GeometryWriter) {
    if let Some(saved) = snapshot(window) {
        let g = saved.geometry;
        let outer = saved.outer.expect("live snapshots have outer dimensions");
        writer.schedule(format!(
            "{} {} {} {} {} {}\n",
            g.x, g.y, g.width, g.height, outer.width, outer.height
        ));
    }
}

pub fn capture_all(app: &tauri::AppHandle) {
    if let Some(writers) = app.try_state::<Writers>() {
        let entries = writers.0.lock().unwrap().clone();
        for (label, writer) in entries {
            if let Some(window) = app.get_webview_window(&label) {
                publish(&window, &writer);
            }
            writer.flush_soon();
        }
    }
}

pub fn flush_all(app: &tauri::AppHandle) {
    if let Some(writers) = app.try_state::<Writers>() {
        let entries = writers.0.lock().unwrap().clone();
        for (label, writer) in entries {
            if let Err(error) = writer.flush() {
                eprintln!("cannot save window {label}: {error}");
            }
        }
    }
}

fn apply(
    window: &WebviewWindow,
    saved: Option<Placement>,
    default: (f64, f64),
    minimum: (f64, f64),
) {
    if window.is_maximized().unwrap_or(false) || window.is_fullscreen().unwrap_or(false) {
        return;
    }
    let Ok(monitors) = window.available_monitors() else {
        return;
    };
    // Primary first gives a deterministic fallback when a saved display is unplugged.
    let primary = window.primary_monitor().ok().flatten();
    let mut screens: Vec<Screen> = monitors
        .iter()
        .map(|m| {
            let area = m.work_area();
            Screen {
                area: Bounds {
                    x: area.position.x as f64,
                    y: area.position.y as f64,
                    width: area.size.width as f64,
                    height: area.size.height as f64,
                },
                scale: m.scale_factor(),
            }
        })
        .collect();
    if let Some(primary) = primary {
        if let Some(index) = monitors
            .iter()
            .position(|m| m.position() == primary.position())
        {
            screens.swap(0, index);
        }
    }
    if screens.is_empty() {
        return;
    }
    let selected = saved.and_then(|p| p.outer).map_or_else(
        || window_geometry::select_screen(saved.map(|p| p.geometry), &screens),
        |rect| window_geometry::select_screen_physical(rect, &screens),
    );
    let screen = screens[selected];
    let frame = match (
        window.outer_size(),
        window.inner_size(),
        window.scale_factor(),
    ) {
        (Ok(outer), Ok(inner), Ok(scale)) => (
            (outer.width.saturating_sub(inner.width)) as f64 / scale,
            (outer.height.saturating_sub(inner.height)) as f64 / scale,
        ),
        _ => (0.0, 32.0),
    };
    let (geometry, min) =
        window_geometry::fit(saved.map(|p| p.geometry), default, minimum, screen, frame);
    // Adopt the destination monitor before applying logical dimensions.
    let position =
        tauri::PhysicalPosition::new(geometry.x.round() as i32, geometry.y.round() as i32);
    if window.outer_position().ok() != Some(position) {
        let _ = window.set_position(position);
    }
    let _ = window.set_min_size(Some(tauri::LogicalSize::new(min.0, min.1)));
    let target =
        tauri::LogicalSize::new(geometry.width, geometry.height).to_physical::<u32>(screen.scale);
    if window.inner_size().ok() != Some(target) {
        let _ = window.set_size(target);
    }
}

pub fn install(
    app: &tauri::App,
    label: &str,
    default: (f64, f64),
    minimum: (f64, f64),
    show: bool,
) {
    let Some(window) = app.get_webview_window(label) else {
        return;
    };
    let path = app
        .path()
        .app_config_dir()
        .ok()
        .map(|base| base.join(format!("window-geometry-{label}-v2.txt")));
    apply(
        &window,
        path.as_ref().and_then(|path| {
            read(path)
                .or_else(|| read(&path.with_file_name(format!("window-geometry-{label}-v1.txt"))))
        }),
        default,
        minimum,
    );
    let writer = path.and_then(|path| match GeometryWriter::new(path) {
        Ok(writer) => Some(Arc::new(writer)),
        Err(error) => {
            eprintln!("cannot start window geometry writer: {error}");
            None
        }
    });
    if let Some(writer) = &writer {
        if app.manage(Writers::default()) {
            let plugin = tauri::plugin::Builder::<tauri::Wry, ()>::new("window-geometry-writer")
                .on_event(|app, event| match event {
                    tauri::RunEvent::ExitRequested { .. } => capture_all(app),
                    tauri::RunEvent::Exit => flush_all(app),
                    _ => {}
                })
                .build();
            if let Err(error) = app.handle().plugin(plugin) {
                eprintln!("cannot install geometry exit handler: {error}");
            }
        }
        let writers = app.state::<Writers>();
        let mut entries = writers.0.lock().unwrap();
        // Finished workers can be dropped without waiting for filesystem I/O.
        entries.retain(|(_, writer)| !writer.is_finished());
        entries.push((label.to_owned(), writer.clone()));
        drop(entries);
        publish(&window, writer);
    }
    if show {
        let _ = window.show();
    }
    let tracked = window.clone();
    window.on_window_event(move |event| {
        if matches!(event, WindowEvent::Destroyed) {
            if let Some(writer) = &writer {
                writer.finish_soon();
            }
            return;
        }
        if matches!(event, WindowEvent::ScaleFactorChanged { .. }) {
            apply(&tracked, snapshot(&tracked), default, minimum);
        }
        if matches!(
            event,
            WindowEvent::Moved(_)
                | WindowEvent::Resized(_)
                | WindowEvent::CloseRequested { .. }
                | WindowEvent::ScaleFactorChanged { .. }
        ) {
            if let Some(writer) = &writer {
                publish(&tracked, writer);
                if matches!(event, WindowEvent::CloseRequested { .. }) {
                    writer.flush_soon();
                }
            }
        }
    });
}

#[cfg(test)]
mod record_tests {
    use super::*;
    #[test]
    fn reads_legacy_and_physical_records_but_rejects_corruption() {
        let dir = std::env::temp_dir().join(format!("placement-records-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let p = dir.join("record.txt");
        fs::write(&p, "-500 0 600 600").unwrap();
        assert!(read(&p).unwrap().outer.is_none());
        fs::write(&p, "-500 0 600 600 600 628").unwrap();
        assert_eq!(read(&p).unwrap().outer.unwrap().height, 628.0);
        for text in [
            "0 0 NaN 500",
            "0 0 600 600 -1 628",
            "0 0 600 600 600 inf",
            "0 0 600 600 600",
        ] {
            fs::write(&p, text).unwrap();
            assert!(read(&p).is_none());
        }
        fs::remove_dir_all(dir).unwrap();
    }
}
