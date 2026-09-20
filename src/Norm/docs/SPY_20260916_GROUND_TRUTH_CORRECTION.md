# SPY Sep 16 Ground-Truth Correction

## Authoritative selected-candle evidence
A later user screenshot placed the TradingView crosshair on **Wed Sep 16, 2026 15:15**. The header explicitly displayed:

- O 751.33
- H 752.12
- **L 749.60**
- C 751.24
- change -0.08 (-0.01%)

Therefore the selected 15:15 candle's low is **749.60**. This is explicit application data tied to the selected candle/time and outranks any inferred wick-to-axis conversion.

The same later screenshot showed the live/right-edge SPY marker near **762.44 around 12:10 ET on Sep 17**. Do not confuse that current-price marker with the crosshair-selected Sep 16 candle OHLC header.

## Prior Norm failure
Task `chat-1b8b9692-a5ef-4c61-921e-4fe2b2ce3155` incorrectly concluded the Sep 16 low was above 750 (~751-752.5). Its step-01 must not be reused as factual chart interpretation.

The step incorrectly declared `AXIS GATE: PASS` even though it did not establish at least two exact `{price, y_coordinate}` anchors, numeric slope/intercept, and residuals. It relied on semantic positions such as `top third` / `bottom third` and an inferred 2.50 spacing. Under the corrected `CHART_AXIS_GATE.md`, that is a FAIL and numeric wick conversion is forbidden.

## Standing lesson
When a crosshair/selection supplies an explicit candle OHLC readout, use that as authoritative for the selected candle. Geometry is then a consistency/debugging problem, not a competing estimate. If geometry conflicts, investigate object identity, occlusion, anchor association, and axis calibration; never average away the explicit readout.
