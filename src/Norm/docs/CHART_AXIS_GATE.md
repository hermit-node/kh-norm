# Generic Chart Price-Axis Hard Gate

This gate is mandatory before any chart pixel endpoint is converted to a price.

1. FORBIDDEN anchors: pane top/bottom, crop edge, panel separator, unlabeled gridlines, inferred chart bounds, or geometry-only assumptions.
2. ALLOWED anchors: explicit visible numeric price labels or price tags that can be tied to an actual horizontal y-coordinate in the original image.
3. **Explicit selected-candle OHLC/readouts outrank inferred geometry.** If a crosshair/selection unambiguously ties an O/H/L/C header to a candle/time, those values are authoritative facts for that candle. Geometry must reconcile to them; it may not override them.
4. Use at least two independent labeled anchors; prefer three or more when available. Never reuse a transform from another image or prior task.
5. Explicitly record each axis anchor as `{visible_price, y_coordinate, association_evidence}`. `top third`, `bottom third`, ordering alone, or inferred uniform spacing are NOT y-coordinates and do not qualify.
6. Fit y-to-price only from allowed anchors. Report numeric slope, intercept, and residual for every labeled anchor used or checked.
7. **NO PARTIAL PASS:** if two exact price↔y pairs, numeric slope/intercept, and residual checks cannot be produced, the axis gate is FAIL. A qualitative bracket may still be reported, but no numeric wick/endpoint price may be asserted.
8. MONOTONIC ORDER GATE: on a normal price chart, larger y means lower price. If object A is visibly below a labeled price line P, its converted price must be below P. If not, the mapping is INVALID.
9. LOCAL BRACKET GATE: whenever an endpoint lies between two visible labeled price lines, its converted price must lie numerically between those labels. If it lies below the lower labeled line, its price must be lower than that label.
10. SPACING SANITY: adjacent labeled gridlines should imply materially consistent pixels-per-price-unit. If spacing differs substantially, investigate perspective/crop/pane mistakes before conversion.
11. PANE IDENTITY: verify every anchor and endpoint belongs to the same price pane. Volume/indicator panes must never supply price anchors or price endpoints.
12. If an explicit OHLC/readout conflicts with a geometric wick interpretation for the same selected candle, STOP and repair object identity / occlusion / anchor association. The readout is not averaged with the inferred geometry.
13. If labels, grid association, pane identity, slope sign, spacing, or residuals conflict, STOP price conversion and repair the mapping. Do not average incompatible transforms.
14. Endpoint geometry is determined before price conversion. Pixel/object classification and axis calibration are separate checks.
15. Report: anchors, mapping, residuals, relevant bracket/ordering checks, endpoint y, converted price, uncertainty, explicit-readout cross-checks, and PASS/FAIL.
16. NO HINDSIGHT TARGETING: the gate validates geometry and coordinate consistency, not a desired market result.
17. Only after this gate PASSES may any wick/body/line endpoint be converted to price.
