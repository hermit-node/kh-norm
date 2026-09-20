# STATUS: COMPLETED REGRESSION / HISTORICAL TASK

The corrective QQQ rerun completed successfully on 2026-09-17 and passed structured final verification. Keep the instructions below as a reproducible regression record; do not treat them as pending work.

Rerun the QQQ chart perception validation using the durable cheat sheet at C:\Users\KHzz\Documents\Norm\docs\CHART_VISION_CHEATSHEET.md and the original image C:\Users\KHzz\Documents\Norm\images\norm images\QQQ_followup_hidden_wick_test_20260916.jpg.

This is a corrective audit of prior task chat-ed0880fd-fdcf-46b9-b6ac-1b9ca7affbf6. Do NOT preserve its ~701.4 conclusion. The prior run made a known axis-calibration error by anchoring pane bottom y=887 to price 700.00. Treat that conclusion as invalid and re-derive everything from the original pixels.

Required sequence:
1. Read CHART_VISION_CHEATSHEET.md first and explicitly state the rules you will enforce.
2. Re-run analyze_image on the original with the current live analyzer. Preserve its artifacts.
3. Calibrate y→price ONLY from visible labeled price references. Use the visible 709.18 Overnight line and 704.69 current-price line as sanity anchors, refining their exact y coordinates from the image; then verify the fitted mapping against as many visible 710/708/706/704/702/700 labels/grid positions as possible. If residuals are inconsistent, stop and repair the mapping before continuing.
4. Audit the deep red candidates around x≈510–552, y≈833–840 as hypotheses, not facts. For each, inspect original pixels, focus crops, 4x/8x zooms, contrast/gamma/saturation variants, multiplicative trace-strength evidence, neighboring-column behavior, and continuity relative to broad-fill geometry.
5. Distinguish normal wick passing through its own candle body from a thin trace continuing beyond the candle body into/through an independent volume-bar/fill object. Do not use 'normal wick-over-body rendering' as a blanket rejection.
6. If semantic vision and pixel geometry disagree, do not average them. Continue local examination and classify each disputed candidate CONFIRMED, REJECTED, or UNRESOLVED with reasons.
7. Only after endpoint geometry is finalized, convert y endpoints to QQQ prices with the verified axis transform.
8. Perform a local redraw/reconstruction sanity check: explain whether the chosen object/layer decomposition could reproduce the original pixels around the disputed low.
9. Return a concise conclusion plus structured JSON including: axis_anchors, axis_mapping, axis_residuals, audited_candidates, confirmed_low_endpoint_y, estimated_low_price, uncertainty, geometry_vs_vision_disagreements, redraw_check, prior_run_failure_diagnosis.

Do not make a market forecast. Do not search for a desired price. Follow the pixels and report unresolved uncertainty if necessary.
