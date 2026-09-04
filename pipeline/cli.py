"""pipeline CLI. Subcommands:

  run <plan.md>                 launch a detached engine for a plan
  quick -r <role> [-x EXIT]...  one detached dispatch; task on stdin
  finish --subject S --paths..  finisher dispatch: land the exact expected commit
  engine <run> <plan>           (internal) the engine process itself
  status [--mine|--conv ID] [--json]
  conv [ID]                     what did this conversation set in motion
  resume <run>                  lift a deliberate stop after judging; relaunch
  stop <run>                    write a deliberate-stop receipt and SIGTERM the engine
  render-agents / check-agents  regenerate generated config / drift guard
  bench                         emit BENCHMARKS.md text
  trial <role> <model> --tasks F --rubric F    stage a head-to-head
  trial-apply <trial-run> <role> <model>       apply a won trial to the registry
  sentry [--once]               the sentry daemon (or one tick)
  suite                         run the characterization suite and log it
  validate <plan.md>            parse and validate a plan
"""

import argparse
import json
import os
import signal
import sys
from pathlib import Path

from . import bench, engine, finisher, paths, plan as planmod, quick, registry, roles as roles_mod, sentry, status
from .journal import Journal
from .util import log, pid_alive


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("run"); p.add_argument("plan"); p.add_argument("--conv")
    p = sub.add_parser("quick"); p.add_argument("-r", "--role", required=True); p.add_argument("-x", "--exit", action="append", default=[])
    p.add_argument("-C", "--cwd", default=os.getcwd()); p.add_argument("-t", "--timeout", type=int, default=1800); p.add_argument("--review", action="store_true")
    p.add_argument("--conv")
    p = sub.add_parser("finish"); p.add_argument("--subject", required=True); p.add_argument("--paths", nargs="+", required=True)
    p.add_argument("--verify"); p.add_argument("--push"); p.add_argument("--suite"); p.add_argument("-C", "--cwd", default=os.getcwd())
    p = sub.add_parser("engine"); p.add_argument("run"); p.add_argument("plan")
    p = sub.add_parser("status"); p.add_argument("--mine", action="store_true"); p.add_argument("--conv"); p.add_argument("--json", action="store_true"); p.add_argument("run", nargs="?")
    p = sub.add_parser("conv"); p.add_argument("id", nargs="?")
    p = sub.add_parser("resume"); p.add_argument("run")
    p = sub.add_parser("stop"); p.add_argument("run"); p.add_argument("--reason", default="operator")
    sub.add_parser("render-agents"); sub.add_parser("check-agents")
    sub.add_parser("bench")
    p = sub.add_parser("trial"); p.add_argument("role"); p.add_argument("model"); p.add_argument("--tasks", required=True); p.add_argument("--rubric", required=True); p.add_argument("-C", "--cwd", default=os.getcwd())
    p = sub.add_parser("trial-apply"); p.add_argument("run"); p.add_argument("role"); p.add_argument("model")
    p = sub.add_parser("sentry"); p.add_argument("--once", action="store_true")
    sub.add_parser("suite")
    p = sub.add_parser("validate"); p.add_argument("plan")
    a = ap.parse_args(argv)
    return globals()["cmd_" + a.cmd.replace("-", "_")](a)


def cmd_run(a):
    row = engine.launch(Path(a.plan), conversation=a.conv)
    print(f"launched {row['run']} engine_pid={row['engine_pid']} conversation={row['conversation']}")
    print(f"journal: {row['journal']}")
    return 0


def cmd_quick(a):
    task = sys.stdin.read()
    if not task.strip():
        print("quick: task on stdin is empty", file=sys.stderr)
        return 2
    row = quick.launch_quick(task, role=a.role, cwd=Path(a.cwd), exits=a.exit, timeout=a.timeout, conversation=a.conv, review=a.review)
    print(f"launched {row['run']} role={a.role} engine_pid={row['engine_pid']} conversation={row['conversation']}")
    print(f"journal: {row['journal']}")
    return 0


def cmd_finish(a):
    row = finisher.launch_finisher(cwd=Path(a.cwd), subject=a.subject, add_paths=a.paths, verify=a.verify, push=a.push, suite=a.suite)
    print(f"launched finisher {row['run']} engine_pid={row['engine_pid']}")
    return 0


def cmd_engine(a):
    return engine.Engine(a.run, Path(a.plan)).start()


def cmd_status(a):
    if a.run:
        reps = [status.run_report(a.run)]
        scope = a.run
    else:
        conv = a.conv or (registry.current_conversation() if a.mine else None)
        reps = status.all_reports(conv)
        scope = f"conversation {conv}" if conv else "all runs"
    if a.json:
        print(json.dumps(reps, indent=1, default=str))
    else:
        sys.stdout.write(status.render(reps, scope=scope))
    return 0


def cmd_conv(a):
    conv = a.id or registry.current_conversation()
    rows = registry.for_conversation(conv)
    reps = [status.run_report(r["run"]) for r in rows]
    sys.stdout.write(status.render(reps, scope=f"conversation {conv}"))
    return 0


def cmd_resume(a):
    pid = engine.relaunch(a.run, by="operator", cleared=True)
    print(f"resumed {a.run} engine_pid={pid}")
    return 0


def cmd_stop(a):
    j = Journal(a.run)
    st = j.state()
    rdir = paths.run_dir(a.run)
    (rdir / "STOPPED").write_text(f"operator\n{a.reason}\n")
    j.write("run.stop", reason="gate_failed", detail=f"operator stop: {a.reason}")
    if pid_alive(st.get("engine_pid")):
        os.kill(int(st["engine_pid"]), signal.SIGTERM)
    j.write("run.close", outcome="stopped")
    print(f"stopped {a.run}")
    return 0


def cmd_render_agents(a):
    for p in roles_mod.write_agents():
        print(p)
    return 0


def cmd_check_agents(a):
    d = roles_mod.drift()
    if d:
        print("DRIFT: generated agent files differ from registry rendering:")
        for x in d:
            print("  " + x)
        return 1
    print("agents match registry")
    return 0


def cmd_bench(a):
    sys.stdout.write(bench.render(bench.collect()))
    return 0


def cmd_trial(a):
    from . import trial
    reg = roles_mod.load()
    tasks = trial.parse_tasks(Path(a.tasks).read_text())
    if not tasks:
        print("no tasks (use '## name' headings)", file=sys.stderr)
        return 2
    tdir = Path(a.cwd).resolve() / f"trial-{a.role}-{a.model.replace('/', '_')}"
    tdir.mkdir(parents=True, exist_ok=True)
    text = trial.trial_plan(a.role, a.model, tasks, Path(a.rubric).read_text(), tdir, reg)
    (tdir / "plan.md").write_text(text)
    row = engine.launch(tdir / "plan.md")
    print(f"trial launched {row['run']}; when done: pipeline trial-apply {row['run']} {a.role} {a.model}")
    return 0


def cmd_trial_apply(a):
    from . import trial
    reg = roles_mod.load()
    j = Journal(a.run)
    st = j.state()
    if st["closed"] != "done":
        print(f"trial run {a.run} is not done ({st['closed']}); refusing", file=sys.stderr)
        return 2
    tdir = Path(st["cwd"])
    verdict = trial.decide(tdir, st, incumbent_q=roles_mod.seat(a.role, reg)["model_q"], candidate_q=roles_mod.qualified(a.model, reg))
    (tdir / "trial" / "verdict.json").write_text(json.dumps(verdict, indent=1))
    print(json.dumps(verdict, indent=1))
    try:
        trial.apply(a.role, a.model, verdict)
    except roles_mod.RegistryError as e:
        print(f"not applied: {e}")
        return 1
    roles_mod.write_agents()
    print(f"applied: {a.role} -> {a.model}; agents re-rendered")
    return 0


def cmd_sentry(a):
    if a.once:
        print(json.dumps(sentry.tick(), default=str))
        return 0
    return sentry.main_loop()


def cmd_suite(a):
    import subprocess
    return subprocess.call([sys.executable, str(paths.repo_root() / "tests" / "run.py")])


def cmd_validate(a):
    try:
        pl = planmod.parse_file(a.plan)
        planmod.validate_roles(pl, set(roles_mod.load()["roles"]) | {planmod.GATE_ROLE})
    except planmod.PlanError as e:
        print(f"INVALID: {e}")
        return 1
    print(f"valid: {len(pl.phases)} phases, order {planmod.topo_order(pl)}, workdir {pl.workdir}")
    for ph in pl.phases:
        flags = []
        if ph.exits: flags.append(f"exit={len(ph.exits)}")
        if ph.after_explicit: flags.append(f"after={ph.after}")
        if ph.lanes: flags.append(f"lanes={ph.lanes} ceiling={ph.ceiling}")
        if ph.review: flags.append("review=cross")
        if ph.surfaces: flags.append(f"surface={ph.surfaces}")
        if ph.is_gate: flags.append(f"gate={ph.gate}")
        print(f"  {ph.number}: {ph.name} ({ph.role}) timeout={ph.timeout} attempts={ph.attempts} {' '.join(flags)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
