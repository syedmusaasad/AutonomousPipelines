## One-page operator guide to the browser bridge

Write a one-page operator guide to the browser bridge. Use only `docs/BROWSER-BRIDGE.md` and `pipeline/browser.py` as your sources; do not use outside knowledge and do not invent commands, flags, paths or limits. Cover what the bridge is for, how an operator starts and checks it, what it refuses to do and why, and what to do when it is down. The reader is an operator who has not read the code.

Write the guide to OUTPUT.md in the output directory named by your brief. It must be at most 400 words. Write nothing else to that directory.

## Plain-language summary of the last ten commits

Write a plain-language summary of what changed in this repository in its last ten commits (`git log -10`, and `git show` where you need detail). The reader is a non-engineer who owns the project and wants to know what is different now and why it matters. Group related changes, avoid jargon (explain any unavoidable term in a few words), and do not claim anything the commits do not show.

Write the summary to OUTPUT.md in the output directory named by your brief. It must be at most 400 words. Write nothing else to that directory.

## Rewrite the worker contract as a checklist

Rewrite the rules in `roles/CONTRACT.md` as a short checklist a worker can scan before finishing. Every rule in that file must survive in the checklist; do not drop, merge away, soften or add any rule. Keep each item to one line, in the imperative, and group items under a few short headings.

Write the checklist to OUTPUT.md in the output directory named by your brief. It must be at most 400 words. Write nothing else to that directory.
