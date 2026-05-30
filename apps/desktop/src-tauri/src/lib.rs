use serde::{Deserialize, Serialize};
use std::{
    collections::VecDeque,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
};
use tauri::{Emitter, Manager};

const SERVICE_ARGS: [&str; 5] = ["-m", "x2md", "desktop", "--port", "0"];
const LOG_TAIL_LIMIT: usize = 80;

struct ServiceState {
    child: Mutex<Option<Child>>,
}

#[derive(Debug, Deserialize)]
struct ServiceReady {
    url: String,
    token: String,
}

#[derive(Clone, Debug, Serialize)]
struct ServiceStarting {
    python: String,
    args: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
struct ServiceLog {
    stream: String,
    message: String,
}

#[derive(Clone, Debug, Serialize)]
struct ServiceError {
    message: String,
    logs: Vec<String>,
}

fn repo_root() -> &'static Path {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    Path::new(manifest_dir)
        .parent()
        .and_then(|path| path.parent())
        .and_then(|path| path.parent())
        .expect("desktop app must live under apps/desktop/src-tauri")
}

fn python_path() -> PathBuf {
    let venv_python = if cfg!(windows) {
        repo_root().join(".venv").join("Scripts").join("python.exe")
    } else {
        repo_root().join(".venv").join("bin").join("python")
    };
    if venv_python.exists() {
        venv_python
    } else {
        PathBuf::from("python3")
    }
}

fn emit_service_error(app: &tauri::AppHandle, message: impl Into<String>, logs: &[String]) {
    let _ = app.emit(
        "x2md-service-error",
        ServiceError {
            message: message.into(),
            logs: logs.to_vec(),
        },
    );
}

fn stop_x2md_service(app: &tauri::AppHandle) {
    let state = app.state::<ServiceState>();
    let child = state
        .child
        .lock()
        .expect("service child lock poisoned")
        .take();
    if let Some(mut child) = child {
        let _ = child.kill();
        let _ = child.wait();
    }
}

fn launch_x2md_service(app: tauri::AppHandle) {
    thread::spawn(move || {
        let python = python_path();
        let args = SERVICE_ARGS.map(String::from).to_vec();
        let _ = app.emit(
            "x2md-service-starting",
            ServiceStarting {
                python: python.display().to_string(),
                args: args.clone(),
            },
        );

        let mut command = Command::new(&python);
        command
            .current_dir(repo_root())
            .args(&args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                emit_service_error(
                    &app,
                    format!("failed to start x2md with {}: {error}", python.display()),
                    &[],
                );
                return;
            }
        };

        let stdout = match child.stdout.take() {
            Some(stdout) => stdout,
            None => {
                emit_service_error(&app, "x2md stdout unavailable", &[]);
                return;
            }
        };
        let stderr = child.stderr.take();

        {
            let state = app.state::<ServiceState>();
            *state.child.lock().expect("service child lock poisoned") = Some(child);
        }

        let log_tail = Arc::new(Mutex::new(VecDeque::<String>::new()));
        if let Some(stderr) = stderr {
            let log_app = app.clone();
            let log_tail = Arc::clone(&log_tail);
            thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    {
                        let mut tail = log_tail.lock().expect("service log lock poisoned");
                        if tail.len() >= LOG_TAIL_LIMIT {
                            tail.pop_front();
                        }
                        tail.push_back(line.clone());
                    }
                    let _ = log_app.emit(
                        "x2md-service-log",
                        ServiceLog {
                            stream: "stderr".to_string(),
                            message: line,
                        },
                    );
                }
            });
        }

        let mut lines = BufReader::new(stdout).lines();
        match lines.next() {
            Some(Ok(line)) => match serde_json::from_str::<ServiceReady>(&line) {
                Ok(ready) => {
                    let app_url = format!("{}?x2md_token={}", ready.url, ready.token);
                    if let Some(window) = app.get_webview_window("main") {
                        let _ = window.navigate(url::Url::parse(&app_url).expect("valid x2md URL"));
                    }
                    let _ = app.emit("x2md-service-ready", ready.url);
                }
                Err(error) => {
                    let logs = log_tail
                        .lock()
                        .expect("service log lock poisoned")
                        .iter()
                        .cloned()
                        .collect::<Vec<_>>();
                    emit_service_error(
                        &app,
                        format!("invalid x2md startup output: {error}: {line}"),
                        &logs,
                    );
                }
            },
            Some(Err(error)) => {
                let logs = log_tail
                    .lock()
                    .expect("service log lock poisoned")
                    .iter()
                    .cloned()
                    .collect::<Vec<_>>();
                emit_service_error(
                    &app,
                    format!("failed to read x2md startup output: {error}"),
                    &logs,
                );
            }
            None => {
                let logs = log_tail
                    .lock()
                    .expect("service log lock poisoned")
                    .iter()
                    .cloned()
                    .collect::<Vec<_>>();
                emit_service_error(&app, "x2md exited before reporting a URL", &logs);
            }
        }
    });
}

#[tauri::command]
fn start_x2md_service(app: tauri::AppHandle) {
    launch_x2md_service(app);
}

#[tauri::command]
fn restart_x2md_service(app: tauri::AppHandle) {
    stop_x2md_service(&app);
    launch_x2md_service(app);
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(ServiceState {
            child: Mutex::new(None),
        })
        .invoke_handler(tauri::generate_handler![
            start_x2md_service,
            restart_x2md_service
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                stop_x2md_service(&window.app_handle());
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running x2md desktop");
}
