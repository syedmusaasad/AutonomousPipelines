# GC backlog sweep

Run: `run_20260906T163914_244032`  
Buffer: 72 hours  
Dry-run: 2026-09-06

## Result

The dry-run found **0 eligible runs**, **61 kept runs**, and an estimated **0
bytes recoverable**. The sweep therefore deleted 0 paths and freed 0 bytes.
The current conversation lineage was kept. Recent TUI and Roblox work remains
untouched. No `opencode.db` or `tool-output` data was swept.

Bytes accounting (du-equivalent GC estimate):

| Measurement | Bytes |
|---|---:|
| Before sweep | 0 eligible artifact bytes |
| After sweep | 0 eligible artifact bytes |
| Freed | 0 bytes |

The GC sweep wrote its manifest to
`~/.system/logs/gc-manifest-20260906T211516Z.jsonl`.

## Dry-run table

```text
run                          kind   closed_at       age_h eligible        bytes
q_20260904T045352_52ded8     quick  1788497647.753     64.4 keep                -
q_20260904T050527_ddab72     quick  1788498337.599     64.2 keep                -
q_20260904T050633_40ff10     quick  1788498468.826     64.1 keep                -
q_20260904T051738_0cc3a2     quick  1788499068.916     64.0 keep                -
q_20260904T051752_75c59e     quick  1788499082.187     64.0 keep                -
q_20260904T052125_d59b15     quick  1788499386.617     63.9 keep                -
q_20260904T053926_376708     quick  1788500441.391     63.6 keep                -
q_20260904T061252_e8acb3     quick  1788502467.508     63.0 keep                -
q_20260904T062214_636272     quick  1788502949.583     62.9 keep                -
q_20260904T062239_6aea69     quick  1788502986.375     62.9 keep                -
q_20260904T062341_e4c99b     quick  1788503044.651     62.9 keep                -
q_20260904T062448_b398d8     quick  1788503093.485     62.8 keep                -
q_20260904T063740_94d62e     quick  1788503865.487     62.6 keep                -
q_20260904T064242_587889     quick  1788504187.905     62.5 keep                -
q_20260904T072800_f77e4b     quick  1788506885.537     61.8 keep                -
q_20260904T072818_f41cae     quick  1788506908.431     61.8 keep                -
q_20260904T072847_c8d5f7     quick  1788506937.488     61.8 keep                -
q_20260904T200631_bf021f     quick  1788552396.2     49.1 keep                -
q_20260904T200839_82fae7     quick  1788552524.343     49.1 keep                -
q_20260904T201201_bbf7e8     quick  1788552731.417     49.1 keep                -
q_20260904T233807_6519f7     quick  1788565122.552     45.6 keep                -
q_20260904T235628_4a2d55     quick  1788566233.848     45.3 keep                -
q_20260905T000921_8f6edf     quick  1788567191.762     45.0 keep                -
q_20260905T001550_854481     quick  1788567750.234     44.9 keep                -
q_20260905T012804_2839fb     quick  1788571700.096     43.8 keep                -
q_20260905T013154_79d598     quick  1788571964.697     43.7 keep                -
q_20260905T013939_97e593     quick  1788572509.571     43.6 keep                -
q_20260905T014807_c5c711     quick  1788573112.858     43.4 keep                -
q_20260905T020815_1cd764     quick  1788574151.217     43.1 keep                -
q_20260905T181154_cdf3d7     quick  1788632079.365     27.0 keep                -
q_20260905T181453_a06c59     quick  1788632139.012     27.0 keep                -
q_20260905T182004_f914e0     quick  1788632589.587     26.9 keep                -
q_20260905T182427_a11769     quick  1788632827.445     26.8 keep                -
q_20260905T182825_92b058     quick  1788632931.053     26.8 keep                -
q_20260905T192008_9e5030     quick  1788636098.905     25.9 keep                -
q_20260906T160515_dcf9c4     quick  1788710880.239      5.1 keep                -
run_20260904T045638_4f36da   plan   1788497893.556     64.3 keep                -
run_20260904T045949_944c1d   plan   1788498169.861     64.2 keep                -
run_20260904T051515_ac738d   plan   1788500536.434     63.5 keep                -
run_20260904T053045_ab8d66   plan   1788500195.996     63.6 keep                -
run_20260904T054447_438b65   plan   1788646338.963     23.0 keep                -
run_20260904T060316_431609   plan   1788504916.21      62.3 keep                -
run_20260904T065718_4fea9b   plan   1788565175.203     45.6 keep                -
run_20260904T234020_e16dc5   plan   1788566203.725     45.3 keep                -
run_20260905T001657_8c3aaa   plan   1788567492.727     45.0 keep                -
run_20260905T001916_651a08   plan   1788567616.717     44.9 keep                -
run_20260905T001926_cbe4f7   plan   1788567632.137     44.9 keep                -
run_20260905T002109_0c0fd0   plan   1788567690.021     44.9 keep                -
run_20260905T010559_3a82bd   plan   1788571932.363     43.7 keep                -
run_20260905T011312_67ec35   plan   1788571888.351     43.7 keep                -
run_20260905T013230_eb40aa   plan   1788573541.145     43.3 keep                -
run_20260905T013311_7b35cc   plan   1788573841.944     43.2 keep                -
run_20260905T020133_5c054c   plan   1788574298.394     43.1 keep                -
run_20260905T183340_017ebe   plan   1788633825.358     26.5 keep                -
run_20260905T190737_062aa0   plan   1788635267.001     26.1 keep                -
run_20260905T190737_62cca8   plan   1788635266.907     26.1 keep                -
run_20260905T190800_f98b05   plan   1788635399.917     26.1 keep                -
run_20260905T190801_b9edbb   plan   1788635400.096     26.1 keep                -
run_20260905T193227_2ec9f8   plan   1788677039.335     14.5 keep                -
run_20260905T194359_d7b2ef   plan   1788673888.598     15.4 keep                -
run_20260906T163914_244032   plan   1788713734.296      4.3 keep                -
```
