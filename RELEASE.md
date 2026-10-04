# Release

## Norm 0.53.14 / Installer 1.6.6

- Added N1/N2 checkpoint 1.
- Ordinary user input passes through N1 to N2 unchanged; N2 user-facing output passes through N1 unchanged.
- N2 remains the primary reasoning/worker agent.
- Model-requested information tools are gated by N1.
- N1 can satisfy repeated information needs from the live validation pool instead of invoking the tool again.
- Fresh tool results are forwarded raw to N2 while N1 records validation evidence out of band.
- N1 can halt a judged repeated reasoning/tool turn before its proposed tools execute and tell N2 to change approach using existing evidence.
- Successful mutations invalidate matching cached evidence so N1 cannot return obviously stale pre-write observations.
- N2 no longer owns the verification preflight/check-in protocol.
- Both roles default to the existing configured `norm` model/endpoint until separately configured.
