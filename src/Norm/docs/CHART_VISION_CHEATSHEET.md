# Generic Chart Vision Cheat Sheet

This is the reusable perception contract for chart images. Ticker-specific anchors and coordinates belong in regression-task files, not here.

1. **Price axis is independently calibrated.** Never infer numeric price from pane top/bottom, crop bounds, separators, or unlabeled horizontal geometry. Use `CHART_AXIS_GATE.md`: visible numeric labels/tags tied to actual y-coordinates, at least two anchors (preferably three or more), numeric slope/intercept/residual checks, same-pane identity, monotonic ordering, and local-bracket sanity. If those numeric checks cannot be produced, the gate FAILS.

2. **Explicit candle readouts outrank inferred pixels.** If a crosshair/selection unambiguously ties an O/H/L/C header to a candle/time, those values are authoritative for that candle. Pixel geometry must reconcile to them; it may not override them.

3. **Separate constants from data.** Grid/pane geometry is reference/measurement structure. Candles, bodies, wicks, volume bars, moving averages, indicators, fills, and traces are variable data. Annotation/UI is a third class. Do not let a visually regular data object become a fake reference anchor.

4. **Respect pane identity.** Price, volume, indicators, and UI regions must remain distinct. Geometry from one pane cannot calibrate or extend an object in another pane unless the relationship is explicitly established.

5. **Use layered object hypotheses.** Keep separate hypotheses for candle body, wick/trace, broad fill/volume, MA/indicator line, grid/reference line, and annotation/UI. Inferred z-order means render/layer order, not physical depth.

6. **Treat occlusion as a relationship.** Broad mostly uniform rectangles are plausible fills; narrow vertically continuous traces are plausible wicks/lines. When a thin trace meets a broad fill, follow literal pixel/color continuity using local darkness, width, continuity, edge orientation, color-family agreement, overlap geometry, and pane membership together.

7. **Distinguish normal body overlap from independent occlusion.** A wick passing through its own candle body is ordinary. A trace continuing beyond its candle body into/through an independent broad fill or volume object is a different hypothesis and must not be dismissed by analogy.

8. **Attack low-confidence/high-impact regions.** Do not force a conclusion, but do not stop at low confidence either. Re-scan suspicious regions with local crops, nearest-neighbor zoom, contrast/gamma/saturation variants, neighboring-column comparison, continuity tracing, whole-pane calibration, and an original-image recheck. Promote only when evidence converges.

9. **Use multiplicative evidence for fragile traces.** Combine color-family match, local darkness, thinness, vertical continuity, edge verticality, same-pane membership, overlap-with-fill, and z-consistency so a weak required channel lowers confidence instead of being hidden by averaging.

10. **Do not average semantic vision and geometry disagreements.** If the local vision model and pixel geometry disagree, investigate until the candidate is `CONFIRMED`, `REJECTED`, or explicitly `UNRESOLVED`. Neither method wins automatically; an explicit selected-candle OHLC readout is a separate higher-priority source of truth for that candle.

11. **Audit candidates as hypotheses, never facts.** For each disputed endpoint/trace, determine whether it is a real data trace, broad-fill/body edge, compression/aliasing artifact, reference geometry, UI/annotation, or unresolved.

12. **Convert price last.** Establish object identity and endpoint geometry first. Only then apply the independently verified y-to-price transform. Report exact anchors with y-coordinates, association evidence, fitted mapping, residuals, endpoint y, converted price, uncertainty, and any explicit-readout cross-check. Any mapping that lacks numeric anchors/residuals or violates visible ordering/brackets fails.

13. **Use a redraw/reconstruction test.** The chosen object/layer decomposition should plausibly reproduce the local original pixels. If it cannot, downgrade or reject the interpretation.

14. **No hindsight targeting.** Never search for a desired low/high or force a threshold because a forecast expects it. Follow the pixels and preserve unresolved uncertainty.
