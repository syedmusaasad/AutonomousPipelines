# Open

## 2026-11-03T18:50:29+00:00 q_20261103T184100_e847d3 dispatch
fault: installed devpass-code run rejects the obsolete --variant and --dir flags (Unrecognized flag), so every worker launch exited 1 before work; runs q_20261103T183431_1ad345 and q_20261103T184100_e847d3 burned their attempts
mitigation: dispatch builds argv with --model provider/model#variant, attaches the brief with --file, and sets the process cwd; the fake CLI now rejects the obsolete flags and the suite asserts effort encoding and cwd
fix: fix: restore current DevPass worker CLI compatibility

# Fixed
