#!/usr/bin/env python3
"""dol8 folder comparison CLI.

Runs the Rust comparison engine on an "old" and a "new" project folder,
applies the statistical layer from dol8.stats, and emits an honest JSON
report. Designed for CI/CD gating.

What is new vs v0.1:
  * --runs default raised to 10 (3 is not enough for energy measurement)
  * --timeout per-child, with the engine actually killing hung scripts
  * --min-confidence gate policy: LOW_PROXY results never fail CI
  * Richer JSON schema (schema_version 3): per-side samples, stddev, CoV,
    dropped outliers, confidence, gate_action, commit_sha, timestamp
  * Cross-tier baseline check still refuses with exit 3
  * Honest null fields: energy_joules is null unless Tier A actually ran

Exit codes:
    0  pass or advisory
    1  regression exceeded threshold (hard gate)
    2  operational error (engine failure, bad args, IO)
    3  cross-tier comparison attempted (cannot compare Joules to CPU time)
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone

# Make the inner `dol8` package importable when this script is run from
# CompareFolder/ regardless of the current working directory.
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

# Rust extension is required; no fallback for this one.
try:
    import dol8_core  # type: ignore
except ImportError as exc:
    print(f"[ERROR] could not import dol8_core (Rust extension): {exc}", file=sys.stderr)
    print(
        "Run 'maturin develop --release' to build the Rust engine.",
        file=sys.stderr,
    )
    sys.exit(2)

# capabilities and stats are pure Python. Try the normal package import
# first; if it fails (typically because dol8/__init__.py imports
# PowerProfiler from runner.py and runner.py imports the old stats.py
# API), fall back to loading the two files directly so the comparison
# CLI keeps working while you fix the rest of the project.
import importlib.util


def _load_submodule(dotted_name: str, rel_path: str):
    spec = importlib.util.spec_from_file_location(
        dotted_name, os.path.join(ROOT_DIR, rel_path)
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"could not locate {rel_path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[dotted_name] = mod
    spec.loader.exec_module(mod)
    return mod


try:
    from dol8 import capabilities as capabilities  # noqa: F401
    from dol8 import stats as stats_mod  # noqa: F401
except Exception:
    capabilities = _load_submodule("dol8.capabilities", "dol8/capabilities.py")
    stats_mod = _load_submodule("dol8.stats", "dol8/stats.py")


EXIT_OK = 0
EXIT_FAIL = 1
EXIT_ERROR = 2
EXIT_CROSS_TIER = 3


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="compareJ",
        description="Compare energy/work of two project folders using the dol8 engine.",
    )
    p.add_argument("old_dir", help="path to old project folder")
    p.add_argument("new_dir", help="path to new project folder")
    p.add_argument("--entry", default="main.py", help="entry file inside each folder")
    p.add_argument(
        "--runs",
        type=int,
        default=10,
        help="number of runs per side (default 10; do not go below 5)",
    )
    p.add_argument(
        "--baseline", help="path to baseline JSON for cross-tier safety check"
    )
    p.add_argument(
        "--json", dest="json_path", help="write structured result JSON here"
    )
    p.add_argument(
        "--threshold",
        type=float,
        default=5.0,
        help="fail CI if regression exceeds this percent (default 5.0)",
    )
    p.add_argument(
        "--min-confidence",
        default="MEDIUM",
        choices=["LOW", "MEDIUM", "HIGH", "EXACT"],
        help=(
            "gate is advisory (never fails CI) when measured confidence is "
            "below this level (default MEDIUM)"
        ),
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="per-child timeout in seconds (default 60)",
    )
    p.add_argument(
        "--no-gate",
        action="store_true",
        help="compute and report the verdict but always exit 0",
    )
    return p


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            cwd=ROOT_DIR,
        )
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _fail(msg: str, code: int = EXIT_ERROR) -> int:
    print(f"[ERROR] {msg}", file=sys.stderr)
    return code


def main() -> int:
    args = build_parser().parse_args()

    if args.runs < 3:
        return _fail("--runs must be at least 3 for any meaningful statistics")

    # --- 1. Tier detection -----------------------------------------------
    tier_info = capabilities.get_current_tier()
    print(
        f"[INFO] environment: Tier {tier_info.tier} "
        f"({tier_info.confidence}) source={tier_info.source} "
        f"vm={tier_info.is_vm} vendor={tier_info.cpu_vendor}"
    )

    # --- 2. Cross-tier baseline safety -----------------------------------
    if args.baseline and os.path.exists(args.baseline):
        try:
            with open(args.baseline, "r") as f:
                baseline = json.load(f)
        except (OSError, ValueError) as exc:
            return _fail(f"could not read baseline JSON {args.baseline}: {exc}")
        baseline_tier = baseline.get("tier", "UNKNOWN")
        if baseline_tier != tier_info.tier:
            print(
                f"[ERROR] cross-tier comparison refused (exit {EXIT_CROSS_TIER}).",
                file=sys.stderr,
            )
            print(f"        baseline tier: {baseline_tier}", file=sys.stderr)
            print(f"        current tier : {tier_info.tier}", file=sys.stderr)
            print(
                f"        you cannot compare {baseline.get('metric')} to "
                f"{tier_info.metric}; recreate the baseline on this tier.",
                file=sys.stderr,
            )
            return EXIT_CROSS_TIER

    # --- 3. Run the engine -----------------------------------------------
    try:
        old_samples, new_samples = dol8_core.run_comparison(
            args.old_dir,
            args.new_dir,
            args.entry,
            args.runs,
            sys.executable,
            tier_info.tier,
            args.timeout,
        )
    except Exception as exc:
        return _fail(f"engine failure: {exc}")

    # --- 4. Statistical analysis (outlier rejection, CoV, verdict) -------
    old_stats = stats_mod.compute_stats(list(old_samples))
    new_stats = stats_mod.compute_stats(list(new_samples))

    verdict = stats_mod.regression_verdict(
        old=old_stats,
        new=new_stats,
        threshold_pct=args.threshold,
        min_confidence=args.min_confidence,
        tier_confidence=tier_info.confidence,
    )

    # --- 5. Honest JSON schema -------------------------------------------
    payload = {
        "schema_version": 3,
        "tool": "dol8",
        "tool_version": "0.2.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "commit_sha": _git_sha(),
        "os": platform.platform(),
        "cpu_vendor": tier_info.cpu_vendor,
        "is_vm": tier_info.is_vm,
        "tier": tier_info.tier,
        "tier_name": tier_info.name,
        "metric": tier_info.metric,
        "source": tier_info.source,
        "tier_confidence": tier_info.confidence,
        "runs": args.runs,
        "entry": args.entry,
        "old_dir": os.path.abspath(args.old_dir),
        "new_dir": os.path.abspath(args.new_dir),
        "old": old_stats.as_dict(),
        "new": new_stats.as_dict(),
        "verdict": verdict.as_dict(),
        # Legacy fields kept for older dashboards that still read these:
        "old_metric_value": old_stats.mean,
        "new_metric_value": new_stats.mean,
        "delta_pct": verdict.delta_pct,
        "confidence": verdict.confidence,
        "energy_joules": (
            new_stats.mean if tier_info.metric == "energy_joules" else None
        ),
        "instructions": (
            new_stats.mean
            if tier_info.metric in ("proxy_count", "instructions")
            else None
        ),
        "cpu_time_s": (
            new_stats.mean if tier_info.metric == "cpu_time_s" else None
        ),
    }

    if args.json_path:
        try:
            with open(args.json_path, "w") as f:
                json.dump(payload, f, indent=2)
            print(f"[INFO] saved JSON to {args.json_path}")
        except OSError as exc:
            return _fail(f"could not write JSON {args.json_path}: {exc}")

    # --- 6. Human verdict ------------------------------------------------
    print("\n=== VERDICT ===")
    print(
        f"old mean = {old_stats.mean:.4f} "
        f"(stddev={old_stats.stddev:.4f}, CoV={old_stats.cov * 100:.2f}%, "
        f"n={old_stats.n})"
    )
    print(
        f"new mean = {new_stats.mean:.4f} "
        f"(stddev={new_stats.stddev:.4f}, CoV={new_stats.cov * 100:.2f}%, "
        f"n={new_stats.n})"
    )
    if old_stats.dropped:
        print(
            f"[warn] dropped {len(old_stats.dropped)} old outlier(s): "
            f"{[round(x, 4) for x in old_stats.dropped]}"
        )
    if new_stats.dropped:
        print(
            f"[warn] dropped {len(new_stats.dropped)} new outlier(s): "
            f"{[round(x, 4) for x in new_stats.dropped]}"
        )
    print(f"delta = {verdict.delta:+.4f} ({verdict.delta_pct:+.2f}%)")
    print(
        f"combined_stddev = {verdict.combined_stddev:.4f}  "
        f"inconclusive={verdict.is_inconclusive}"
    )
    print(
        f"confidence = {verdict.confidence}  "
        f"gate_action = {verdict.gate_action}"
    )
    print(f"reason: {verdict.reason}")

    # --- 7. Exit code / CI gate ------------------------------------------
    if args.no_gate:
        print("[INFO] --no-gate set; always exit 0")
        return EXIT_OK
    if verdict.gate_action == "fail":
        print(
            f"[FAILED] regression {verdict.delta_pct:+.2f}% exceeds "
            f"threshold {args.threshold:.2f}%"
        )
        return EXIT_FAIL
    if verdict.gate_action == "advisory":
        # Advisory is a pass with a visible warning.
        print(f"[ADVISORY] {verdict.reason}")
        return EXIT_OK
    print(f"[PASSED] {verdict.reason}")
    return EXIT_OK

    # --- 8. Structured Logging (for CI/CD) ---
    log_entry = {
        "event": "dol8_gate_result",
        "tier": tier_info.tier,
        "metric": tier_info.metric,
        "delta_pct": round(verdict.delta_pct, 2),
        "confidence": verdict.confidence,
        "gate_action": verdict.gate_action,
        "exit_code": EXIT_FAIL if verdict.gate_action == "fail" else EXIT_OK
    }
    print(f"[LOG] {json.dumps(log_entry)}")

    if verdict.gate_action == "fail":
        return EXIT_FAIL
    return EXIT_OK

if __name__ == "__main__":
    sys.exit(main())
    
    