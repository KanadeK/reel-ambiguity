"""Strict video-cut EDL preflight against explicit, operator-reviewed source spans."""

from __future__ import annotations

import csv
import io
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

RATES = {
    "24": 24,
    "25": 25,
    "30": 30,
    "50": 50,
    "60": 60,
    "24000/1001": 24,
    "30000/1001": 30,
    "60000/1001": 60,
}
MAX_BYTES = 16 * 1024 * 1024
MAX_EVENTS = 20_000
MAX_SOURCES = 100_000
MAX_CHECKS = 2_000_000
MAX_MATCHES = 100_000
REEL = re.compile(r"[A-Za-z0-9_.-]{1,128}")
TC = re.compile(r"([0-9]{2}):([0-9]{2}):([0-9]{2})([:;])([0-9]{2})")


class InputError(ValueError):
    """Input cannot be checked without guessing."""


@dataclass(frozen=True)
class Clock:
    rate: str
    drop: bool

    def __post_init__(self):
        if self.rate not in RATES:
            raise InputError("unsupported rate; use an integer or documented rational rate")
        if self.drop and self.rate not in {"30000/1001", "60000/1001"}:
            raise InputError("DROP FRAME requires rate 30000/1001 or 60000/1001")

    def frames(self, value: str) -> int:
        match = TC.fullmatch(value)
        if not match:
            raise InputError(f"invalid timecode: {value!r}")
        hour, minute, second, separator, frame = match.groups()
        hour, minute, second, frame = map(int, (hour, minute, second, frame))
        nominal = RATES[self.rate]
        if hour >= 24 or minute >= 60 or second >= 60 or frame >= nominal:
            raise InputError(f"timecode field outside range: {value!r}")
        if separator == ";" and not self.drop:
            raise InputError("semicolon timecode conflicts with NON-DROP FRAME")
        drop_count = nominal // 15 if self.drop else 0
        if self.drop and minute % 10 != 0 and second == 0 and frame < drop_count:
            raise InputError(f"nonexistent drop-frame label: {value!r}")
        minutes = hour * 60 + minute
        return (
            (hour * 3600 + minute * 60 + second) * nominal
            + frame
            - drop_count * (minutes - minutes // 10)
        )


@dataclass(frozen=True)
class Event:
    number: int
    line: int
    reel: str
    start: int
    end: int
    record_start: int
    record_end: int


@dataclass(frozen=True)
class Source:
    clip_id: str
    reel: str
    start: int
    end: int
    row: int


def read_text(path: str | Path) -> str:
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise InputError("input exceeds 16 MiB limit")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeError as exc:
        raise InputError("input must be UTF-8 text") from exc
    if "\0" in text:
        raise InputError("NUL bytes are not allowed")
    return text


def _span(clock: Clock, start: str, end: str) -> tuple[int, int]:
    a, b = clock.frames(start), clock.frames(end)
    if b <= a:
        raise InputError("source/record spans must be positive; midnight wrapping is unsupported")
    return a, b


def parse_edl(text: str, rate: str) -> tuple[Clock, list[Event], int]:
    clock = None
    events = []
    ids = set()
    comments = 0
    titled = False
    for line_no, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line:
            continue
        if len(line) > 8192:
            raise InputError(f"EDL line {line_no} exceeds 8192 characters")
        if line.startswith("TITLE:"):
            if titled or events:
                raise InputError("TITLE must occur at most once before events")
            titled = True
            continue
        if line.startswith("FCM:"):
            if clock is not None or events:
                raise InputError("exactly one FCM declaration must precede events")
            mode = line[4:].strip()
            if mode not in {"DROP FRAME", "NON-DROP FRAME"}:
                raise InputError("unknown FCM mode")
            clock = Clock(rate, mode == "DROP FRAME")
            continue
        if line.startswith("*"):
            # Comments are never a hidden relinking rule. Speed effects require
            # separate supported syntax; only informational comment forms pass.
            if not line.startswith(("* FROM CLIP NAME:", "* SOURCE FILE:", "* COMMENT:")):
                raise InputError(f"unsupported EDL comment/directive at line {line_no}")
            comments += 1
            continue
        if clock is None:
            raise InputError("FCM declaration required before events")
        fields = line.split()
        if len(fields) != 8 or not re.fullmatch(r"[0-9]{1,9}", fields[0]):
            raise InputError(f"unsupported or malformed EDL statement at line {line_no}")
        number, reel, track, transition, sin, sout, rin, rout = fields
        number = int(number)
        if number in ids:
            raise InputError(f"duplicate event number {number}")
        if not REEL.fullmatch(reel) or track != "V" or transition != "C":
            raise InputError(f"line {line_no}: only video cuts with ASCII reel names are supported")
        start, end = _span(clock, sin, sout)
        record_start, record_end = _span(clock, rin, rout)
        if end - start != record_end - record_start:
            raise InputError(
                f"line {line_no}: source/record durations differ (speed effects unsupported)"
            )
        if events and record_start < events[-1].record_end:
            raise InputError("record spans overlap or are out of order")
        events.append(Event(number, line_no, reel, start, end, record_start, record_end))
        ids.add(number)
        if len(events) > MAX_EVENTS:
            raise InputError("EDL event limit exceeded")
    if clock is None or not events:
        raise InputError("EDL must declare FCM and contain at least one video cut")
    return clock, events, comments


def parse_inventory(text: str, clock: Clock) -> list[Source]:
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        header = next(reader, None)
        if header is None or len(header) != 4 or set(header) != {"clip_id", "reel", "start", "end"}:
            raise InputError(
                "CSV requires exactly clip_id,reel,start,end columns, without duplicates"
            )
        sources = []
        ids = set()
        for fields in reader:
            if not fields:  # blank physical lines are not inventory records
                continue
            if len(fields) != 4:
                raise InputError(f"CSV row ending at line {reader.line_num} must have four fields")
            item = dict(zip(header, fields, strict=True))
            if any(not value or value != value.strip() for value in fields):
                raise InputError("CSV fields must be nonempty with no surrounding whitespace")
            clip_id, reel = item["clip_id"], item["reel"]
            if len(clip_id) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in clip_id):
                raise InputError(
                    "clip_id must be at most 256 characters without control characters"
                )
            if clip_id in ids:
                raise InputError(f"duplicate clip_id: {clip_id!r}")
            if not REEL.fullmatch(reel) or reel == "BL":
                raise InputError("inventory reel must be ASCII; BL is reserved for generated black")
            start, end = _span(clock, item["start"], item["end"])
            sources.append(Source(clip_id, reel, start, end, reader.line_num))
            ids.add(clip_id)
            if len(sources) > MAX_SOURCES:
                raise InputError("inventory source limit exceeded")
    except csv.Error as exc:
        raise InputError(f"malformed CSV: {exc}") from exc
    if not sources:
        raise InputError("inventory contains no source rows")
    return sources


def audit(
    edl_text: str,
    inventory_text: str,
    rate: str,
    *,
    reel_width: int | None = None,
    case_fold: bool = False,
    handles: int = 0,
) -> dict:
    if reel_width is not None and (type(reel_width) is not int or not 1 <= reel_width <= 128):
        raise InputError("reel width must be 1..128")
    if type(handles) is not int or not 0 <= handles <= 1_000_000:
        raise InputError("handles must be 0..1000000 frames per side")
    clock, events, comments = parse_edl(edl_text, rate)
    sources = parse_inventory(inventory_text, clock)

    def key(reel):
        value = reel[:reel_width] if reel_width is not None else reel
        return value.casefold() if case_fold else value

    groups = defaultdict(list)
    for source in sources:
        groups[key(source.reel)].append(source)
    collisions = []
    for normalized, group in sorted(groups.items()):
        originals = sorted({s.reel for s in group})
        if len(originals) > 1:
            collisions.append(
                {"key": normalized, "original_reels": originals, "source_count": len(group)}
            )
    results = []
    checks = matches_total = 0
    for event in events:
        candidates = []
        status = "generated_black" if event.reel == "BL" else "missing_source"
        if event.reel != "BL":
            group = groups.get(key(event.reel), ())
            checks += len(group)
            if checks > MAX_CHECKS:
                raise InputError(
                    "matching work limit exceeded; split EDL/inventory into smaller batches"
                )
            for source in group:
                if source.start <= event.start and source.end >= event.end:
                    candidates.append(
                        {
                            "clip_id": source.clip_id,
                            "reel": source.reel,
                            "row": source.row,
                            "head_frames": event.start - source.start,
                            "tail_frames": source.end - event.end,
                        }
                    )
            candidates.sort(key=lambda c: c["clip_id"])
            matches_total += len(candidates)
            if matches_total > MAX_MATCHES:
                raise InputError("candidate report limit exceeded; split inputs")
            if len(candidates) > 1:
                status = "ambiguous"
            elif len(candidates) == 1:
                c = candidates[0]
                status = (
                    "ok"
                    if min(c["head_frames"], c["tail_frames"]) >= handles
                    else "insufficient_handles"
                )
        results.append(
            {
                "event": event.number,
                "line": event.line,
                "reel": event.reel,
                "source_start_frame": event.start,
                "source_end_frame": event.end,
                "status": status,
                "candidates": candidates,
            }
        )
    return {
        "schema": "reel-ambiguity/1",
        "ok": all(e["status"] in {"ok", "generated_black"} for e in results),
        "policy": {
            "rate": rate,
            "drop_frame": clock.drop,
            "reel_width": reel_width,
            "case_fold": case_fold,
            "handles_per_side": handles,
        },
        "informational_comments": comments,
        "reel_collisions": collisions,
        "events": results,
    }
