use serde::{Deserialize, Serialize};
use std::{
    collections::VecDeque,
    env, fs,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::Duration,
};
use tauri::{Emitter, Manager};

const SERVICE_ARGS: [&str; 5] = ["-m", "x2md", "desktop", "--port", "0"];
const LOG_TAIL_LIMIT: usize = 80;
const SERVICE_READY_ATTEMPTS: usize = 120;
const SERVICE_READY_INTERVAL: Duration = Duration::from_millis(250);

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
    source: String,
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

struct RuntimeConfig {
    python: PathBuf,
    working_dir: PathBuf,
    source: String,
}

fn compiled_repo_root() -> Option<&'static Path> {
    let manifest_dir = env!("CARGO_MANIFEST_DIR");
    let root = Path::new(manifest_dir)
        .parent()
        .and_then(|path| path.parent())
        .and_then(|path| path.parent())
        .expect("desktop app must live under apps/desktop/src-tauri");
    root.join("pyproject.toml").exists().then_some(root)
}

fn env_python() -> Option<PathBuf> {
    env::var_os("X2MD_DESKTOP_PYTHON")
        .or_else(|| env::var_os("X2MD_PYTHON"))
        .map(PathBuf::from)
        .filter(|path| path.exists())
}

fn venv_python(root: &Path) -> PathBuf {
    if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    }
}

fn bundled_python_candidates(resource_dir: &Path) -> Vec<PathBuf> {
    if cfg!(windows) {
        vec![
            resource_dir
                .join("x2md-runtime")
                .join("Scripts")
                .join("python.exe"),
            resource_dir
                .join("x2md-runtime")
                .join("python")
                .join("python.exe"),
            resource_dir.join("python").join("python.exe"),
        ]
    } else {
        vec![
            resource_dir
                .join("x2md-runtime")
                .join("python")
                .join("bin")
                .join("python3"),
            resource_dir
                .join("x2md-runtime")
                .join("python")
                .join("bin")
                .join("python"),
            resource_dir
                .join("x2md-runtime")
                .join("bin")
                .join("python3"),
            resource_dir.join("x2md-runtime").join("bin").join("python"),
            resource_dir.join("python").join("bin").join("python3"),
            resource_dir.join("python").join("bin").join("python"),
        ]
    }
}

fn fallback_python() -> PathBuf {
    if cfg!(windows) {
        PathBuf::from("python")
    } else {
        PathBuf::from("python3")
    }
}

fn resolve_runtime(app: &tauri::AppHandle) -> RuntimeConfig {
    let resource_dir = app.path().resource_dir().ok();

    if let Some(python) = env_python() {
        return RuntimeConfig {
            working_dir: python
                .parent()
                .map(Path::to_path_buf)
                .or_else(|| env::current_dir().ok())
                .unwrap_or_else(|| PathBuf::from(".")),
            python,
            source: "env".to_string(),
        };
    }

    if let Some(resource_dir) = resource_dir.as_ref() {
        if let Some(python) = bundled_python_candidates(resource_dir)
            .into_iter()
            .find(|path| path.exists())
        {
            return RuntimeConfig {
                python,
                working_dir: resource_dir.clone(),
                source: "bundled".to_string(),
            };
        }
    }

    if let Some(repo_root) = compiled_repo_root() {
        let python = venv_python(repo_root);
        if python.exists() {
            return RuntimeConfig {
                python,
                working_dir: repo_root.to_path_buf(),
                source: "dev-venv".to_string(),
            };
        }
    }

    let working_dir = resource_dir
        .clone()
        .or_else(|| compiled_repo_root().map(Path::to_path_buf))
        .or_else(|| env::current_dir().ok())
        .unwrap_or_else(|| PathBuf::from("."));
    RuntimeConfig {
        python: fallback_python(),
        working_dir,
        source: "system".to_string(),
    }
}

fn model_cache_dir(app: &tauri::AppHandle, runtime: &RuntimeConfig) -> PathBuf {
    app.path()
        .app_cache_dir()
        .unwrap_or_else(|_| runtime.working_dir.join("cache"))
        .join("models")
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

fn wait_for_service(url: &str) -> Result<(), String> {
    let health_url = format!("{}/healthz", url.trim_end_matches('/'));
    let parsed_url = url::Url::parse(&health_url).map_err(|error| error.to_string())?;
    let host = parsed_url
        .host_str()
        .ok_or_else(|| "service URL has no host".to_string())?
        .to_string();
    let port = parsed_url
        .port_or_known_default()
        .ok_or_else(|| "service URL has no port".to_string())?;
    let path = parsed_url.path().to_string();
    let request =
        format!("GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n");
    let address = format!("{host}:{port}");
    let mut last_error = "service did not respond".to_string();

    for _ in 0..SERVICE_READY_ATTEMPTS {
        match std::net::TcpStream::connect(&address) {
            Ok(mut stream) => {
                use std::io::{Read, Write};
                let _ = stream.set_read_timeout(Some(Duration::from_secs(2)));
                let _ = stream.set_write_timeout(Some(Duration::from_secs(2)));
                if let Err(error) = stream.write_all(request.as_bytes()) {
                    last_error = error.to_string();
                    thread::sleep(SERVICE_READY_INTERVAL);
                    continue;
                }
                let mut response = String::new();
                match stream.read_to_string(&mut response) {
                    Ok(_)
                        if response.starts_with("HTTP/1.1 200")
                            || response.starts_with("HTTP/1.0 200") =>
                    {
                        return Ok(());
                    }
                    Ok(_) => {
                        last_error = response
                            .lines()
                            .next()
                            .unwrap_or("unexpected response")
                            .to_string();
                    }
                    Err(error) => {
                        last_error = error.to_string();
                    }
                }
            }
            Err(error) => {
                last_error = error.to_string();
            }
        }
        thread::sleep(SERVICE_READY_INTERVAL);
    }

    Err(format!(
        "x2md service was not ready at {health_url}: {last_error}"
    ))
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
        let runtime = resolve_runtime(&app);
        let model_cache = model_cache_dir(&app, &runtime);
        let _ = fs::create_dir_all(&model_cache);
        let args = SERVICE_ARGS.map(String::from).to_vec();
        let _ = app.emit(
            "x2md-service-starting",
            ServiceStarting {
                python: runtime.python.display().to_string(),
                args: args.clone(),
                source: runtime.source.clone(),
            },
        );

        let mut command = Command::new(&runtime.python);
        command
            .current_dir(&runtime.working_dir)
            .args(&args)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONUTF8", "1")
            .env("HF_HOME", &model_cache)
            .env("MODELSCOPE_CACHE", &model_cache)
            .env("TORCH_HOME", &model_cache)
            .env("X2MD_MODEL_CACHE", &model_cache)
            .env("X2MD_DESKTOP_RUNTIME_SOURCE", &runtime.source)
            .env("X2MD_DESKTOP_RUNTIME_PYTHON", &runtime.python)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        if runtime.source == "bundled" {
            command.env("X2MD_DESKTOP_LIGHT", "1");
        }

        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => {
                emit_service_error(
                    &app,
                    format!(
                        "failed to start x2md with {} from {}: {error}",
                        runtime.python.display(),
                        runtime.source
                    ),
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
                    if let Err(error) = wait_for_service(&ready.url) {
                        let logs = log_tail
                            .lock()
                            .expect("service log lock poisoned")
                            .iter()
                            .cloned()
                            .collect::<Vec<_>>();
                        emit_service_error(&app, error, &logs);
                        return;
                    }
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
