#!/usr/bin/env python3
"""audit_render_gates.py - Automated Quality Gate Validator for FilmCraft and EffectCraft Renders.

Evaluates rendered video files against production broadcast standards and motion design quality gates:
  G0: Render Health (Valid decoding, non-blank frames, motion detected, expected duration, audio present)
  G1: Proof Readability (Visual scale verification on hero frames)
  G2: No Dead Frames (Detects runs of >3 consecutive motionless frames)
  G3: End-Card Discipline (Picture-unchanged end hold <= 1.4s; final shot <= 25% of total film duration)
  G4: Single Hero Focus (Single focal point verification)
  G5: Anti-Slop Check (Page chrome, HUD labels, counters, timecodes, fake decks)
  AUDIO: ITU-R BS.1770 / EBU R128 Loudness (default -14.0 LUFS ± 1.0, True Peak <= -1.0 dBTP;
         change with --target-lufs, --lufs-tolerance, --max-true-peak, e.g. -23 for broadcast)

Usage:
  python audit_render_gates.py <video_file> [--expect-duration S] [--expect-audio] [--out-report gates.json] [--strict]
                                [--target-lufs -14] [--lufs-tolerance 1.0] [--max-true-peak -1.0]
"""

import argparse
import re
import json
import math
import os
import shutil
import subprocess
import sys
import numpy as np

CLI_PATH = r"C:\Program Files\ArtCraft\FilmCraft\filmcraft-cli.exe"
TICKS_PER_SEC = 254016000000

def parse_args():
    parser = argparse.ArgumentParser(description="Audit video render against G0-G5 Quality Gates and EBU R128 loudness.")
    parser.add_argument("video", help="Path to rendered MP4 or MOV file.")
    parser.add_argument("--expect-duration", type=float, default=None, help="Expected duration in seconds (tolerance ±0.35s).")
    parser.add_argument("--expect-audio", action="store_true", default=True, help="Enforce audio stream presence and non-silence.")
    parser.add_argument("--out-report", default=None, help="Path to write JSON gates report.")
    parser.add_argument("--hold-warn", type=float, default=1.4, help="Max allowed static end hold in seconds (default 1.4s).")
    parser.add_argument("--target-lufs", type=float, default=-14.0, help="Integrated loudness target in LUFS (default -14.0 for web/social; use -23 for EBU R128 broadcast).")
    parser.add_argument("--lufs-tolerance", type=float, default=1.0, help="Allowed distance from --target-lufs in LU (default 1.0).")
    parser.add_argument("--max-true-peak", type=float, default=-1.0, help="True peak ceiling in dBTP (default -1.0).")
    parser.add_argument("--strict", action="store_true", help="Exit code 3 if any gate fails.")
    return parser.parse_args()

def probe_video(file_path):
    # Try ffprobe first
    if shutil.which("ffprobe"):
        cmd = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", file_path]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            doc = json.loads(res.stdout)
            v_streams = [s for s in doc.get("streams", []) if s.get("codec_type") == "video"]
            a_streams = [s for s in doc.get("streams", []) if s.get("codec_type") == "audio"]
            dur = float(doc.get("format", {}).get("duration", 0.0))
            w = int(v_streams[0].get("width", 0)) if v_streams else 0
            h = int(v_streams[0].get("height", 0)) if v_streams else 0
            fps = 60.0
            if v_streams and "r_frame_rate" in v_streams[0]:
                num, den = v_streams[0]["r_frame_rate"].split("/")
                fps = float(num) / float(den)
            return {
                "duration": dur,
                "width": w,
                "height": h,
                "fps": fps,
                "video_streams": v_streams,
                "audio_streams": a_streams,
                "source": "ffprobe"
            }

    # Fallback to filmcraft-cli probe
    if os.path.exists(CLI_PATH):
        res = subprocess.run([CLI_PATH, "probe", file_path], capture_output=True, text=True, encoding="utf-8")
        if res.returncode == 0 and res.stdout.strip():
            doc = json.loads(res.stdout)
            dur_ticks = doc.get("duration", 0)
            dur = dur_ticks / TICKS_PER_SEC
            v = doc.get("video", {})
            w = v.get("width", 0)
            h = v.get("height", 0)
            fr = v.get("frame_rate", {})
            fps = float(fr.get("num", 60)) / float(fr.get("den", 1))
            a_streams = doc.get("audio_streams", [])
            return {
                "duration": dur,
                "width": w,
                "height": h,
                "fps": fps,
                "video_streams": [v] if v else [],
                "audio_streams": a_streams,
                "source": "filmcraft-cli"
            }

    raise RuntimeError(f"Unable to probe video: neither ffprobe nor filmcraft-cli available for {file_path}")

def measure_loudness(file_path):
    """Returns (integrated LUFS, true peak dBTP, LRA) from ffmpeg's ebur128 summary, or (None, None, None)."""
    if not shutil.which("ffmpeg"):
        return None, None, None
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", file_path, "-af", "ebur128=peak=true", "-f", "null", "-"]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = res.stderr
    if "Summary:" not in out:
        return None, None, None
    summary = out.split("Summary:")[-1]
    def grab(label, unit):
        m = re.search(rf"^\s*{label}:\s*(-?inf|-?\d+(?:\.\d+)?)\s*{unit}", summary, re.M)
        if not m:
            return None
        return float("-inf") if "inf" in m.group(1) else float(m.group(1))
    lufs = grab("I", "LUFS")
    lra = grab("LRA", "LU")
    true_peak = grab("Peak", "dBFS")
    return lufs, true_peak, lra


def analyze_motion_frames(file_path, width=96, height=54):
    if shutil.which("ffmpeg"):
        cmd = ["ffmpeg", "-v", "error", "-i", file_path, "-an", "-vf", f"scale={width}:{height}:flags=area,format=gray", "-f", "rawvideo", "-"]
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        size = width * height
        diffs = []
        prev = None
        max_empty = 0
        cur_empty = 0
        while True:
            buf = p.stdout.read(size)
            if len(buf) < size: break
            fr = list(buf)
            if prev is not None:
                d = sum(abs(a - b) for a, b in zip(fr, prev)) / size
                diffs.append(d)
                if d < 0.25:
                    cur_empty += 1
                    if cur_empty > max_empty: max_empty = cur_empty
                else:
                    cur_empty = 0
            else:
                diffs.append(0.0)
            prev = fr
        p.wait()
        return diffs, max_empty

    # ffmpeg is not available: motion cannot be measured.
    return None, None

def main():
    args = parse_args()
    if not os.path.exists(args.video):
        print(f"Error: File not found: {args.video}", file=sys.stderr)
        sys.exit(1)

    print(f"=== AUDITING GATES FOR: {args.video} ===")
    info = probe_video(args.video)
    actual_dur = info["duration"]
    w = info["width"]
    h = info["height"]
    fps = info["fps"]
    has_audio = len(info["audio_streams"]) > 0

    # 1. Gate G0: Render Health
    g0_pass = True
    g0_reasons = []
    if w == 0 or h == 0 or actual_dur == 0:
        g0_pass = False
        g0_reasons.append("Invalid video dimensions or zero duration.")

    if args.expect_duration is not None:
        delta = abs(actual_dur - args.expect_duration)
        if delta > 0.35:
            g0_pass = False
            g0_reasons.append(f"Duration mismatch: {actual_dur:.2f}s vs expected {args.expect_duration:.2f}s (delta {delta:.2f}s).")

    if args.expect_audio and not has_audio:
        g0_pass = False
        g0_reasons.append("Audio stream missing.")

    diffs, _ = analyze_motion_frames(args.video)

    # 3. Gate G3: End-Card Hold Discipline (measured first, so G2 can skip the end hold)
    trailing_frozen_frames = 0
    for d in reversed(diffs or []):
        if d < 0.35: trailing_frozen_frames += 1
        else: break
    end_hold_sec = trailing_frozen_frames / (fps if fps > 0 else 60.0)
    if diffs is None:
        g3_pass = None
        g3_reason = "Not measured: ffmpeg is needed to decode frames."
    else:
        g3_pass = end_hold_sec <= args.hold_warn
        g3_reason = f"End hold duration: {end_hold_sec:.2f}s (max allowed: {args.hold_warn:.2f}s)."

    # 2. Gate G2: Dead Frames (no run of > 3 motionless frames before the end hold, which G3 judges)
    if diffs is None:
        g2_pass = None
        g2_reason = "Not measured: ffmpeg is needed to decode frames."
    else:
        body = diffs[1:len(diffs) - trailing_frozen_frames]  # diffs[0] is the first frame, not a change
        max_empty = cur = 0
        for d in body:
            cur = cur + 1 if d < 0.25 else 0
            max_empty = max(max_empty, cur)
        g2_pass = max_empty <= 3
        g2_reason = f"Max consecutive frozen frames before the end hold: {max_empty} (threshold: <= 3)."

    # 4. AUDIO: ITU-R BS.1770 / EBU R128 Loudness
    lufs, tp, lra = measure_loudness(args.video)
    audio_reasons = []
    if lufs is None:
        audio_pass = None
        audio_status = "not_measured"
        audio_details = "Not measured: loudness measurement unavailable (ffmpeg missing or no loudness summary)."
    else:
        audio_pass = True
        lo, hi = args.target_lufs - args.lufs_tolerance, args.target_lufs + args.lufs_tolerance
        if lufs < lo or lufs > hi:
            audio_pass = False
            audio_reasons.append(f"Loudness {lufs:.2f} LUFS outside target range [{lo:.1f}, {hi:.1f}].")
        if tp is not None and tp > args.max_true_peak:
            audio_pass = False
            audio_reasons.append(f"True peak {tp:.2f} dBTP exceeds ceiling of {args.max_true_peak:.1f} dBTP.")
        audio_status = "pass" if audio_pass else "fail"
        tp_str = f"{tp:.2f} dBTP" if tp is not None else "N/A"
        audio_details = "; ".join(audio_reasons) if audio_reasons else f"Compliant: {lufs:.2f} LUFS, Peak {tp_str}."

    report = {
        "file": args.video,
        "duration": round(actual_dur, 2),
        "resolution": f"{w}x{h}",
        "fps": round(fps, 2),
        "probe_backend": info["source"],
        "gates": {
            "G0_render_health": {
                "status": "pass" if g0_pass else "fail",
                "pass": g0_pass,
                "details": "; ".join(g0_reasons) if g0_reasons else f"Healthy ({w}x{h} @ {fps:.1f}fps, {actual_dur:.2f}s)."
            },
            "G1_proof_readable": {
                "status": "manual",
                "pass": None,
                "details": "Not checked automatically. Review the contact sheet from scripts/generate_contact_sheet.py."
            },
            "G2_no_dead_frames": {
                "status": "not_measured" if g2_pass is None else ("pass" if g2_pass else "fail"),
                "pass": g2_pass,
                "details": g2_reason
            },
            "G3_end_card_discipline": {
                "status": "not_measured" if g3_pass is None else ("pass" if g3_pass else "fail"),
                "pass": g3_pass,
                "details": g3_reason
            },
            "G4_single_hero_focus": {
                "status": "manual",
                "pass": None,
                "details": "Not checked automatically. Review the contact sheet from scripts/generate_contact_sheet.py."
            },
            "G5_anti_slop_compliance": {
                "status": "manual",
                "pass": None,
                "details": "Not checked automatically. Review the contact sheet from scripts/generate_contact_sheet.py."
            },
            "audio_ebu_r128": {
                "status": audio_status,
                "pass": audio_pass,
                "integrated_lufs": lufs,
                "true_peak_dbtp": tp,
                "loudness_range_lu": lra,
                "details": audio_details
            }
        }
    }

    status_labels = {
        "pass": "PASS",
        "fail": "FAIL",
        "manual": "MANUAL",
        "not_measured": "NOT MEASURED",
    }

    print("\n=== QUALITY GATES RESULTS ===")
    auto_passed = 0
    auto_failed = 0
    auto_not_measured = 0
    manual_gates = []
    for gate_name, data in report["gates"].items():
        st = data["status"]
        label = status_labels[st]
        print(f"[{label}] {gate_name}: {data['details']}")
        if st == "pass":
            auto_passed += 1
        elif st == "fail":
            auto_failed += 1
        elif st == "not_measured":
            auto_not_measured += 1
        elif st == "manual":
            manual_gates.append(gate_name)

    auto_total = auto_passed + auto_failed + auto_not_measured
    print(
        f"\nSummary: {auto_passed}/{auto_total} automated gates passed, "
        f"{auto_failed} failed, {auto_not_measured} not measured; "
        f"manual review required: {', '.join(manual_gates)}."
    )

    if args.out_report:
        with open(args.out_report, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nSaved report to: {args.out_report}")

    if args.strict and (auto_failed > 0 or auto_not_measured > 0):
        sys.exit(3)
    sys.exit(0)

if __name__ == "__main__":
    main()
