//! Folder comparison engine.

use pyo3::prelude::*;
use pyo3::exceptions::{PyFileNotFoundError, PyRuntimeError, PyValueError};

// THIS LINE imports the structs from lib.rs
use crate::{RaplError, RaplReader};

use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread::sleep;
use std::time::{Duration, Instant};

#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn run_comparison(
    old_dir: &str,
    new_dir: &str,
    entry_file: &str,
    runs: u32,
    python_exe: &str,
    tier: &str,
    timeout_secs: u64,
) -> PyResult<(Vec<f64>, Vec<f64>)> {
    let old_path = PathBuf::from(old_dir)
        .canonicalize()
        .map_err(|e| PyValueError::new_err(format!("invalid old path: {}", e)))?;
    let new_path = PathBuf::from(new_dir)
        .canonicalize()
        .map_err(|e| PyValueError::new_err(format!("invalid new path: {}", e)))?;

    if !old_path.join(entry_file).exists() || !new_path.join(entry_file).exists() {
        return Err(PyFileNotFoundError::new_err(
            "entry file missing in one or both folders",
        ));
    }

    let reader: Option<RaplReader> = if tier == "A" {
        Some(
            RaplReader::new_impl("/sys/class/powercap")
                .map_err(|e| PyRuntimeError::new_err(e.0))?,
        )
    } else {
        None
    };

    println!("\n=== dol8 COMPARISON ENGINE (Tier {}) ===", tier);
    println!("Old: {}", old_path.display());
    println!("New: {}", new_path.display());
    println!("Runs: {} per side | timeout: {}s/child", runs, timeout_secs);
    
    if let Some(r) = reader.as_ref() {
        println!("RAPL vendor: {} domains: {:?}", r.vendor(), r.domains());
    }

    let old_samples = measure_folder(
        &old_path, entry_file, runs, python_exe, tier, reader.as_ref(), timeout_secs,
    ).map_err(|e| PyRuntimeError::new_err(e.0))?;

    println!("\n[COOLDOWN] 5s thermal normalization...");
    sleep(Duration::from_secs(5));

    let new_samples = measure_folder(
        &new_path, entry_file, runs, python_exe, tier, reader.as_ref(), timeout_secs,
    ).map_err(|e| PyRuntimeError::new_err(e.0))?;

    Ok((old_samples, new_samples))
}

fn measure_folder(
    folder_path: &Path,
    entry_file: &str,
    runs: u32,
    python_exe: &str,
    tier: &str,
    reader: Option<&RaplReader>,
    timeout_secs: u64,
) -> Result<Vec<f64>, RaplError> {
    let script_path = folder_path.join(entry_file);
    let mut samples: Vec<f64> = Vec::with_capacity(runs as usize);

    for i in 0..runs {
        print!("  -> Run {}/{} ... ", i + 1, runs);
        let v = match tier {
            "A" => measure_tier_a(&script_path, folder_path, python_exe, reader, timeout_secs)?,
            "D" => measure_tier_d_perf(&script_path, folder_path, python_exe, timeout_secs)?,
            _ => measure_tier_e_cpu_time(&script_path, folder_path, python_exe, timeout_secs)?,
        };
        println!("{:.4}", v);
        samples.push(v);
    }
    Ok(samples)
}

fn measure_tier_a(
    script_path: &Path,
    folder_path: &Path,
    python_exe: &str,
    reader: Option<&RaplReader>,
    timeout_secs: u64,
) -> Result<f64, RaplError> {
    let reader = reader.ok_or_else(|| RaplError("Tier A requires a RaplReader".into()))?;
    let start = reader.read_all_impl()?;
    let ok = spawn_python(python_exe, script_path, folder_path, timeout_secs)?;
    if !ok {
        return Err(RaplError("script exited non-zero".into()));
    }
    let end = reader.read_all_impl()?;
    reader.joules_between_impl(&start, &end)
}

fn measure_tier_d_perf(
    script_path: &Path,
    folder_path: &Path,
    python_exe: &str,
    timeout_secs: u64,
) -> Result<f64, RaplError> {
    if Command::new("perf").arg("--version").output().is_err() {
        return measure_tier_e_cpu_time(script_path, folder_path, python_exe, timeout_secs);
    }

    let out_file = folder_path.join(".dol8_perf.out");
    let events = "instructions,cache-misses,cycles";

    let mut cmd = Command::new("perf");
    cmd.arg("stat")
        .arg("-e").arg(events)
        .arg("-x").arg(",")
        .arg("-o").arg(&out_file)
        .arg("--")
        .arg(python_exe)
        .arg(script_path);
    cmd.current_dir(folder_path);

    let ok = spawn_cmd(cmd, timeout_secs)?;
    if !ok {
        let _ = fs::remove_file(&out_file);
        return Err(RaplError("script under perf exited non-zero".into()));
    }
    let content = fs::read_to_string(&out_file).unwrap_or_default();
    let _ = fs::remove_file(&out_file);

    let (ins, cm, _cyc) = parse_perf_output(&content);
    Ok(ins as f64 + (cm as f64) * 5.0)
}

fn parse_perf_output(s: &str) -> (u64, u64, u64) {
    let mut ins = 0u64;
    let mut cm = 0u64;
    let mut cyc = 0u64;
    for line in s.lines() {
        let parts: Vec<&str> = line.split(',').collect();
        if parts.len() < 3 { continue; }
        let raw = parts[0].trim();
        let val: u64 = raw
            .replace("<not supported>", "0")
            .replace("<not counted>", "0")
            .parse()
            .unwrap_or(0);
        let event = parts[2].trim().to_lowercase();
        if event.contains("instructions") {
            ins = val;
        } else if event.contains("cache-misses") {
            cm = val;
        } else if event.contains("cycles") && !event.contains("stalled") {
            cyc = val;
        }
    }
    (ins, cm, cyc)
}

fn measure_tier_e_cpu_time(
    script_path: &Path,
    folder_path: &Path,
    python_exe: &str,
    timeout_secs: u64,
) -> Result<f64, RaplError> {
    let start = Instant::now();
    let ok = spawn_python(python_exe, script_path, folder_path, timeout_secs)?;
    if !ok {
        return Err(RaplError("script exited non-zero".into()));
    }
    Ok(start.elapsed().as_secs_f64())
}

fn spawn_python(
    python_exe: &str,
    script_path: &Path,
    cwd: &Path,
    timeout_secs: u64,
) -> Result<bool, RaplError> {
    let mut cmd = Command::new(python_exe);
    cmd.arg(script_path).current_dir(cwd);
    spawn_cmd(cmd, timeout_secs)
}

fn spawn_cmd(mut cmd: Command, timeout_secs: u64) -> Result<bool, RaplError> {
    let mut child = cmd.spawn().map_err(|e| RaplError(format!("spawn failed: {}", e)))?;
    let start = Instant::now();
    let timeout = Duration::from_secs(timeout_secs);
    loop {
        match child.try_wait().map_err(|e| RaplError(format!("wait failed: {}", e)))? {
            Some(status) => return Ok(status.success()),
            None => {
                if start.elapsed() >= timeout {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Err(RaplError(format!("child did not finish within {}s; killed", timeout_secs)));
                }
                sleep(Duration::from_millis(50));
            }
        }
    }
}