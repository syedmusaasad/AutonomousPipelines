# TUI dogfood capture

Date: 2026-09-07.  The capture used the isolated `pipeline-tui-probe` tmux
server and a real quick dispatch, `q_20260907T170117_ffcb95`, launched through
`bin/quick`.  It wrote `docs/TUI-DOGFOOD-DELIVERABLE.txt`; its engine-run EXIT
was `test -s docs/TUI-DOGFOOD-DELIVERABLE.txt`.

## Pipelines / live dispatch

The quick was deliberately held open for observation.  Quicks are shown on
**Dispatches**, not **Pipelines**; the Pipelines capture honestly showed the
empty plan-run state while the quick was running:

```text
 1:Pipelines  2:Dispatches  3:Files  4:Conversation    this conversation
---------------------------------------------------------------------------
(no plan runs launched by this conversation)
...
scroll:live  |  mouse:pipeline (m: native-terminal select)
```

Live run status at the same point:

```text
q_20260907T170117_ffcb95  running
  phase 1: running role=implementer attempts=1
  worker d_20260907T170117_545491 ... alive transcript_age=36s
```

## Dispatches / cost

After completion, the Dispatches pane rendered the real elapsed time, token
count, and cost:

```text
q_20260907T170117_ffcb95     implementer      done
wall=   84.9s cost=$0.0139 tok=5000
```

## Files / deliverable

The Files pane registered the deliverable from the completed dispatch:

```text
q_20260907T170117_ffcb95     phase=1    implementer
docs/TUI-DOGFOOD-DELIVERABLE.txt
```

## Conversation replay

The Conversation pane replayed the attached session from its SQLite database,
including tool activity and the read-only composer state:

```text
« assistant
  [tool:completed] bash ... tmux -L pipeline-tui-probe capture-pane ...
...
>  read-only: no writer attached for this session (typing is not sent)
scroll:live  |  mouse:pipeline (m: native-terminal select)  |
read-only: no writer attached for this session (typing is not sent)
```

The run closed successfully after its EXIT passed.  The isolated probe server
was stopped after these captures.
