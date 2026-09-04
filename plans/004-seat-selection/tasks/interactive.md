## interactive: Database Migration Plan
Write a `plan.md` in `$OUT/` to migrate a production database schema from v1 to v2, update 5 API endpoints (user, auth, order, product, payment), deploy the new backend service, verify the endpoints, clear the global Redis cache, update the web and mobile frontend clients to use v2, and send a final success notification to the engineering channel.

## interactive: Server Cluster Provisioning Plan
Write a `plan.md` in `$OUT/` to provision a new 10-node server cluster in AWS, configure the VPC and subnets, deploy our Docker application images to the new nodes, switch Route53 DNS records to the new load balancer, monitor traffic for 15 minutes, rollback if errors exceed 1%, and gracefully decommission the 10 old servers.

## interactive: Large Context Debugging
Read the journal excerpt in `/root/.system/runs/run_20260904T065718_4fea9b/journal.jsonl` (which has over 200 rows) and the plan files in `/root/pipeline/plans/004-seat-selection/`. Based on facts deep inside these files, figure out the specific standard-tier model chosen as the interactive fallback and output its name to `$OUT/answer.txt`.

## interactive: Status Report
Read the journal facts for the current incident. Write a status report to `$OUT/report.txt`. The very first sentence must state the final outcome. Include an explicit "waiting-on-you" list (if there is nothing to wait on, explicitly state an empty list). Do not include any preamble or extra conversational text. The entire report must be under 50 words.

## interactive: Fallback Analysis
Read `/root/.system/runs/run_20260904T065718_4fea9b/journal.jsonl` and output to `$OUT/fact.txt` the exact error message that caused the system to fall back on the interactive seat.
