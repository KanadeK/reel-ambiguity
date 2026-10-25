# reel-ambiguity

[![CI](https://github.com/KanadeK/reel-ambiguity/actions/workflows/ci.yml/badge.svg)](https://github.com/KanadeK/reel-ambiguity/actions/workflows/ci.yml)

Find ambiguous source matches **before** conforming a video cut EDL. Compare a
CMX3600 EDL with a reviewed CSV inventory and get a JSON explanation for each
event: unique source, missing source, multiple possible sources, or insufficient
head/tail handles. Runs locally with Python 3.11+, without an NLE or dependencies.

Two camera cards can share timecodes and reel names. An eight-character matching
rule may also turn `CAMERA01_A` and `CAMERA01_B` into the same reel. This tool makes
those candidate matches visible; it never chooses one arbitrarily or relinks files.

## Install and run

Published on GitHub, not PyPI:

```sh
python -m pip install "git+https://github.com/KanadeK/reel-ambiguity.git@v0.1.0"
reel-ambiguity --help
```

Or clone and run `python -m pip install .`. Use `python -m reel_ambiguity` if your
scripts directory is not on PATH. The CLI works on Windows, macOS, and Linux.

From a clone:

```sh
reel-ambiguity examples/cuts.edl examples/sources.csv --rate 24 --reel-width 8
reel-ambiguity examples/cuts.edl examples/ambiguous.csv --rate 24 --reel-width 8
```

The first exits **0**. The second exits **1**: event 1 matches two different camera
card clips after truncation. Both candidate IDs and original reel names appear in
the JSON. The included examples are original synthetic data.

## Prepare your inventory

Export or prepare UTF-8 CSV with **exactly** these columns, in any order:

```csv
clip_id,reel,start,end
shot-A,CAMERA01_A,01:00:00:00,01:00:10:00
shot-B,CAMERA02_A,02:00:00:00,02:00:05:00
```

- Each `clip_id` must identify one source clip uniquely. Quoted commas are allowed.
- `reel` is the complete reel name, not a file path. ASCII letters, digits, `.`, `_`
  and `-`, up to 128 characters. `BL` is reserved for generated black in the EDL.
- `start` is inclusive and `end` **exclusive**, both source timecodes. If your NLE
  exports the last included frame as Out, add one frame before using this CSV.
- EDL and inventory must use the same declared rate and FCM timecode mode. The
  inventory is operator evidence: this application does not open the media or
  verify its existence, checksum, embedded timecode, or actual duration.

```sh
reel-ambiguity edit.edl sources.csv --rate 30000/1001 --reel-width 8 --case-fold --handles 12
```

`--reel-width` truncates **both** reel strings, then `--case-fold` ignores ASCII
case. Without these options matching is exact. Match the intended NLE policy;
there is no automatic filename fallback or policy guessing. `--handles 12`
requires 12 additional available frames on **each** side of a uniquely matched
source. Handles never resolve an otherwise ambiguous clip identity.

## Results and exit statuses

JSON includes the chosen policy, per-event source frame span, EDL line, candidates,
CSV line numbers, and available head/tail frames. Reel normalization collisions
are listed even if non-overlapping timecodes make individual events unambiguous.
Those informational collisions alone do not fail the check.

| Status | Meaning |
|---|---|
| `ok` | Exactly one source covers the complete event and required handles |
| `missing_source` | No matching reel covers the full source span |
| `ambiguous` | More than one source covers the full span |
| `insufficient_handles` | Unique source, but one or both margins are too short |
| `generated_black` | Reserved EDL reel `BL`; no media lookup needed |

Exit **0**: all events `ok` or `generated_black`. Exit **1**: a conform finding.
Exit **2**: malformed, unsupported, unreadable input, invalid options, or resource
limit. Errors go to stderr; no partial success report is emitted. Reports contain
clip IDs, reel names and timecodes, so treat them like project metadata.

## Explicit v0.1.0 scope

- Video `V`, cut `C` events only, in non-overlapping record-time order. Gaps allowed.
- Exactly one `FCM: NON-DROP FRAME` or `FCM: DROP FRAME` before events; optional TITLE.
- Nominal rates 24/25/30/50/60 and rational 24000/1001, 30000/1001, 60000/1001.
  Drop-frame numbering only at 30000/1001 and 60000/1001. Nonexistent dropped labels
  are errors; colons are accepted in DF EDLs because FCM is authoritative.
- Only `* FROM CLIP NAME:`, `* SOURCE FILE:` and `* COMMENT:` remarks are accepted,
  counted, and treated as informational. Paths/names in comments never select media.
- Dissolves, wipes, audio events, time warps, freeze frames, other directives,
  mixed modes, duplicate events, and midnight-wrapping spans are rejected. Source
  and record durations must agree. Unsupported material cannot silently pass.
- 16 MiB per input, 20,000 events, 100,000 source rows, 2 million matching checks
  and 100,000 reported candidates. Split large or highly ambiguous batches. These
  limits bound work; this is designed for handoff preflight, not a media database.

[pycmx](https://github.com/iluvcapra/pycmx) is a broader EDL parser and explicitly
leaves timecode/rate validation to its callers. This project instead provides a
strict inventory-based acceptance check. It cannot prove that a unique candidate
is the editorially intended clip, nor guarantee a particular NLE's relinking.

## Development and cleanup

See [CONTRIBUTING.md](CONTRIBUTING.md). Tests cover actual CLI examples, ambiguous
and missing sources, exact endpoint/handle boundaries, NTSC drop-frame landmarks,
invalid labels, unsupported EDL effects and bounded failure behavior.

Uninstall with `python -m pip uninstall reel-ambiguity`. Delete any saved reports
when no longer needed. No media are changed and no background service or cache is
created. MIT licensed; no third-party parser code or media is bundled.

## 中文说明

在视频 EDL 回套之前，用 CSV 素材清单检查卷名和源时间码是否能唯一匹配素材。
显式选择卷名截断宽度和大小写规则，报告重复候选、素材缺失与两侧预留帧不足。
当前支持单轨视频硬切、整帧及指定 NTSC 丢帧编号；转场、变速、音频轨和跨午夜区间
会明确拒绝，不会猜测。清单由操作者提供，工具不读取素材本体、不代替最终人工回套检查。
