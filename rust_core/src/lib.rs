//! dol8_core: RAPL energy reader + comparison engine bridge for Python (PyO3).

use pyo3::prelude::*;
use pyo3::exceptions::PyRuntimeError;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

// THIS LINE tells Rust to include the compare.rs file
pub mod compare;

/// Crate-wide error type.
pub struct RaplError(pub String);

impl From<std::io::Error> for RaplError {
    fn from(e: std::io::Error) -> Self {
        RaplError(e.to_string())
    }
}

struct Domain {
    name: String,
    energy_uj_path: PathBuf,
    max_range_uj_path: PathBuf,
}

#[pyclass(name = "RaplReader")]
pub struct RaplReader {
    domains: Vec<Domain>,
    max_range_uj: HashMap<String, u64>,
    vendor: String,
}

#[pymethods]
impl RaplReader {
    #[new]
    pub fn new(base: &str) -> PyResult<Self> {
        Self::new_impl(base).map_err(|e| PyRuntimeError::new_err(e.0))
    }

    pub fn read_all(&self) -> PyResult<HashMap<String, u64>> {
        self.read_all_impl().map_err(|e| PyRuntimeError::new_err(e.0))
    }

    pub fn joules_between(
        &self,
        start: HashMap<String, u64>,
        end: HashMap<String, u64>,
    ) -> PyResult<f64> {
        self.joules_between_impl(&start, &end)
            .map_err(|e| PyRuntimeError::new_err(e.0))
    }

    #[getter]
    pub fn vendor(&self) -> &str {
        &self.vendor
    }

    #[getter]
    pub fn domains(&self) -> Vec<String> {
        self.domains.iter().map(|d| d.name.clone()).collect()
    }
}

impl RaplReader {
    pub fn new_impl(base: &str) -> Result<Self, RaplError> {
        let base_path = PathBuf::from(base);
        if !base_path.exists() {
            return Err(RaplError(format!("RAPL base path does not exist: {}", base)));
        }

        let vendor = detect_cpu_vendor().unwrap_or_else(|| "unknown".into());
        let mut domains: Vec<Domain> = Vec::new();

        for entry in fs::read_dir(&base_path)? {
            let entry = entry?;
            let name = match entry.file_name().to_str().map(|s| s.to_string()) {
                Some(s) => s,
                None => continue,
            };
            if !name.starts_with("intel-rapl:") {
                continue;
            }

            let pkg_dir = entry.path();
            let pkg_name = read_domain_name(&pkg_dir).unwrap_or_else(|| name.clone());
            domains.push(Domain {
                name: pkg_name.clone(),
                energy_uj_path: pkg_dir.join("energy_uj"),
                max_range_uj_path: pkg_dir.join("max_energy_range_uj"),
            });

            if let Ok(subs) = fs::read_dir(&pkg_dir) {
                for sub in subs.flatten() {
                    let sub_name = match sub.file_name().to_str().map(|s| s.to_string()) {
                        Some(s) => s,
                        None => continue,
                    };
                    if !sub_name.starts_with("intel-rapl:") {
                        continue;
                    }
                    let sub_dir = sub.path();
                    let pretty = read_domain_name(&sub_dir).unwrap_or_else(|| sub_name.clone());
                    domains.push(Domain {
                        name: pretty,
                        energy_uj_path: sub_dir.join("energy_uj"),
                        max_range_uj_path: sub_dir.join("max_energy_range_uj"),
                    });
                }
            }
        }

        if domains.is_empty() {
            return Err(RaplError(format!("no intel-rapl:* domains found under {}", base)));
        }

        let mut max_range_uj = HashMap::new();
        for d in &domains {
            let m = fs::read_to_string(&d.max_range_uj_path)
                .ok()
                .and_then(|s| s.trim().parse::<u64>().ok())
                .unwrap_or(0);
            max_range_uj.insert(d.name.clone(), m);
        }

        Ok(RaplReader { domains, max_range_uj, vendor })
    }

    pub fn read_all_impl(&self) -> Result<HashMap<String, u64>, RaplError> {
        let mut out = HashMap::new();
        for d in &self.domains {
            let raw = fs::read_to_string(&d.energy_uj_path)?;
            let v = raw.trim().parse::<u64>().map_err(|e| {
                RaplError(format!("bad energy_uj in {}: {}", d.energy_uj_path.display(), e))
            })?;
            out.insert(d.name.clone(), v);
        }
        Ok(out)
    }

    pub fn joules_between_impl(
        &self,
        start: &HashMap<String, u64>,
        end: &HashMap<String, u64>,
    ) -> Result<f64, RaplError> {
        let mut total: f64 = 0.0;
        let mut used: Vec<String> = Vec::new();

        for d in &self.domains {
            if !d.name.starts_with("package") {
                continue;
            }
            total += self.delta_uj(&d.name, start, end)?;
            used.push(d.name.clone());
        }

        if self.vendor == "intel" {
            for d in &self.domains {
                if d.name == "dram" && !used.contains(&d.name) {
                    total += self.delta_uj(&d.name, start, end)?;
                }
            }
        }

        Ok(total / 1_000_000.0)
    }

    fn delta_uj(
        &self,
        name: &str,
        start: &HashMap<String, u64>,
        end: &HashMap<String, u64>,
    ) -> Result<f64, RaplError> {
        let s = *start.get(name).ok_or_else(|| RaplError(format!("missing start reading for {}", name)))?;
        let e = *end.get(name).ok_or_else(|| RaplError(format!("missing end reading for {}", name)))?;

        if e >= s {
            Ok((e - s) as f64)
        } else {
            // Wraparound occurred. We MUST know the max range to calculate it safely.
            let max = self.max_range_uj.get(name).copied().filter(|&m| m > 0);
            
            match max {
                Some(max_val) => Ok(((max_val - s) + e) as f64),
                None => {
                    if self.vendor == "amd" {
                        // Fail loudly instead of returning 18 Terajoules
                        Err(RaplError(
                            "AMD RAPL counter wrapped, but max_energy_range_uj is missing. Refusing to calculate an incorrect delta.".into()
                        ))
                    } else {
                        // Intel's 32-bit register fallback is mathematically safe
                        Ok((((u32::MAX as u64) - s) + e) as f64)
                    }
                }
            }
        }
    }
}

fn read_domain_name(dir: &Path) -> Option<String> {
    fs::read_to_string(dir.join("name")).ok().map(|s| s.trim().to_string())
}

fn detect_cpu_vendor() -> Option<String> {
    let cpuinfo = fs::read_to_string("/proc/cpuinfo").ok()?;
    let lower = cpuinfo.to_lowercase();
    if lower.contains("amd") {
        Some("amd".into())
    } else if lower.contains("intel") {
        Some("intel".into())
    } else {
        None
    }
}

#[pymodule]
fn dol8_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<RaplReader>()?;
    m.add_function(wrap_pyfunction!(compare::run_comparison, m)?)?;
    Ok(())
}