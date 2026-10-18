//! Fixed project updater bridge. No arbitrary commands, paths or download URLs.
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use serde_json::{json, Value};
use tauri::State;
use super::types::MaaState;

static RESERVED: AtomicBool = AtomicBool::new(false);
static STARTING: AtomicUsize = AtomicUsize::new(0);
static WORKER: Mutex<Option<Child>> = Mutex::new(None);

// Protect the entire async preparation of a task, before Maa reports running.
pub struct StartGuard;
impl StartGuard {
    pub fn acquire(state: &MaaState) -> Result<Self, String> {
        let _instances = state.instances.lock().map_err(|e| e.to_string())?;
        if RESERVED.load(Ordering::SeqCst) {
            return Err("正在安装更新，请稍后再开始任务。".into());
        }
        STARTING.fetch_add(1, Ordering::SeqCst);
        Ok(Self)
    }
}
impl Drop for StartGuard {
    fn drop(&mut self) { STARTING.fetch_sub(1, Ordering::SeqCst); }
}

fn root() -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let app = exe.parent().ok_or("无法定位助手目录")?;
    let root = app.parent().ok_or("无法定位助手目录")?.to_path_buf();
    let interface: Value = serde_json::from_slice(&std::fs::read(app.join("interface.json")).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    if interface["name"] != "CangwuMaa" || !root.join("cangwu/updater.py").is_file() {
        return Err("当前目录不支持苍雾助手更新。".into());
    }
    Ok(root)
}

fn command(root: &PathBuf, action: &str, enabled: Option<bool>, automatic: bool) -> Command {
    let mut cmd = Command::new(root.join(".venv/Scripts/python.exe"));
    cmd.current_dir(root).args(["-X", "utf8", "-B", "-m", "cangwu.updater", "--action", action])
        .env("PYTHONIOENCODING", "utf-8").env("PYTHONDONTWRITEBYTECODE", "1")
        .stdin(Stdio::null()).stderr(Stdio::null());
    if let Some(on) = enabled { cmd.args(["--enabled", if on { "on" } else { "off" }]); }
    if automatic { cmd.arg("--automatic"); }
    #[cfg(windows)] {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }
    cmd
}

fn short(root: &PathBuf, action: &str, enabled: Option<bool>) -> Result<Value, String> {
    let mut child = command(root, action, enabled, false).stdout(Stdio::piped()).spawn().map_err(|e| e.to_string())?;
    let deadline = Instant::now() + Duration::from_secs(8);
    loop {
        if child.try_wait().map_err(|e| e.to_string())?.is_some() { break; }
        if Instant::now() >= deadline {
            let _ = child.kill(); let _ = child.wait();
            return Err("读取更新设置超时，请重试。".into());
        }
        std::thread::sleep(Duration::from_millis(40));
    }
    let output = child.wait_with_output().map_err(|e| e.to_string())?;
    let value: Value = serde_json::from_slice(&output.stdout).map_err(|_| "更新设置无法读取".to_string())?;
    if !output.status.success() { return Err(value["error"].as_str().unwrap_or("更新设置无法读取").into()); }
    Ok(value)
}

fn execute(app: tauri::AppHandle, state: Arc<MaaState>, action: String, enabled: Option<bool>, automatic: bool) -> Result<Value, String> {
    if !["status", "configure", "cancel", "check", "prepare", "sync", "install"].contains(&action.as_str()) {
        return Err("不支持的更新操作".into());
    }
    let root = root()?;
    if ["configure", "cancel"].contains(&action.as_str()) && RESERVED.load(Ordering::SeqCst) {
        return Err("正在重启以安装更新，请稍后再修改更新设置。".into());
    }
    if ["status", "configure", "cancel"].contains(&action.as_str()) { return short(&root, &action, enabled); }
    let mut worker = WORKER.lock().map_err(|e| e.to_string())?;
    if let Some(child) = worker.as_mut() {
        if child.try_wait().map_err(|e| e.to_string())?.is_none() { return Ok(json!({"deferred": true})); }
    }
    *worker = None;
    if action != "install" {
        let child = command(&root, &action, None, automatic).stdout(Stdio::null()).spawn().map_err(|e| e.to_string())?;
        *worker = Some(child);
        return Ok(json!({"started": true}));
    }
    let current = short(&root, "status", None)?;
    if current["phase"] != "ready" { return Err("请先下载并校验更新。".into()); }
    if current["automatic"] == true && current["auto_update"] != true { return Ok(json!({"deferred": true})); }
    {
        let instances = state.instances.lock().map_err(|e| e.to_string())?;
        if STARTING.load(Ordering::SeqCst) > 0 || instances.values().any(|i|
            i.stop_in_progress || i.tasker.as_ref().is_some_and(|t| t.running())
            || i.task_run_state.overall_status.as_deref() == Some("Running")) {
            return Ok(json!({"deferred": true}));
        }
        if RESERVED.swap(true, Ordering::SeqCst) { return Ok(json!({"deferred": true})); }
    }
    let result = (|| {
        let mut cmd = command(&root, "install", None, false);
        cmd.args(["--parent-pid", &std::process::id().to_string()]).stdout(Stdio::null());
        let mut child = cmd.spawn().map_err(|e| e.to_string())?;
        let deadline = Instant::now() + Duration::from_secs(30);
        loop {
            let status: Value = std::fs::read(root.join("runtime/updates/status.json")).ok()
                .and_then(|b| serde_json::from_slice(&b).ok()).unwrap_or(Value::Null);
            if status["pid"].as_u64() == Some(child.id() as u64) && status["phase"] == "waiting_exit" {
                *worker = Some(child);
                std::thread::spawn(move || {
                    std::thread::sleep(Duration::from_millis(700));
                    app.exit(0);
                });
                return Ok(json!({"restarting": true}));
            }
            if child.try_wait().map_err(|e| e.to_string())?.is_some() {
                return Err(status["message"].as_str().unwrap_or("更新未开始，原程序保持不变。").to_string());
            }
            if Instant::now() >= deadline {
                let _ = child.kill(); let _ = child.wait();
                return Err("更新准备超时，原程序保持不变。".into());
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    })();
    if result.is_err() { RESERVED.store(false, Ordering::SeqCst); }
    result
}

pub async fn run(app: tauri::AppHandle, state: Arc<MaaState>, action: String, enabled: Option<bool>, automatic: bool) -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(move || execute(app, state, action, enabled, automatic)).await.map_err(|e| e.to_string())?
}

#[tauri::command]
pub async fn cangwu_update(app: tauri::AppHandle, state: State<'_, Arc<MaaState>>, action: String,
    enabled: Option<bool>, automatic: Option<bool>) -> Result<Value, String> {
    run(app, state.inner().clone(), action, enabled, automatic.unwrap_or(false)).await
}
