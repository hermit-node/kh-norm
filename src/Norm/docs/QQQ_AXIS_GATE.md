# QQQ Axis Calibration Hard Gate

This gate is mandatory before any wick endpoint can be converted to price.

1. FORBIDDEN anchors: pane top, pane bottom, crop boundary, panel separator, or any unlabeled horizontal line inferred only from geometry.
2. ALLOWED anchors: explicit visible numeric price labels/tags tied to their actual horizontal y positions in the original image.
3. Required primary anchors: Overnight 709.18 at approximately y=298 and current-price 704.69 at approximately y=514.5; refine from original pixels.
4. From those two labeled anchors alone, implied slope is about 0.02074 price units per pixel downward.
5. Therefore 700.00 should be near y=741, not y=887. Any mapping that places 700 near y=887 is automatically INVALID.
6. Locate visible 710/708/706/704/702/700 labels and their y positions independently; use them only if the label-to-grid association is visually supported.
7. Fit the mapping from labeled anchors only and report residuals for every anchor.
8. If any anchor residual is materially inconsistent, stop price conversion and repair the mapping.
9. Do not reuse any axis transform from prior tasks.
10. Only after this gate PASSES may wick endpoints be converted to QQQ prices.
