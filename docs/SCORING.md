# How the performance-health score is calculated

The score is a **transparent summary of measured values**. It is not an industry benchmark
and it is not comparable between different PCs. Every number can be traced back to a
formula below; hover a category bar on the dashboard to see its inputs.

Source: [`boostai/core/performance_score.py`](../boostai/core/performance_score.py)
(unit tests: [`tests/test_scoring.py`](../tests/test_scoring.py)).

## Method

Each category is a piecewise-linear function of one or more measurements (points between
the listed values are linearly interpolated and values outside are clamped). Categories
that cannot be measured on a machine (for example when disk counters are unavailable) are
**left out**, not scored as zero. The overall score is the weighted average of the
categories that were measured.

| Category        | Weight | Inputs |
|-----------------|-------:|--------|
| Memory          | 0.25 | RAM in use %, commit charge %, hard page faults/s, possible memory leaks |
| CPU             | 0.20 | Average CPU % over the last 2 minutes, CPU % while the user is idle, interrupt + DPC time |
| Responsiveness  | 0.20 | Disk active %, average disk latency, heavy paging |
| Disk capacity   | 0.15 | Free space on the system drive |
| Startup         | 0.10 | Enabled startup items by category |
| Background load | 0.10 | Process count relative to this PC's baseline (or an absolute scale until a baseline exists) |

## Curves

| Input | Points `(value -> score)` |
|-------|---------------------------|
| RAM in use % | 0->100, 60->100, 75->85, 85->65, 92->40, 97->15, 100->0 |
| Commit charge % | 0->100, 80->100, 90->70, 95->40, 100->0 (Memory = min(RAM, commit)) |
| Hard faults/s | > 1000: -10, > 3000: -20 |
| Possible leaks | -10 per MEDIUM/HIGH-confidence leak |
| Average CPU % | 0->100, 30->100, 60->80, 85->50, 100->20 |
| Idle CPU % (penalty) | 10->0, 20->10, 30->20, 60->30 |
| Interrupt + DPC % | >= 5: -10, >= 10: -25 |
| Disk active % | 0->100, 50->100, 80->70, 95->40, 100->20 |
| Disk latency ms | 0->100, 20->100, 50->70, 100->40, 200->15 (Responsiveness = min of the two, -15 if paging > 1500/s) |
| System drive free % | 0->0, 5->20, 10->50, 15->75, 20->90, 25->100; capped at 40 below 10 GB free |
| Startup | 100 - 10 x high-impact - 4 x optional - 2 x unknown |
| Processes / baseline median | 1.1->100, 1.5->60, 2.0->30, 3.0->10 |
| Processes (no baseline yet) | 250->100, 350->85, 450->60, 600->30 |

## Labels

| Score | Label |
|------:|-------|
| >= 85 | Good |
| 70-84 | Fair |
| 50-69 | Needs attention |
| < 50  | Poor |
