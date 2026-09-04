"""Test harness: isolated estate per test, fake worker, fast timings."""

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAKE = REPO / "tests" / "fake-devpass-code"
sys.path.insert(0, str(REPO))


class Estate:
    """A throwaway estate. Sets env so every module reads from it."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="plsuite-"))
        self.estate = self.tmp / "estate"
        self.work = self.tmp / "work"
        self.agents = self.tmp / "agents"
        self.work.mkdir(parents=True)
        self.estate.mkdir()
        self.log = self.tmp / "fake.log"
        self.env = {
            "PIPELINE_ESTATE": str(self.estate),
            "PIPELINE_WORKER_BIN": str(FAKE),
            "PIPELINE_AGENTS_DIR": str(self.agents),
            "PIPELINE_POLL_S": "0.2",
            "PIPELINE_GATE_POLL_S": "0.2",
            "PIPELINE_BACKOFF_SCALE": "0.01",
            "PIPELINE_STALL_S": "2",
            "PIPELINE_ENGINE_GRACE_S": "0",
            "PIPELINE_SENTRY_TICK_S": "1",
            "PIPELINE_CONVERSATION": "ses_test_conv",
            "FAKE_LOG": str(self.log),
            "PYTHONPATH": str(REPO),
        }
        self._saved = {}

    def __enter__(self):
        for k, v in self.env.items():
            self._saved[k] = os.environ.get(k)
            os.environ[k] = v
        # modules read env at import for some constants; reload the ones that do
        import importlib
        from pipeline import engine, sentry, status
        importlib.reload(engine)
        importlib.reload(sentry)
        importlib.reload(status)
        return self

    def __exit__(self, *exc):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def plan(self, text: str, name="plan.md", sub="p") -> Path:
        d = self.work / sub
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        p.write_text(text)
        return p

    def cli(self, *args, timeout=60, check=False, input=None):
        r = subprocess.run([sys.executable, "-m", "pipeline.cli", *args], capture_output=True, text=True,
                           env={**os.environ}, timeout=timeout, input=input, cwd=str(self.work))
        if check and r.returncode != 0:
            raise AssertionError(f"cli {args} failed rc={r.returncode}\n{r.stdout}\n{r.stderr}")
        return r

    def engine_fg(self, run_id: str, plan: Path, timeout=60):
        """Run an engine in the foreground (as a subprocess) and wait. Tests only."""
        return self.cli("engine", run_id, str(plan), timeout=timeout)

    def fake_calls(self) -> list:
        import json
        if not self.log.exists():
            return []
        return [json.loads(l) for l in self.log.read_text().splitlines() if l.strip()]


def wait_for(pred, timeout=30, every=0.1):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = pred()
        if v:
            return v
        time.sleep(every)
    raise AssertionError("timed out waiting")
