# filmcraft-effectcraft-studio

A skill (instruction file) for AI coding agents such as Claude Code that guides video editing, motion graphics, and audio mixing in FilmCraft and EffectCraft.

FilmCraft is a timeline video editor with integer tick timing, Lumetri color grading, and Essential Sound mixing. EffectCraft is a 2D and 3D motion graphics engine with shape modifiers, expressions, and Lottie/After Effects support.

## Why

- Without guidance, agents often cut talking-head footage by raw silence thresholds, creating choppy edits with no J/L-cut overlap or audio crossfades.
- Agents tend to animate graphics with linear interpolation or zero-scale growth, and leave motionless frames or long static holds at the end.
- Agents often add visual clutter such as corner labels, chapter counters, typewriter headlines, and generic placeholder UI cards.
- Agents may report an edit as done without checking the rendered file for decode errors, loudness levels, or pacing.

## What it does

- Guides the agent to connect to FilmCraft (`127.0.0.1:9876`) for timeline cuts, color grading, and audio ducking, and to EffectCraft (`127.0.0.1:9877`) for 3D cameras, kinetic text, and alpha overlays.
- Asks the agent to use 12 easing curves (`presets/effectcraft-ease-presets.json`), 5 EffectCraft motion presets (`presets/effectcraft-motion-presets.json`), and 16 motion mechanisms (`references/measured-motion-and-mechanisms.md`), avoiding generic linear eases and scaling from zero.
- Enforces rough-cut and voice rules: keeping the last complete take, removing spoken editor notes via `transcript.inspect`, applying parametric voice EQ, and ducking music under speech.
- Calculates musical back-timing with `scripts/backtime_music_calc.py` so the music track's final downbeat lands on the end-card cut.
- Checks rendered outputs with `scripts/audit_render_gates.py` (for `G0`, `G2`, `G3`, and loudness: `-14.0 LUFS` ±1 and `-1.0 dBTP` by default, other targets with `--target-lufs`) and builds a frame grid with `scripts/generate_contact_sheet.py` for visual review of `G1`, `G4`, and `G5`.
- Documents how to import external assets from Mixkit (`.prfpset`, `.wav`), LottieFiles (`.json`, `.lottie`), Luttie/ON1 (`.cube` LUTs), and CinePacks overlays.

## Files

- `SKILL.md` — Main skill instructions, workflow checklist, and quality gate summary.
- `README.md` — Overview, file list, and install steps.
- `presets/effectcraft-ease-presets.json` — 12 easing curves mapped to EffectCraft velocity and cubic Bezier values.
- `presets/effectcraft-motion-presets.json` — Templates for snap zoom, whip pan, 3D card tilt, kinetic text, and bounce.
- `presets/filmcraft-command-batches.json` — Command batches for J-cuts, auto-zoom, Lumetri S-curves, and EBU R128 ducking.
- `references/anti-ai-slop-rules.md` — Editorial prohibitions, cut padding, J/L-cut timing, and transient alignment rules.
- `references/effectcraft-mcp-playbook.md` — EffectCraft MCP connection modes, commands, shape modifiers, 3D cameras, and Lottie export.
- `references/external-asset-integration.md` — Rules for importing Mixkit, LottieFiles, Luttie/ON1, and CinePacks assets.
- `references/filmcraft-mcp-playbook.md` — FilmCraft MCP connection modes, tick math, timeline commands, Lumetri color, blend modes, and `.prfpset` import.
- `references/gauntlet-critic-and-gates.md` — `G0`–`G5` quality gates, rejection pattern list, and critic review prompts.
- `references/genre-editing-library.md` — Pacing, easing, color, and audio settings across 8 video genres.
- `references/measured-motion-and-mechanisms.md` — Timing rules, 12 easing curves, and 16 motion mechanisms.
- `references/rough-cut-and-audio-mastering.md` — Retake selection, voice EQ settings, and musical back-timing math.
- `scripts/audit_render_gates.py` — Checks rendered video files for `G0`, `G2`, `G3`, and EBU R128 loudness.
- `scripts/backtime_music_calc.py` — Detects audio transients and trims music to land on the end-card cut.
- `scripts/generate_contact_sheet.py` — Extracts evenly spaced frames into a tiled contact sheet image.

## Install

Copy this folder into `~/.claude/skills/filmcraft-effectcraft-studio/`.

Required tools:

- FilmCraft with its control bridge (`filmcraft --control 9876`, token in `FILMCRAFT_CONTROL_TOKEN`) or `filmcraft-cli mcp`.
- EffectCraft with its control bridge (`effectcraft --control 9877`) or headless (`effectcraft-cli mcp --gpu`).
- Python 3 with `numpy`, `scipy`, and `Pillow`, plus `ffmpeg`/`ffprobe` (needed for real `audit_render_gates.py` checks; `filmcraft-cli` fallback uses a Windows path).

## Test results for `audit_render_gates.py`

Tested with ffmpeg 9.1 on generated clips and two real videos: a 45 s drone clip and a finished 38 s vertical music edit.

| Input | Result |
|---|---|
| Clip at -14.5 LUFS | audio pass |
| Clip at -16.4 LUFS | audio fail; pass with `--target-lufs -16` |
| Clip with a +0.8 dBTP peak (drone clip with an unmastered song at -7.9 LUFS) | audio fail |
| Drone clip with a silent audio track | audio fail |
| Same drone clip with the song normalized | audio pass (-13.6 LUFS) |
| Finished music edit | audio pass (-13.7 LUFS) |
| 1 s frozen picture in the middle | G2 fail |
| 3 s frozen end hold | G3 fail |
| Slow drone move, finished edit | G2 and G3 pass |
| ffmpeg not installed | G2, G3 and audio reported as `not_measured`, not as passed |

These tests found and fixed four bugs:
- Loudness was never really measured. A hard-coded value was used instead, so every render passed.
- No frames were decoded, so G2 and G3 always passed.
- The end hold was also counted as dead frames in G2.
- Slow camera moves were flagged as frozen frames.

`G1`, `G4` and `G5` are not checked automatically. They need a person to look at the contact sheet.

## Status

A personal project, built and tested on the author's own work. The script tests above are small, and there is no formal benchmark of the skill itself.
