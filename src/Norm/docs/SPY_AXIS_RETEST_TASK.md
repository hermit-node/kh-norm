# STATUS: PENDING REGRESSION

This remains the focused chart-axis regression. The prior 16-path counterfactual generation does not need to be regenerated merely to run this test.

# SPY Axis-Gate Regression Test

Audit only the price perception of this image:
C:\Users\KHzz\Documents\Norm\images\norm images\SPY_recast_20260917_0730.jpg

Before any y-to-price conversion, read and enforce:
C:\Users\KHzz\Documents\Norm\docs\CHART_AXIS_GATE.md
C:\Users\KHzz\Documents\Norm\docs\CHART_VISION_CHEATSHEET.md

Do not reuse the prior SPY mapping or its prior low estimate. Build a fresh mapping only from visible labeled price anchors tied to their actual gridline/tag y positions.

First identify the true lowest PRICE-PANE wick endpoint geometrically. Keep volume and indicator panes separate. Then apply the generic axis gate.

Explicitly test the monotonic/local-bracket invariant around the visible 750.00 label: if the confirmed wick endpoint is physically below the 750.00 gridline, its converted price must be below 750.00. A transform violating that invariant must FAIL and be repaired before continuing.

Return: labeled anchors with y coordinates and association evidence; fitted mapping and residuals; 750.00 gridline y; lowest confirmed price-wick endpoint y; whether endpoint is above/below the 750 line; converted low with uncertainty; axis gate PASS/FAIL; and a short diagnosis of why the previous SPY read reported ~752-753 if this audit differs.

This is a perception regression test, not a request to force a sub-750 result. Follow the pixels and gate rules.
