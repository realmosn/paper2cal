#!/usr/bin/env python3
"""Run every photo in a samples folder through the model and score it against its expected.json.

python evaluate.py                      # your private samples/ (default)
python evaluate.py --samples examples   # the public example slip
python evaluate.py --cached             # re-score the last results without calling the API

An expected.json may contain "_today": "YYYY-MM-DD". The run then uses that as today's date,
so slips without a year resolve the same way on every run, no matter when it is run.
"""

import argparse
import datetime as dt
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from extract import MODEL, OUT, ROOT, extract

PHOTO_TYPES = {".heic", ".jpg", ".jpeg", ".png"}
MAX_TITLE = 45  # longer titles get cut off in the iPhone calendar views

FORBIDDEN = [
    "angekreuzt",
    "handschriftlich",
    "arztpraxis",
]  # slip descriptions / placeholders


def prompt_version() -> str:
    """Last commit that touched prompts/, plus '+dirty' if there are uncommitted edits."""
    try:
        sha = (
            subprocess.run(
                ["git", "log", "-1", "--format=%h", "--", "prompts/"],
                capture_output=True,
                text=True,
                cwd=ROOT,
            ).stdout.strip()
            or "uncommitted"
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "prompts/"],
            capture_output=True,
            text=True,
            cwd=ROOT,
        ).stdout.strip()
        return sha + ("+dirty" if dirty else "")
    except OSError:
        return "unknown"


def run(photo: Path, cached: bool) -> tuple[str, tuple[int, int]]:
    """Return (answer text, (input tokens, output tokens)) for one photo."""
    cache = OUT / f"{photo.stem}.txt"
    if cached:
        return (cache.read_text() if cache.exists() else ""), (0, 0)
    text, resp = extract(photo)
    cache.write_text(text)
    return text, (resp.usage.input_tokens, resp.usage.output_tokens)


def score(events: list[dict], expected: list[dict]) -> list[str]:
    """Compare one photo's events with its answer key. Returns a list of problems."""
    problems = []
    if len(events) != len(expected):
        problems.append(f"expected {len(expected)} event(s), got {len(events)}")
    by_start = {e.get("start"): e for e in events}
    for exp in expected:
        ev = by_start.get(exp["start"])
        if ev is None:
            problems.append(f"no event starting {exp['start']}")
            continue
        if ev.get("end") != exp["end"]:
            problems.append(
                f"{exp['start']}: end is {ev.get('end')}, expected {exp['end']}"
            )
        text = " ".join(
            str(ev.get(k, "")) for k in ("title", "location", "notes")
        ).lower()
        for word in exp["mentions"]:
            if word.lower() not in text:
                problems.append(f"{exp['start']}: '{word}' not mentioned")
        for word in FORBIDDEN:
            if word in text:
                problems.append(f"{exp['start']}: contains '{word}'")
        title = ev.get("title", "")
        if len(title) > MAX_TITLE:
            problems.append(
                f"{exp['start']}: title too long ({len(title)} chars): {title}"
            )
    return problems


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cached", action="store_true", help="re-score saved results, no API calls"
    )
    parser.add_argument(
        "--samples",
        default="samples",
        metavar="DIR",
        help="folder with the photos and their expected.json (default: samples)",
    )
    args = parser.parse_args()

    samples = ROOT / args.samples
    expected = json.loads((samples / "expected.json").read_text())
    pinned = expected.pop("_today", None)
    if pinned:  # the answer key was written for this date, so pin it
        os.environ["PAPER2CAL_TODAY"] = pinned
    photos = sorted(p for p in samples.iterdir() if p.suffix.lower() in PHOTO_TYPES)
    OUT.mkdir(exist_ok=True)

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda p: run(p, args.cached), photos))

    version = prompt_version()
    print(
        f"samples {args.samples}   today {pinned or 'real date'}   "
        f"prompt {version}   model {MODEL}{'   (cached)' if args.cached else ''}\n"
    )
    passed = warned = tokens_in = tokens_out = 0
    for photo, (text, (t_in, t_out)) in zip(photos, results):
        tokens_in, tokens_out = tokens_in + t_in, tokens_out + t_out
        if not text:
            print(f"  ----  {photo.stem}  no result yet (run without --cached)")
            continue
        try:
            events = json.loads(text)["events"]
        except (json.JSONDecodeError, KeyError, TypeError):
            print(f"  FAIL  {photo.stem}  response is not valid JSON")
            continue
        warnings = sum(1 for e in events if e.get("warning"))
        warned += warnings
        if photo.stem not in expected:
            print(f"  ----  {photo.stem}  no answer key, {len(events)} event(s)")
            continue
        problems = score(events, expected[photo.stem])
        passed += not problems
        print(
            f"  {'FAIL' if problems else 'PASS'}  {photo.stem}  {len(events)} event(s), {warnings} with warning"
        )
        for p in problems:
            print(f"          - {p}")

    keyed = sum(p.stem in expected for p in photos)
    print(
        f"\n{passed}/{keyed} photos pass · {warned} events with warning · tokens in/out {tokens_in}/{tokens_out}"
    )

    if not args.cached:  # keep a history so prompt versions can be compared later
        with (OUT / "history.tsv").open("a") as f:
            f.write(
                f"{dt.datetime.now():%Y-%m-%d %H:%M}\t{version}\t{MODEL}\t{passed}/{keyed}\t{warned}\t{tokens_in}\t{tokens_out}\t{args.samples}\n"
            )


if __name__ == "__main__":
    main()
