# Coding rubric

Score each arm's work for each task from 0 to 10 (whole or half points). The total is the weighted sum of the four criteria below, scaled to 10.

| Criterion | Weight |
|---|---|
| Correctness against the task as written, checked by running the arm's code | 4 |
| Stays inside the task: no unrequested scope | 2 |
| Code clarity and fit to the given style | 2 |
| Quality of the arm's own tests | 2 |

**How to score**

- **Run it, do not just read it.** For every arm and task, change into the arm's folder and run its tests (`python3 -m unittest` there). Then write a few calls of your own against the code, one for each edge case the task lists, and run them. Reading code is not evidence. An arm whose code does not import, or whose own tests fail or error, scores at most 4 on correctness. Run the code from a copy of the folder if you want to leave the arm's files untouched.
- **Correctness (weight 4):** every behaviour the task states holds when you run it, including each edge case, each error case, the exact file names, the exact return shapes and the exact error types. For the bug-fix task, also check both complaints are fixed, that nothing that worked has changed (same function names, keys and output format), and that the arm's regression tests fail on the original program in the task and pass on the corrected one: run them against the original to confirm. For the extension task, the original tests must be present, unchanged and passing, and the old behaviour with no `ttl` must be unchanged.
- **Stays inside the task (weight 2):** only the requested files exist in the folder (stray scratch files, caches kept on purpose and notes count against this), and the code adds only what the task asks for. Extra public functions, options, rewrites of working code, renamed things, reformatted untouched code and new dependencies lower the score. For the bug-fix task, the smaller and more targeted the fix, the better.
- **Clarity and fit to the style (weight 2):** readable names, short functions, comments only where they help, docstrings in the style of the code given, and, for the two tasks that embed code, the same conventions as the embedded code. Over-engineering is not clarity.
- **Quality of the arm's own tests (weight 2):** the tests run and pass, are independent of each other and of the clock or the machine, check results and not only that no exception was raised, and cover the edge and error cases the task lists, not just the happy path. Tests that cannot fail, or that repeat one case many times, earn little.
- A task not attempted, or a folder with no usable files, scores 0 on every criterion.

You see only the labels A, B, C and so on. Do not try to work out which arm is which. Score each task for each arm and write the scores in the shape the phase brief gives.
