# Iterative migration demo

## Phase 1: migrate fixture (implementer)
ITERATE: on
CEILING: 4
EXIT: python3 verify.py

Complete the four deterministic checks named in `tasks.md`. Maintain
`.pipeline/phase-1/progress.md` by checking off completed items.

FAKE: touch-on-call 1 migration/item-1.done
FAKE: touch-on-call 1 migration/item-2.done
FAKE: touch-on-call 2 migration/item-3.done
FAKE: touch-on-call 2 migration/item-4.done
FAKE: write-on-call 1 .pipeline/phase-1/progress.md <<# Migration progress\n\n- [x] Item 1\n- [x] Item 2\n- [ ] Item 3\n- [ ] Item 4>>
FAKE: write-on-call 2 .pipeline/phase-1/progress.md <<# Migration progress\n\n- [x] Item 1\n- [x] Item 2\n- [x] Item 3\n- [x] Item 4>>
