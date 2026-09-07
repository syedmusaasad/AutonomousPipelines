#!/usr/bin/env python3
"""Create the large, disposable conversation fixture used by the TUI probe."""

import argparse
import json
import random
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path


PART_COUNT = 20_000
TRANSCRIPT_LINES = 100_000


def generate(target: Path) -> tuple[int, int]:
    # The seeded generator makes identifiers stable while timestamps remain a
    # faithful representation of a freshly-created session.
    rng = random.Random(0)
    target = Path(target)
    if target.exists():
        if not target.is_dir():
            raise ValueError(f"target is not a directory: {target}")
        for child in target.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
    else:
        target.mkdir(parents=True)

    now_ms = int(time.time() * 1000)
    db_path = target / "giant.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE session (
                id text PRIMARY KEY, title text, directory text,
                time_created integer, time_updated integer
            );
            CREATE TABLE message (
                id text PRIMARY KEY, session_id text, time_created integer,
                time_updated integer, data text
            );
            CREATE TABLE part (
                id text PRIMARY KEY, message_id text, session_id text,
                time_created integer, time_updated integer, data text
            );
        """)
        conn.execute("INSERT INTO session VALUES (?,?,?,?,?)",
                     ("ses_giant", "giant replay probe", str(target), now_ms, now_ms))
        messages = []
        parts = []
        for n in range(PART_COUNT):
            # Consume the seeded stream without making the fixture's observable
            # text dependent on implementation details of random.
            suffix = rng.randrange(1 << 30)
            mid = f"m_giant_{n}_{suffix}"
            pid = f"p_giant_{n}_{suffix}"
            ts = now_ms + n
            messages.append((mid, "ses_giant", ts, ts, json.dumps({"role": "assistant"})))
            kind = "reasoning" if n % 50 == 0 else "text"
            body = f"giant replay line {n}"
            parts.append((pid, mid, "ses_giant", ts, ts,
                          json.dumps({"type": kind, "text": body})))
            if len(parts) == 1000:
                conn.executemany("INSERT INTO message VALUES (?,?,?,?,?)", messages)
                conn.executemany("INSERT INTO part VALUES (?,?,?,?,?,?)", parts)
                messages.clear()
                parts.clear()
        if parts:
            conn.executemany("INSERT INTO message VALUES (?,?,?,?,?)", messages)
            conn.executemany("INSERT INTO part VALUES (?,?,?,?,?,?)", parts)
        conn.commit()
    finally:
        conn.close()

    transcript_path = target / "giant_transcript.jsonl"
    with transcript_path.open("w", buffering=1024 * 1024, encoding="utf-8") as out:
        for n in range(TRANSCRIPT_LINES):
            out.write(json.dumps({"type": "text", "part": {"text": f"line {n}"}}))
            out.write("\n")
    return db_path.stat().st_size, transcript_path.stat().st_size


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default="/tmp/devpass-code/tui-probe-fixture/giant")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        with tempfile.TemporaryDirectory() as directory:
            db_size, jsonl_size = generate(Path(directory))
            with sqlite3.connect(str(Path(directory) / "giant.db")) as conn:
                assert conn.execute("select count(*) from part").fetchone()[0] == PART_COUNT
            assert sum(1 for _ in (Path(directory) / "giant_transcript.jsonl").open()) == TRANSCRIPT_LINES
        print("SELFTEST-OK")
        print(f"db bytes: {db_size}")
        print(f"jsonl bytes: {jsonl_size}")
    else:
        db_size, jsonl_size = generate(Path(args.target))
        print(f"db bytes: {db_size}")
        print(f"jsonl bytes: {jsonl_size}")


if __name__ == "__main__":
    main()
