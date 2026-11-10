# Open

## 2026-11-03T18:50:29+00:00 q_20261103T184100_e847d3 dispatch
fault: installed devpass-code run rejects the obsolete --variant and --dir flags (Unrecognized flag), so every worker launch exited 1 before work; runs q_20261103T183431_1ad345 and q_20261103T184100_e847d3 burned their attempts
mitigation: dispatch builds argv with --model provider/model#variant, attaches the brief with --file, and sets the process cwd; the fake CLI now rejects the obsolete flags and the suite asserts effort encoding and cwd
fix: fix: restore current DevPass worker CLI compatibility

## 2026-11-10T19:51:46+00:00 q_20261110T195038_cb0d5a plan-check gate path
fault: a relative GATE path resolves against the plan file's directory, but the plan author and handoff assumed WORKDIR; plans/003-claude-port waited on plans/003-claude-port/plans/003-claude-port/.approve-release (run_20261108T002619_8a315f phase 5) and nothing flagged it
mitigation: plan-check (and run) refuse a GATE whose resolved parent directory does not exist and print the resolved absolute path; README and WRITING-PLANS state the base directory; suite covers doubled, relative and absolute paths
fix: engine: plan-check refuses unresolvable gate paths; token counts never negative

## 2026-11-10T19:51:46+00:00 q_20261110T195038_cb0d5a dispatch token accounting
fault: agy usage was normalized as input_tokens - cache_read_tokens, but agy's input_tokens already excludes the cache (total_tokens == input_tokens + output_tokens; thinking is inside output_tokens), so run_20261108T002619_8a315f phase 4 recorded tokens.input=-2177837, total=-2149501 and inspect showed a negative run total
mitigation: agy input is taken as reported and the cache is not subtracted; every CLI's normalized counts are clamped at 0 and the part marked telemetry-incomplete when fields cannot be reconciled; the dispatch total is clamped too; past journals untouched
fix: engine: plan-check refuses unresolvable gate paths; token counts never negative

# Fixed
