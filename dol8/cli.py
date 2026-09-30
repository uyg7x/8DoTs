"""Command-line interface for the DoL8 energy profiler."""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, cast

from . import __version__
from .compare import compare as compare_targets
from .compare import scan
from .doctor import run_doctor
from .gate import evaluate_gate, save_baseline, boot_energy
from .poller import PollingThread
from .rapl import RAPLReader, RaplUnavailable
from .report import render_terminal, to_csv_timeline, to_json, write_json
from .runner import PowerProfiler, RunResult, TargetRunner, map_exit
from .stats import aggregate, measure_baseline, subtract_baseline
from .thermal import ThermalMonitor
from .watch import run_watch


def build_parser() -> argparse.ArgumentParser:
    """Create the argparse command tree."""
    parser = argparse.ArgumentParser(prog="dol8", description="High-resolution estimate of program energy use")
    commands = parser.add_subparsers(dest="command", required=True)

    run_parser = commands.add_parser("run", help="Profile one or more scripts sequentially")
    run_parser.add_argument("scripts", nargs="+")
    run_parser.add_argument("--runs", type=int, default=1)
    run_parser.add_argument("--warmup", type=int, default=1)
    run_parser.add_argument("--no-warmup", action="store_true")
    run_parser.add_argument("--json", dest="json_path")
    run_parser.add_argument("--csv", dest="csv_path")
    run_parser.add_argument("--timeout", type=float, default=600.0)
    run_parser.add_argument("--price", type=float)
    run_parser.add_argument("--runs-per-day", type=float, default=0.0)
    run_parser.add_argument("--grid-factor", type=float)
    run_parser.add_argument("--gate-baseline")
    run_parser.add_argument("--threshold", type=float, default=15.0)

    compare_parser = commands.add_parser("compare", help="Compare two targets")
    compare_parser.add_argument("first")
    compare_parser.add_argument("second")
    compare_parser.add_argument("--runs", type=int, default=5)
    compare_parser.add_argument("--timeout", type=float, default=600.0)
    compare_parser.add_argument("--price", type=float)
    compare_parser.add_argument("--grid-factor", type=float)
    compare_parser.add_argument("--json", dest="json_path", default="compare.json")

    watch_parser = commands.add_parser("watch", help="Observe machine energy and estimate process share")
    watch_parser.add_argument("--attach", nargs="?", const="", default=None)
    watch_parser.add_argument("--out", default="dol8_session.json")

    boot_parser = commands.add_parser("boot-energy", help="Estimate energy since boot")
    boot_parser.add_argument("--json", dest="json_path", default="boot.json")

    doctor_parser = commands.add_parser("doctor", help="Check measurement environment readiness")
    doctor_parser.add_argument("--output-dir", default=".")

    gate_parser = commands.add_parser("gate", help="Manage an energy baseline")
    gate_commands = gate_parser.add_subparsers(dest="gate_command", required=True)
    save_parser = gate_commands.add_parser("save", help="Save a result JSON as a gate baseline")
    save_parser.add_argument("result_json")
    save_parser.add_argument("--out", default="baseline.json")

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Parse arguments, run the selected command, and map failures to exit codes."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "gate":
        return _gate_save(args.result_json, args.out)
    if args.command == "doctor":
        try:
            result = run_doctor(output_directory=args.output_dir)
        except (OSError, ValueError, RuntimeError) as error:
            print("DoL8 doctor error: {}".format(error), file=sys.stderr)
            return 1
        print(result.render())
        return 0 if result.ready else 1
    if not _platform_supported():
        print("DoL8 v1 requires Linux bare metal with powercap RAPL support.", file=sys.stderr)
        return 1
    try:
        if args.command == "run":
            return _run_command(args)
        if args.command == "compare":
            return _compare_command(args)
        if args.command == "watch":
            return _watch_command(args)
        if args.command == "boot-energy":
            return _boot_command(args)
    except KeyboardInterrupt:
        print("\nInterrupted. Partial measurements were retained where available.", file=sys.stderr)
        return 130
    except (RaplUnavailable, OSError, ValueError, RuntimeError) as error:
        print("DoL8 error: {}".format(error), file=sys.stderr)
        return 1
    parser.error("unknown command")
    return 1


def _platform_supported() -> bool:
    """Return whether the current host matches the v1 Linux-only contract."""
    return sys.platform.startswith("linux")


def _run_command(args: argparse.Namespace) -> int:
    """Execute the full baseline, warmup, measured-run, and reporting flow."""
    if args.runs < 1 or args.warmup < 0 or args.timeout <= 0:
        raise ValueError("runs must be positive; warmup non-negative; timeout positive")
    if args.price is not None and args.price < 0:
        raise ValueError("price must be non-negative")
    if args.runs_per_day < 0 or (args.grid_factor is not None and args.grid_factor < 0):
        raise ValueError("runs-per-day and grid-factor must be non-negative")
    reader = RAPLReader()
    thermal = ThermalMonitor()
    if not reader.available():
        raise RaplUnavailable(reader.unavailable_reason)
    print("DoL8 high-resolution estimates")
    scripts = _expand_scripts(args.scripts)
    all_results: List[Dict[str, Any]] = []
    all_run_statuses: List[int] = []
    interrupted = False
    previous_temperature: Optional[float] = None
    runner = TargetRunner()
    phase = "BETWEEN_FILES"
    phase_started = time.perf_counter()
    current_index = 0
    skip_from: Optional[int] = None
    try:
        for index, script in enumerate(scripts):
            current_index = index
            if index and previous_temperature is not None:
                phase = "BETWEEN_FILES"
                phase_started = time.perf_counter()
                _cooldown(thermal, previous_temperature)
            phase = "BASELINE"
            phase_started = time.perf_counter()
            baseline = measure_baseline(reader, thermal, seconds=5.0)
            if not args.no_warmup:
                phase = "WARMUP"
                phase_started = time.perf_counter()
                for _ in range(args.warmup):
                    warmup = runner.run(_command_for(script), args.timeout)
                    _print_survivor_warning(warmup.survivors)
                    if warmup.returncode == 130:
                        raise KeyboardInterrupt
                    if warmup.returncode != 0:
                        print("Warmup for {} exited {}; continuing.".format(script, warmup.returncode), file=sys.stderr)
            phase = "MEASURED"
            phase_started = time.perf_counter()
            run_results: List[Dict[str, Any]] = []
            run_statuses: List[int] = []
            for _ in range(args.runs):
                measurement, run_result = _measure_target(script, reader, args.timeout)
                measurement["returncode"] = run_result.returncode
                run_results.append(measurement)
                status = map_exit(run_result, interrupted=run_result.returncode == 130)
                run_statuses.append(status)
                if status == 130:
                    interrupted = True
                    skip_from = index + 1
                    break
            adjusted = [subtract_baseline(result, baseline) for result in run_results]
            energy_stats = aggregate([
                {"energy_joules": float(cast(float, result["energy_joules"]))}
                for result in adjusted
            ])
            result = _combine_runs(script, adjusted, energy_stats)
            result["baseline_removed_joules"] = statistics.mean(
                float(cast(float, value.get("baseline_removed_joules", 0.0))) for value in adjusted
            )
            result["price_per_kwh"] = args.price
            result["runs_per_day"] = args.runs_per_day
            result["grid_factor"] = args.grid_factor
            result["statistics"]["warmup_discarded"] = 0 if args.no_warmup else args.warmup
            result["returncode"] = 0 if all(status == 0 for status in run_statuses) else 2
            result["partial"] = 130 in run_statuses
            result["cpu"] = _cpu_name()
            result["os"] = platform.platform()
            result["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            all_results.append(result)
            all_run_statuses.extend(run_statuses)
            phase = "BETWEEN_FILES"
            phase_started = time.perf_counter()
            skip_from = index + 1
            if len(scripts) > 1 and not result["partial"]:
                thermal_end = result["thermal"].get("end_c")
                temp_text = "n/a" if thermal_end is None else "{:.1f}".format(thermal_end)
                status_text = "OK" if result["returncode"] == 0 else "FAILED"
                print("[{}/{}] {} .... {}  {:.2f}s | {:.1f}J +- {:.1f} | {:.1f}W | {}C".format(
                    index + 1, len(scripts), script, status_text, result["duration_seconds"],
                    result["energy_joules"], result["statistics"]["stdev_j"], result["avg_watts"], temp_text
                ))
                _print_survivor_warning(result.get("survivors", []))
            else:
                print(render_terminal(result))
            print()
            previous_temperature = result["thermal"].get("end_c")
            if interrupted:
                break
    except KeyboardInterrupt:
        interrupted = True
        _attempt_final_read(reader)
        elapsed = max(0.0, time.perf_counter() - phase_started)
        if phase == "BASELINE":
            print(
                "Interrupted during baseline measurement ({:.1f}s of 5.0s). "
                "No workload data collected.".format(elapsed)
            )
            skip_from = current_index
        elif phase == "WARMUP":
            print("Interrupted during warm-up (discarded by design). No measured data collected.")
            skip_from = current_index
        elif phase == "MEASURED":
            print("Interrupted during measured run; no completed sample is available.")
            skip_from = current_index
        else:
            print("Interrupted between files.")
            skip_from = current_index if skip_from is None else skip_from
    if skip_from is not None:
        _print_skipped(scripts, skip_from)
    if interrupted and len(scripts) > 1:
        for completed in all_results:
            if not completed.get("partial"):
                print(render_terminal(completed))
    completed_results = [result for result in all_results if not result.get("partial")]
    if len(scripts) > 1 and completed_results:
        combined = scan(
            [str(result["target"]) for result in completed_results],
            lambda target: next(item for item in completed_results if item["target"] == target),
            price_per_kwh=args.price,
        )
        _print_scan_summary(combined)
    if args.json_path:
        if not all_results:
            payload: Any = {"files": [], "summary": None}
        elif len(all_results) == 1:
            payload = to_json(all_results[0])
        else:
            scan_summary = None
            if completed_results:
                scan_summary = scan(
                    [str(item["target"]) for item in completed_results],
                    lambda name: next(result for result in completed_results if result["target"] == name),
                    price_per_kwh=args.price,
                )["summary"]
            payload = {
                "files": [to_json(result) for result in all_results],
                "summary": scan_summary,
            }
        if interrupted:
            payload["partial"] = True
            payload["skipped_targets"] = scripts[skip_from:] if skip_from is not None else []
        with open(args.json_path, "w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2, sort_keys=True)
            output.write("\n")
        print("Saved: {}".format(args.json_path))
    if args.csv_path and all_results:
        timeline = all_results[-1].get("timeline", [])
        with open(args.csv_path, "w", encoding="utf-8", newline="") as output:
            output.write(to_csv_timeline(timeline))
    if args.gate_baseline and all_results:
        with open(args.gate_baseline, "r", encoding="utf-8") as source:
            baseline_data = json.load(source)
        gate_result = evaluate_gate(all_results[0]["statistics"], baseline_data, args.threshold)
        print("ENERGY GATE: baseline {:.3f} J, current {:.3f} J, delta {:+.2f}%, threshold {:.2f}%: {}".format(
            gate_result.baseline_j, gate_result.current_j, gate_result.delta_pct,
            gate_result.threshold_pct, "PASS" if gate_result.passed else "FAIL",
        ))
        if not gate_result.passed:
            return 1
    return _map_run_exit(all_run_statuses, len(scripts), interrupted)


def _map_run_exit(statuses: List[int], target_count: int, interrupted: bool = False) -> int:
    """Map run and scan outcomes while distinguishing one crash from total scan failure."""
    if interrupted or 130 in statuses:
        return 130
    if not statuses:
        return 1
    if target_count == 1:
        return 0 if statuses[0] == 0 else 2
    if all(status == 0 for status in statuses):
        return 0
    return 2 if any(status == 0 for status in statuses) else 1


def _attempt_final_read(reader: RAPLReader) -> None:
    """Attempt one last counter read after an interrupted orchestration phase."""
    try:
        reader.read_all()
    except (RaplUnavailable, OSError, ValueError) as error:
        print("Final RAPL read failed: {}".format(error), file=sys.stderr)


def _print_skipped(scripts: Sequence[str], start_index: int) -> None:
    """List targets that could not start after an interrupted scan."""
    for script in scripts[start_index:]:
        print("[SKIPPED] {}".format(script))


def _print_survivor_warning(survivors: Sequence[int]) -> None:
    """Warn that survivor energy is excluded from the reported measurement."""
    if survivors:
        print("WARNING: {} child processes could not be terminated: {}".format(
            len(survivors), list(survivors)
        ))
        print("Their energy is NOT included in this measurement.")


def _measure_target(script: str, reader: RAPLReader, timeout: float) -> tuple[Dict[str, Any], RunResult]:
    """Run one target inside PowerProfiler and return energy plus process status."""
    thermal = ThermalMonitor()
    profiler = PowerProfiler(reader=reader, thermal=thermal)
    try:
        with profiler:
            run_result = TargetRunner().run(_command_for(script), timeout)
    except KeyboardInterrupt:
        run_result = RunResult(130, "", "", profiler.duration_s)
    thermal_stats = thermal.stats()
    poller = profiler._poller
    frequency_available = bool(poller and poller.frequency_available)
    throttle_events = list(profiler.throttle_events)
    result = {
        "target": script,
        "duration_seconds": profiler.duration_s,
        "energy_joules": profiler.energy_joules,
        "avg_watts": profiler.avg_watts,
        "peak_watts": profiler.peak_watts,
        "per_domain": profiler.per_domain,
        "thermal": {
            "baseline_c": thermal_stats["baseline"],
            "peak_c": thermal_stats["peak"],
            "avg_c": thermal_stats["avg"],
            "end_c": thermal_stats["end"],
            "rise_c": thermal_stats["rise"],
            "throttling": "yes" if throttle_events else "no" if frequency_available else "unknown (no frequency sensor)",
        },
        "throttle_events": throttle_events,
        "timeline": profiler.timeline,
        "gpu_available": profiler.gpu_available,
        "gpu": profiler.gpu,
        "battery": thermal.battery(),
        "survivors": list(run_result.survivors) if run_result is not None else [],
    }
    if run_result is None:
        raise RuntimeError("Target runner did not return a result")
    return result, run_result


def _combine_runs(script: str, runs: List[Dict[str, Any]], stats: Any) -> Dict[str, Any]:
    """Combine baseline-adjusted runs into one report model."""
    keys = set().union(*(run.get("per_domain", {}).keys() for run in runs))
    per_domain = {
        key: statistics.mean(float(run.get("per_domain", {}).get(key, 0.0)) for run in runs)
        for key in keys
    }
    thermal_values = {}
    for output_key, source_key in (("baseline_c", "baseline_c"), ("peak_c", "peak_c"), ("avg_c", "avg_c"), ("end_c", "end_c"), ("rise_c", "rise_c")):
        values = [float(run["thermal"][source_key]) for run in runs if run["thermal"].get(source_key) is not None]
        thermal_values[output_key] = statistics.mean(values) if values else None
    throttle_values = [run["thermal"].get("throttling") for run in runs]
    thermal_values["throttling"] = (
        "yes" if "yes" in throttle_values else
        "unknown (no frequency sensor)" if "unknown (no frequency sensor)" in throttle_values else "no"
    )
    energy = stats.mean_j
    durations = [float(run["duration_seconds"]) for run in runs]
    duration = statistics.mean(durations)
    gpu_runs = [run["gpu"] for run in runs if run.get("gpu")]
    return {
        "target": script,
        "duration_seconds": duration,
        "energy_joules": energy,
        "avg_watts": energy / duration if duration > 0 else 0.0,
        "peak_watts": max(float(run["peak_watts"]) for run in runs),
        "per_domain": per_domain,
        "thermal": thermal_values,
        "gpu_available": any(bool(run.get("gpu_available")) for run in runs),
        "gpu": {
            key: statistics.mean(float(gpu[key]) for gpu in gpu_runs)
            for key in ("watts", "temperature_c")
        } if gpu_runs else None,
        "battery": next((run["battery"] for run in runs if run.get("battery") is not None), None),
        "statistics": {
            "runs": stats.runs,
            "mean_j": stats.mean_j,
            "stdev_j": stats.stdev_j,
            "cov_pct": stats.coefficient_of_variation * 100.0,
            "confidence": stats.confidence,
            "stdev_time_s": statistics.stdev(durations) if len(durations) > 1 else 0.0,
        },
        "throttle_events": [event for run in runs for event in run.get("throttle_events", [])],
        "timeline": runs[-1].get("timeline", []),
        "survivors": sorted({pid for run in runs for pid in run.get("survivors", [])}),
    }


def _compare_command(args: argparse.Namespace) -> int:
    """Run and report alternating A/B measurements."""
    reader = RAPLReader()
    if not reader.available():
        raise RaplUnavailable(reader.unavailable_reason)
    if args.runs < 1 or args.timeout <= 0:
        raise ValueError("runs and timeout must be positive")
    if args.price is not None and args.price < 0:
        raise ValueError("price must be non-negative")
    if args.grid_factor is not None and args.grid_factor < 0:
        raise ValueError("grid-factor must be non-negative")
    print("DoL8 high-resolution estimates")
    run_data: Dict[str, List[Dict[str, Any]]] = {args.first: [], args.second: []}

    def run_target(target: str) -> Dict[str, Any]:
        measurement, outcome = _measure_compare_target(target, reader, args.timeout)
        run_data[target].append(measurement)
        return measurement

    compared = compare_targets(args.first, args.second, run_target, args.runs)
    compared["measurement"] = "high-resolution estimate"
    for target in (args.first, args.second):
        values = run_data[target]
        mean_energy = compared["statistics"][target]["mean_j"]
        mean_time = statistics.mean(float(item["duration_seconds"]) for item in values)
        mean_power = statistics.mean(float(item["avg_watts"]) for item in values)
        temperatures = [item["thermal"].get("peak_c") for item in values if item["thermal"].get("peak_c") is not None]
        mean_temp = statistics.mean(float(value) for value in temperatures) if temperatures else None
        print("{}: {:.3f}s | {:.3f} J +- {:.3f} J | {:.3f} W | {} C".format(
            target, mean_time, mean_energy, compared["statistics"][target]["stdev_j"], mean_power,
            "n/a" if mean_temp is None else "{:.1f}".format(mean_temp),
        ))
    verdict = compared["verdict"]
    for values in run_data.values():
        for item in values:
            _print_survivor_warning(item.get("survivors", []))
    print("Delta: {:+.2f}% | {}".format(verdict["delta_pct"], verdict["label"]))
    if args.price is not None:
        saved_j = -float(verdict["delta_j"])
        print("Estimated savings: {:.3f} J, {:.8f} per run".format(
            saved_j, saved_j / 3_600_000.0 * args.price
        ))
    if args.grid_factor is not None:
        saved_j = -float(verdict["delta_j"])
        print("Estimated CO2 change: {:.3f} g per SCI-aligned estimation".format(
            saved_j / 3_600_000.0 * args.grid_factor * 1000.0
        ))
    first_energy = float(compared["statistics"][args.first]["mean_j"])
    second_energy = float(compared["statistics"][args.second]["mean_j"])
    maximum = max(first_energy, second_energy)
    first_filled = round(first_energy / maximum * 20) if maximum else 0
    second_filled = round(second_energy / maximum * 20) if maximum else 0
    print("{} [{}{}]".format(args.first, "#" * first_filled, "." * (20 - first_filled)))
    print("{} [{}{}]".format(args.second, "#" * second_filled, "." * (20 - second_filled)))
    with open(args.json_path, "w", encoding="utf-8") as output:
        json.dump(compared, output, indent=2, sort_keys=True)
        output.write("\n")
    print("Saved: {}".format(args.json_path))
    return 0 if all(item["returncode"] == 0 for values in run_data.values() for item in values) else 2


def _measure_compare_target(
    target: str, reader: RAPLReader, timeout: float
) -> tuple[Dict[str, Any], RunResult]:
    """Run a fresh baseline, discard one warm-up, and measure one A/B sample."""
    baseline = measure_baseline(reader, ThermalMonitor(), seconds=5.0)
    warmup = TargetRunner().run(_command_for(target), timeout)
    if warmup.returncode != 0:
        print("Warmup for {} exited {}; continuing.".format(target, warmup.returncode), file=sys.stderr)
    measurement, outcome = _measure_target(target, reader, timeout)
    adjusted = subtract_baseline(measurement, baseline)
    adjusted["returncode"] = outcome.returncode
    return adjusted, outcome


def _watch_command(args: argparse.Namespace) -> int:
    """Run observe mode and print machine and estimated application energy."""
    reader = RAPLReader()
    if not reader.available():
        raise RaplUnavailable(reader.unavailable_reason)
    attach = args.attach if args.attach else None
    result = run_watch(reader, ThermalMonitor(), attach=attach, out_path=args.out)
    print("Machine energy (high-resolution estimate): {:.3f} J".format(
        result.get("machine_energy_joules", 0.0)
    ))
    print("App share (CPU-time): {:.2f}% -> App energy (ESTIMATED): {:.3f} J".format(
        result.get("app_share_pct", 0.0), result.get("app_energy_estimated_joules", 0.0)
    ))
    print(result.get("honesty_note", "Attribution is ESTIMATED."))
    print("Saved: {}".format(args.out))
    return 130 if result.get("interrupted") else 0


def _boot_command(args: argparse.Namespace) -> int:
    """Report current RAPL counters as a boot-energy estimate."""
    reader = RAPLReader()
    if not reader.available():
        raise RaplUnavailable(reader.unavailable_reason)
    result = boot_energy(reader)
    result["tool"] = "DoL8"
    result["version"] = __version__
    result["timestamp"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    result["measurement"] = "high-resolution estimate"
    with open(args.json_path, "w", encoding="utf-8") as output:
        json.dump(result, output, indent=2, sort_keys=True)
        output.write("\n")
    print("BOOT ENERGY ESTIMATE: {:.3f} J ({:.8f} kWh), uptime {:.0f}s, average {:.3f} W".format(
        result["energy_joules"], result["energy_kwh"], result["uptime_seconds"], result["average_watts"]
    ))
    if result["wrap_warning"]:
        print("Warning: uptime exceeds the estimated counter wrap window.")
    print("Saved: {}".format(args.json_path))
    return 0


def _gate_save(result_path: str, output_path: str) -> int:
    """Convert a DoL8 result JSON into a reusable gate baseline."""
    with open(result_path, "r", encoding="utf-8") as source:
        result = json.load(source)
    stats = result.get("statistics", result)
    save_baseline(output_path, stats)
    print("Saved baseline: {}".format(output_path))
    return 0


def _expand_scripts(scripts: Sequence[str]) -> List[str]:
    """Keep each requested target as one sequential execution unit."""
    expanded: List[str] = []
    for script in scripts:
        expanded.append(script)
    if not expanded:
        raise ValueError("No runnable Python files found")
    return expanded


def _command_for(script: str) -> List[str]:
    """Build the process argument vector for Python files and executables."""
    path = Path(script)
    if path.is_dir():
        return [sys.executable, "-m", "pytest", str(path)]
    if path.suffix.lower() == ".py":
        return [sys.executable, str(path)]
    return [script]


def _cooldown(thermal: ThermalMonitor, reference_c: float, timeout_s: float = 60.0) -> None:
    """Wait up to one minute for temperature to return within five Celsius."""
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        temperature = thermal.current()
        if temperature is None or abs(temperature - reference_c) <= 5.0:
            return
        time.sleep(1.0)


def _cpu_name() -> str:
    """Return a best-effort CPU model label without requiring psutil."""
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8", errors="replace") as cpuinfo:
            for line in cpuinfo:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return platform.processor() or "unknown"


def _print_scan_summary(result: Dict[str, Any]) -> None:
    """Print energy-ranked per-file scan totals."""
    print("COMBINED SUMMARY")
    for row in result["summary"]["ranked"]:
        filled = max(0, min(20, round(row["share_pct"] / 5.0)))
        line = "{} | {:.3f} J | {:5.1f}% [{}{}]".format(
            row["target"], row["energy_joules"], row["share_pct"], "#" * filled, "." * (20 - filled)
        )
        if row["cost"] is not None:
            line += " | {:.8f} per run".format(row["cost"])
        print(line)
    print("Total: {:.8f} kWh".format(result["summary"]["total_energy_kwh"]))
    print("Most/least expensive: {} / {} | ratio {}".format(
        result["summary"]["most_expensive"], result["summary"]["least_expensive"],
        "n/a" if result["summary"]["energy_ratio"] is None else "{:.2f}:1".format(result["summary"]["energy_ratio"]),
    ))
    if result["summary"]["total_cost"] is not None:
        print("Combined estimated cost: {:.8f}".format(result["summary"]["total_cost"]))


def entrypoint() -> None:
    """Exit the process using the CLI's selected command status."""
    raise SystemExit(main())


if __name__ == "__main__":
    entrypoint()
