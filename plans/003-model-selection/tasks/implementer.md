## Task 1: Fix registry loading
Deliverable: `<out>/fix-registry.patch`
The current `pipeline/trial.py` fails to handle JSON parsing errors gracefully if `roles/registry.json` is corrupted. Write a patch that adds a `try/except json.JSONDecodeError` block when loading the registry and falls back to an empty dictionary or logs a warning. Scorer checks the patch applies and adds the exception handling.

## Task 2: Implement trial logging
Deliverable: `<out>/trial-logging.patch`
Trials currently don't write their output to a log file. Modify `pipeline/trial.py` to write stdout/stderr of the trial process to `lane_dir / "trial.log"`. Write a patch file. Scorer checks that output redirection to `trial.log` is added to the subprocess call.

## Task 3: Setup CI test for registry drift
Deliverable: `<out>/ci-registry-drift.sh`
Write a bash script that runs `pipeline render-agents` and then checks if there are any uncommitted changes in the agent files (using `git diff --exit-code`). The script should exit 1 if changes are found. Scorer checks for `git diff --exit-code` and correct exit logic.

## Task 4: Fix blind scoring leak
Deliverable: `<out>/fix-blind-scoring.patch`
Phase 1 of model selection requires the scorer not to see which arm is incumbent. Modify `pipeline/trial.py` so that model names in the `lane.json` output are hashed or replaced with an alias (e.g., "arm-1", "arm-2") before being passed to the scorer. Scorer verifies the patch removes direct model name references.

## Task 5: Add benchmark parsing
Deliverable: `<out>/parse-bench.py`
Write a python script that reads a simulated vendor benchmark JSON file and extracts the "pass_at_1" metric for a given model. Scorer checks that the script handles missing keys and returns the correct float value.
