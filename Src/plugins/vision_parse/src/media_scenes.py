"""Local video scene boundaries using two-of-three independent signals.

Signals: >500ms pause (near-static video or audio silence), fade through black,
substantial change in color distribution across the boundary. Automated scene
boundaries are hypotheses; a scene break requires 2/3 cues and a hard
30-second minimum on both sides (shorter whole videos remain intact).
"""
from __future__ import annotations

from dataclasses import dataclass
from bisect import bisect_right
from io import BytesIO
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from PIL import Image, ImageChops, ImageStat

FRAME_WIDTH, FRAME_HEIGHT = 96, 54
SIGNALS = ('pause', 'fade_black', 'colorset')
MIN_SCENE_SECONDS = 30.0


@dataclass(frozen=True)
class Feature:
    time: float
    darkness: float  # normalized 0-1 luma
    palette: tuple[float, ...]  # normalized RGB histogram
    freeze_score: float  # normalized average frame delta


def _read_exact(pipe, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        part = pipe.read(n-len(out))
        if not part: break
        out.extend(part)
    return bytes(out)


def _features_from_rgb(raw: bytes, t: float, previous: Image.Image | None) -> tuple[Feature, Image.Image]:
    im=Image.frombytes('RGB', (FRAME_WIDTH, FRAME_HEIGHT),raw)
    luma=ImageStat.Stat(im.convert('L')).mean[0]/255.0
    diff=ImageStat.Stat(ImageChops.difference(im,previous)).mean if previous is not None else [255,255,255]
    freeze_score=sum(diff)/3/255.0
    hist=[0]*64
    for r,g,b in im.getdata():
        hist[(r//64)*16+(g//64)*4+(b//64)]+=1
    scale=1.0/(FRAME_WIDTH*FRAME_HEIGHT)
    return Feature(t,luma,tuple(x*scale for x in hist),freeze_score),im


def decode_features(source: Path, duration: float, fps: int = 4, *,
                    stopped: Callable | None = None, progress: Callable | None = None) -> list[Feature]:
    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg is required for scene detection')
    if fps < 3 or fps > 12:
        raise ValueError('fps must be in 3..12')
    command=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-i',str(source),
             '-an','-vf',f'fps={fps},scale={FRAME_WIDTH}:{FRAME_HEIGHT}',
             '-pix_fmt','rgb24','-f','rawvideo','pipe:1']
    proc=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    results=[]; previous=None; chunk=FRAME_WIDTH*FRAME_HEIGHT*3
    try:
        while True:
            if stopped and stopped():
                raise InterruptedError('Scene detection cancelled')
            raw=_read_exact(proc.stdout,chunk)
            if len(raw)!=chunk: break
            t=(len(results)+.5)/fps
            feat,previous=_features_from_rgb(raw,t,previous)
            results.append(feat)
            if progress and len(results)% (fps*30) ==0:
                progress(min(duration,t),duration)
        code=proc.wait(timeout=10)
        if code: raise RuntimeError(f'ffmpeg decoder failed (exit {code})')
    finally:
        if proc.poll() is None:
            proc.kill();proc.wait()
        if proc.stdout:proc.stdout.close()
    return results


def audio_pauses(source: Path, *, threshold_db: int = -38,
                 min_pause: float = .5, timeout: float = 600) -> list[tuple[float,float]]:
    """Audio silence is optional. No audio stream or ffmpeg failure means no audio cue."""
    if not shutil.which('ffmpeg'): return []
    command=['ffmpeg','-nostdin','-hide_banner','-i',str(source),'-vn',
             '-af',f'silencedetect=noise={threshold_db}dB:d={min_pause}',
             '-f','null','-']
    try:
        r=subprocess.run(command,stderr=subprocess.PIPE,stdout=subprocess.DEVNULL,
                         timeout=timeout,check=False,text=True,errors='replace')
    except (OSError,subprocess.TimeoutExpired):
        return []
    # Extract intervals sequentially to correctly handle multiple starts / ends.
    pauses=[]; start=None
    for line in r.stderr.splitlines():
        a=re.search(r'silence_start:\s*([\d.]+)',line)
        b=re.search(r'silence_end:\s*([\d.]+)',line)
        if a: start=float(a.group(1))
        if b and start is not None:
            end=float(b.group(1))
            if end-start>min_pause:pauses.append((start,end))
            start=None
    return pauses


def _palette_distance(a:tuple[float,...],b:tuple[float,...]) -> float:
    # Total variation distance [0,1] across discretized RGB colors.
    return sum(abs(x-y) for x,y in zip(a,b))*0.5


def _nearest(features:list[Feature],t:float,offset:float=0) -> Feature:
    return min(features,key=lambda f:abs(f.time-(t+offset)))


def _palette_across(features:list[Feature],t:float,interval:float=.65) -> float:
    # Do not compare against darkness during the fade itself.
    before=[f for f in features if t-1.6<=f.time<t-.25 and f.darkness>.09]
    after=[f for f in features if t+.25<f.time<=t+1.6 and f.darkness>.09]
    if not before or not after:return 0.0
    left=before[-min(3,len(before)):]
    right=after[:min(3,len(after))]
    def avg(seq): return tuple(sum(f.palette[i] for f in seq)/len(seq) for i in range(64))
    return _palette_distance(avg(left),avg(right))


def detect_boundaries(features:list[Feature],duration:float, *,
                      silence:list[tuple[float,float]] | None=None,
                      min_pause:float=.5,palette_threshold:float=.68,
                      min_scene:float=MIN_SCENE_SECONDS,merge_window:float=1.25) -> list[dict]:
    """Vote 2/3 at candidate boundaries, return distinct confident breaks.

    pause: stillness >min_pause ending near candidate OR audio silence >min_pause.
    fade_black: dark interval between bright frames.
    colorset: palette changes substantially between the bright frames around t.
    """
    if not features:return []
    events=[]
    def add(t,signal):
        if min_scene <= t <= duration-min_scene:events.append((float(t),signal))
    # stillness intervals, measured by adjacent frame deltas
    started=None
    for i,f in enumerate(features):
        frozen=f.freeze_score<.012
        if frozen and started is None and i:started=features[i-1].time
        if not frozen and started is not None:
            if f.time-started > min_pause:add(f.time,'pause')
            started=None
    # dark-through-bright intervals (fade-out/fade-in or black transition)
    black_at=None;bright_before=False
    for i,f in enumerate(features):
        if f.darkness<.085:
            if black_at is None:
                black_at=f.time
                bright_before=any(x.darkness>.09 for x in features[max(0,i-8):i])
        elif black_at is not None:
            step=(features[1].time-features[0].time) if len(features)>1 else .25
            if bright_before and f.darkness>.09 and f.time-black_at>=step:
                add(f.time,'fade_black')
            black_at=None
    for start,end in silence or []:
        if end-start>min_pause:
            add(end,'pause')
    # Palette changes: compare a stable prior/post frame, not just adjacent
    # frames, which may be black during a fade.
    for i in range(1,len(features)):
        t=features[i].time
        if t<min_scene or t>duration-min_scene:continue
        if features[i].darkness>.09:
            before=features[i-1]
            # Prune non-transition frames before the relatively expensive
            # scene palette comparison. Otherwise decoding long films is O(n²).
            if (_palette_distance(before.palette,features[i].palette)>.4
                    or before.darkness<.09):
                if _palette_across(features,t)>=palette_threshold:
                    add(t,'colorset')
    if not events:return []
    events.sort()
    groups=[]
    for t,signal in events:
        if not groups or t-groups[-1][-1][0]>merge_window:
            groups.append([])
        groups[-1].append((t,signal))
    breaks=[]
    for group in groups:
        signals={s for _,s in group}
        if len(signals)<2:continue
        ts=[t for t,s in group if s=='fade_black'] or [t for t,s in group if s=='colorset'] or [t for t,_ in group]
        t=sorted(ts)[len(ts)//2]
        # A palette cue can occur a couple of samples after black recovery.
        if breaks and t-breaks[-1]['time']<min_scene:continue
        breaks.append({'time':round(t,3),'votes':len(signals),
                       'evidence':{name:name in signals for name in SIGNALS},
                       'palette_delta':round(_palette_across(features,t),4)})
    return breaks


def _select_spaced_breaks(breaks: list[dict], duration: float,
                          min_scene: float) -> list[dict]:
    """Select the greatest number of qualifying cuts, preferring stronger evidence.

    Every interval (including the last) must be at least min_scene. For
    conflicting cuts, a 3/3 vote wins over 2/3; palette contrast breaks ties.
    This dynamic-programming selection avoids an early weak cut suppressing
    a stronger cut or preventing a second valid scene later in the video.
    """
    eligible = []
    for b in breaks:
        t = float(b['time'])
        if (math.isfinite(t) and min_scene <= t <= duration - min_scene
                and int(b.get('votes', 0)) >= 2):
            eligible.append((t, b))
    eligible.sort(key=lambda x: (x[0], -int(x[1]['votes'])))
    if not eligible:
        return []

    times = [t for t, _ in eligible]
    # Compare (count, evidence score): preserve more valid scenes first.
    dp = [(0, 0.0)]
    choice: list[tuple[bool, int]] = [(False, 0)]
    for i, (t, item) in enumerate(eligible, 1):
        prev = bisect_right(times, t - min_scene + 1e-8, 0, i - 1)
        evidence = float(item.get('palette_delta') or 0.0)
        quality = int(item.get('votes', 0)) + max(0.0, min(1.0, evidence))
        take = (dp[prev][0] + 1, dp[prev][1] + quality)
        skip = dp[i-1]
        if take > skip:
            dp.append(take)
            choice.append((True, prev))
        else:
            dp.append(skip)
            choice.append((False, i-1))

    selected = []
    i = len(eligible)
    while i:
        take, prev = choice[i]
        if take:
            selected.append(eligible[i-1][1])
        i = prev
    return list(reversed(selected))


def scenes_from_breaks(breaks: list[dict], duration: float, *,
                       min_scene: float = MIN_SCENE_SECONDS) -> list[dict]:
    """Build scene intervals; no automatic scene shorter than min_scene.

    A video shorter than min_scene stays one whole-video segment. Accepted
    scene boundaries require two of three independent detector signals.
    """
    duration = float(duration)
    min_scene = float(min_scene)
    if not math.isfinite(duration) or duration < 0:
        raise ValueError('duration must be finite and non-negative')
    if not math.isfinite(min_scene) or min_scene <= 0:
        raise ValueError('min_scene must be positive and finite')
    chosen = _select_spaced_breaks(breaks, duration, min_scene)
    scenes = []
    start = 0.0
    for item in chosen:
        end = float(item['time'])
        scenes.append({'start_sec': start, 'end_sec': end, 'break_evidence': item})
        start = end
    scenes.append({'start_sec': start, 'end_sec': duration,
                   'break_evidence': {'evidence': {}, 'votes': 0}})
    return scenes

def detect_video_scenes(source:Path,duration:float,*,fps:int=4,
                        stopped:Callable|None=None,progress:Callable|None=None,
                        return_audit:bool=False,
                        previous_breaks:list[float]|None=None,
                        min_scene:float=MIN_SCENE_SECONDS):
    features=decode_features(source,duration,fps,stopped=stopped,progress=progress)
    if stopped and stopped():raise InterruptedError('Cancelled')
    silence=audio_pauses(source)
    from .media_two_pass import detect_two_pass
    report=detect_two_pass(features,duration,silence=silence,
                           previous_breaks=previous_breaks,min_scene=min_scene)
    return report if return_audit else report['scenes']
