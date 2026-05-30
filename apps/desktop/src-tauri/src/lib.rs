use serde::Deserialize;
use std::{
    io::{BufRead, BufReader},
    process::{Child, Command, Stdio},
    sync::Mutex,
    thread,
};
use tauri::{Emitter, Manager};

struct ServiceState {
    child: Mutex<Option<Child>>,
}

#[derive(Debug, Deserialize)]
struct ServiceReady {
    url: String,
    token: String,
}

fn python_command() -> Command {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let repo_root = std::path::Path::new(manifest_dir)
        .parent()
        .and_then(|path| path.parent())
        .and_then(|path| path.parent())
        .expect("desktop app must live under apps/desktop/src-tauri");
    let venv_python = repo_root.join(".venv").join("bin").join("python");
    if venv_python.exists() {
        Command::new(venv_python)
    } else {
        Command::new("python3")
    }
}

fn start_x2md_service(app: tauri::AppHandle) {
    thread::spawn(move || {
        let mut command = python_command();
        command
            .arg("-m")
            .arg("x2md")
            .arg("desktop")
            .arg("--port")
            .arg("0")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                let _ = app.emit("x2md-service-error", format!("failed to start x2md: {error}"));
                return;
            }
        };

        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                let _ = app.emit("x2md-service-error", "x2md stdout unavailable");
                return;
            }
        };

        {
            let state = app.state::<ServiceState>();
            *state.child.lock().expect("service child lock poisoned") = Some(child);
        }

        let mut lines = BufReader::new(stdout).lines();
        if let Some(Ok(line)) = lines.next() {
            match serde_json::from_str::<ServiceReady>(&line) {
                Ok(ready) => {
                    let app_url = format!("{}?x2md_token={}", ready.url, ready.token);
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.navigate(url::Url::parse(&app_url).expect("valid x2md URL"));
                    }
                    let _ = app.emit("x2md-service-ready", ready.url);
                }
                Err(error) => {
                    let _ = app.emit(
                        "x2md-service-error",
                        format!("invalid x2md startup output: {error}: {line}"),
                    );
                }
            }
        } else {
            let _ = app.emit("x2md-service-error", "x2md exited before reporting a URL");
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ServiceState {
            child: Mutex::new(None),
        })
        .setup(|app| {
            start_x2md_service(app.handle().clone());
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                let state = window.state::<ServiceState>();
                let child = state.child.lock().expect("service child lock poisoned").take();
                if let Some(mut child) = child {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running x2md desktop");
}
