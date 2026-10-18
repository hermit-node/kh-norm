"""Two-pass, global scene-boundary adjudication.

Pass 1: collect ALL detector hypotheses (including weak ones).
Pass 2: re-score each hypothesis with context from before/after the candidate
and the full proposal timeline, then perform a global optimal-spacing selection.
Never commit sub-30-second scenes. Every selected transition needs two
independent evidence families: temporal, transition, or appearance.
"""
from __future__ import annotations
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
import math
from .media_scenes import Feature, _palette_distance, _select_spaced_breaks, MIN_SCENE_SECONDS

FAMILIES = {
    'pause': 'temporal', 'audio_pause': 'temporal',
    'fade_black': 'transition',
    'colorset': 'appearance', 'structural_cut': 'appearance',
}


def _appearance_before_after(features: list[Feature], times: list[float], t: float, window: float = 2.0):
    """Window-local lookups; never scan the entire frame list for each cut."""
    left = features[bisect_left(times, t-window):bisect_left(times, t-.25)]
    right = features[bisect_right(times, t+.25):bisect_right(times, t+window)]
    left = [f for f in left if f.darkness > .09][-8:]
    right = [f for f in right if f.darkness > .09][:8]
    if not left or not right:
        return 0.0
    n = min(len(left[0].palette), len(right[0].palette))
    l = tuple(sum(f.palette[i] for f in left)/len(left) for i in range(n))
    r = tuple(sum(f.palette[i] for f in right)/len(right) for i in range(n))
    return _palette_distance(l,r)


def propose_boundaries(features: list[Feature], duration: float, *,
                       silence: list[tuple[float,float]] | None = None,
                       min_pause: float = .5, palette_threshold: float = .68,
                       merge_window: float = 1.25) -> list[dict]:
    """First pass: collect weak + strong candidates WITHOUT scene-length pruning."""
    if not features or duration <= 0:
        return []
    times = [f.time for f in features]
    events: list[tuple[float,str]] = []
    def add(t, signal):
        if 0 < t < duration and math.isfinite(t):
            events.append((float(t), signal))
    frozen_from = None
    for i, f in enumerate(features):
        is_frozen = f.freeze_score < .012
        if is_frozen and frozen_from is None and i:
            frozen_from = features[i-1].time
        if not is_frozen and frozen_from is not None:
            if f.time-frozen_from >= min_pause:
                add(f.time, 'pause')
            frozen_from = None
    black_start = None
    saw_bright = False
    for i, f in enumerate(features):
        if f.darkness < .085:
            if black_start is None:
                black_start = f.time
                saw_bright = any(x.darkness>.09 for x in features[max(0,i-8):i])
        elif black_start is not None:
            if saw_bright:
                add(f.time, 'fade_black')
            black_start = None
    for start,end in silence or []:
        if end-start >= min_pause:
            add(end, 'audio_pause')
    for i in range(1,len(features)):
        now, prev = features[i],features[i-1]
        # Structural change: instantaneous high pixel change, an appearance cue,
        # NOT an extra independent vote when a palette cue also exists.
        if now.freeze_score > .32 and prev.darkness > .09 and now.darkness > .09:
            add(now.time, 'structural_cut')
        raw = _palette_distance(prev.palette, now.palette)
        if raw > .4 or prev.darkness < .09:
            if _appearance_before_after(features,times,now.time) >= palette_threshold:
                add(now.time, 'colorset')
    events.sort()
    grouped: list[list[tuple[float,str]]] = []
    for t,signal in events:
        if not grouped or t-grouped[-1][-1][0] > merge_window:
            grouped.append([])
        grouped[-1].append((t,signal))
    proposed=[]
    for group in grouped:
        types={name for _,name in group}
        time_list=[t for t,s in group if s == 'fade_black'] or [t for t,s in group if s=='colorset'] or [t for t,_ in group]
        t=sorted(time_list)[len(time_list)//2]
        proposed.append({'time':round(t,3),'raw_signals':sorted(types),
                         'pass':1,'proposal_count':len(group)})
    return proposed


def adjudicate_candidates(features: list[Feature], proposals: list[dict],
                          duration: float, *, min_scene: float = MIN_SCENE_SECONDS,
                          min_palette: float = .68) -> dict:
    """Second pass: reconsider every first-pass candidate with full-video context.

    No intermediate acceptance/spacing filter is applied until ALL candidates
    are rescored. The global dynamic program decides conflicting boundaries.
    """
    times=[f.time for f in features]
    qualifying=[]
    audit=[]
    for proposal in sorted(proposals,key=lambda p:float(p['time'])):
        t=float(proposal['time'])
        signals=set(proposal.get('raw_signals',[]))
        palette=_appearance_before_after(features,times,t)
        if palette >= min_palette:
            signals.add('colorset')
        elif 'colorset' in signals and palette < min_palette*.6:
            # An isolated sampled palette spike isn't enough by itself.
            signals.remove('colorset')
        families={FAMILIES[s] for s in signals if s in FAMILIES}
        valid=len(families)>=2
        item={'time':round(t,3), 'votes':len(families),
              'evidence':{k:k in signals for k in sorted(FAMILIES)},
              'palette_delta':round(palette,4),
              'evidence_families':sorted(families),
              'pass':2, 'accepted':False,
              'reason':'eligible' if valid else 'insufficient_independent_signals'}
        if valid:
            qualifying.append(item)
        audit.append(item)
    chosen=_select_spaced_breaks(qualifying,duration,min_scene)
    selected={item['time'] for item in chosen}
    for item in audit:
        if item['time'] in selected:
            item['accepted']=True
            item['reason']='selected_global'
        elif item['reason']=='eligible':
            item['reason']='global_spacing_or_edge'
    # build from globally-selected breaks; never reintroduce filtered boundaries
    scenes=[]
    prior=0.0
    for item in chosen:
        at=float(item['time'])
        scenes.append({'start_sec':prior,'end_sec':at,'break_evidence':item})
        prior=at
    scenes.append({'start_sec':prior,'end_sec':float(duration),
                   'break_evidence':{'votes':0,'evidence':{},'pass':2}})
    return {'scenes':scenes, 'proposals':len(proposals),
            'candidates':audit, 'accepted_breaks':len(chosen),
            'algorithm':'global-two-pass-v1','min_scene_seconds':min_scene}


def detect_two_pass(features: list[Feature], duration: float, *,
                    silence: list[tuple[float,float]] | None = None,
                    previous_breaks: list[float] | None = None,
                    min_scene: float = MIN_SCENE_SECONDS) -> dict:
    proposals=propose_boundaries(features,duration,silence=silence)
    # Explicit second-look candidates: old recorded cuts are not assumed correct,
    # but they must not vanish from consideration before full-context review.
    for old in previous_breaks or []:
        t=float(old)
        if not math.isfinite(t) or not 0<t<duration:continue
        if not any(abs(float(p['time'])-t)<=1.25 for p in proposals):
            proposals.append({'time':round(t,3),'raw_signals':[],
                              'pass':1,'proposal_count':0,'previously_recorded':True})
    report=adjudicate_candidates(features,proposals,duration,min_scene=min_scene)
    report['prior_boundaries_rechecked']=len(previous_breaks or [])
    return report
