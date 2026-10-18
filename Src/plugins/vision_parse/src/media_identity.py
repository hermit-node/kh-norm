"""Generic, read-only, whole-corpus face-profile reconciliation.

Person names and UUID assignments belong to the caller. No database writes.
Manual identity constraints apply to entire union-find components, not just pairs.
"""
from __future__ import annotations

import math
import unicodedata
from collections import defaultdict
from typing import Any


def _alias_set(profile: dict) -> frozenset[str]:
    values = profile.get('aliases') or []
    if isinstance(values, str):
        values = [values]
    # A manually edited display name counts as a label unless it is a default.
    name = str(profile.get('name') or '').strip()
    if name and (bool(profile.get('manually_named')) or not (name.lower().startswith('person ') and name[7:].isdigit())):
        values = [*values, name]
    return frozenset(
        unicodedata.normalize('NFKC', str(x)).strip().casefold()
        for x in values if str(x).strip()
    )


def _normalize(v: list[float]) -> list[float] | None:
    if not isinstance(v, (list, tuple)) or not v or len(v) > 4096:
        return None
    try:
        floats = [float(x) for x in v]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(x) for x in floats):
        return None
    norm = math.sqrt(sum(x*x for x in floats))
    return [x/norm for x in floats] if norm > 1e-10 else None


def reconcile_profiles(profiles: list[dict], appearances: list[dict], *,
                       auto_threshold: float = .60, review_threshold: float = .39,
                       max_proposals: int = 2000) -> dict[str, Any]:
    """Compare saved face vectors; propose only, never apply identity changes.

    Input profiles have person_uuid, optional aliases/name/manual_lock; occurrences
    have face_uuid, person_uuid and embedding. For a profile, the unit-normalized
    centroid represents all its observations. 0.60 is intentionally conservative
    and is NOT a calibrated probability. Caller can tune against real media.
    """
    if not (0 < review_threshold <= auto_threshold <= 1):
        raise ValueError('thresholds must satisfy 0 < review <= auto <= 1')
    if len(profiles) > 50_000 or len(appearances) > 2_000_000:
        raise ValueError('Corpus exceeds single batch safety limits; partition first')
    if not (1 <= max_proposals <= 100_000):
        raise ValueError('max_proposals outside supported range')
    by_id: dict[str, dict] = {}
    for p in profiles:
        pid = str(p.get('person_uuid') or '').strip()
        if not pid or pid in by_id:
            raise ValueError('Each profile requires a unique person_uuid')
        by_id[pid] = dict(p)
    vectors: dict[str, list[list[float]]] = defaultdict(list)
    models: dict[str, str] = {}
    mixed_models: set[str] = set()
    seen_faces: set[str] = set()
    for a in appearances:
        fid = str(a.get('face_uuid') or '').strip()
        if not fid or fid in seen_faces:
            raise ValueError('Each appearance requires a unique face_uuid')
        seen_faces.add(fid)
        pid = str(a.get('person_uuid') or '')
        if pid not in by_id:
            continue
        vec = _normalize(a.get('embedding') or [])
        if vec:
            model = str(a.get('embedding_model') or 'opencv_sface_2021dec').strip()
            if pid in models and models[pid] != model:
                mixed_models.add(pid)
            else:
                models[pid] = model
                vectors[pid].append(vec)
    centers: dict[str, list[float]] = {}
    for pid, v in vectors.items():
        if pid in mixed_models:
            continue
        dim = len(v[0]); same = [x for x in v if len(x) == dim]
        centroid = _normalize([sum(x[i] for x in same) / len(same) for i in range(dim)])
        if centroid:
            centers[pid] = centroid
    ids = sorted(centers)
    parent = {p: p for p in by_id}
    labels = {p: _alias_set(row) for p, row in by_id.items()}
    locks = {p: bool(row.get('manual_lock') or row.get('never_merge')) for p, row in by_id.items()}
    def root(p: str) -> str:
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p
    # Component-level aliases: an unlabeled intermediary can never bridge
    # separately labeled people with conflicting alias sets.
    comp_labels = {p: set([labels[p]]) if labels[p] else set() for p in by_id}
    comp_locked = dict(locks)
    possible = []
    # Stream similarities in blocks to avoid allocating an N x N matrix.
    # Optional NumPy vectorization, never required for the permission rules.
    try:
        import numpy as np
        by_dim: dict[int, list[str]] = defaultdict(list)
        for pid in ids:
            by_dim[(len(centers[pid]), models[pid])].append(pid)
        for dim, members in by_dim.items():
            matrix = np.asarray([centers[pid] for pid in members], dtype=np.float32)
            for start in range(0, len(members), 256):
                scores = matrix[start:start+256] @ matrix.T
                for offset, row in enumerate(scores):
                    i = start + offset
                    eligible = np.flatnonzero(row[i+1:] >= review_threshold) + i + 1
                    if len(eligible) > 12:
                        # Limit candidate graph degree; never materialize dense N²
                        # proposals when thousands of photos depict the same person.
                        chosen = np.argpartition(row[eligible], -12)[-12:]
                        eligible = eligible[chosen]
                    for j in eligible:
                        possible.append((float(row[j]), members[i], members[int(j)]))
    except ImportError:
        for i, x in enumerate(ids):
            # Keep only a small nearest-neighbor graph, even without NumPy.
            neighbors = []
            for y in ids[i+1:]:
                if len(centers[x]) != len(centers[y]) or models[x] != models[y]:
                    continue
                score = sum(a*b for a, b in zip(centers[x], centers[y]))
                if score >= review_threshold:
                    neighbors.append((score, x, y))
                    if len(neighbors) > 64:
                        neighbors.sort(reverse=True)
                        del neighbors[12:]
            possible.extend(sorted(neighbors,reverse=True)[:12])
    possible.sort(key=lambda x: (-x[0], x[1], x[2]))
    accepted = []; review = []; skipped = 0
    for score, a, b in possible:
        ra, rb = root(a), root(b)
        if ra == rb:
            continue
        la, lb = comp_labels[ra], comp_labels[rb]
        conflict = bool(la and lb and la != lb)
        locked = comp_locked[ra] or comp_locked[rb]
        # A label set may only merge with an identical label set, or an as-yet
        # unlabelled profile; different existing user labels NEVER merge.
        eligible = score >= auto_threshold and not conflict and not locked
        proposal = {'source_person_uuid': b, 'target_person_uuid': a,
                    'similarity': round(score, 6),
                    'reason': ('conflicting_manual_aliases' if conflict else
                               'manual_lock' if locked else
                               'below_auto_threshold' if score < auto_threshold else
                               'high_similarity')}
        if eligible:
            # Canonical root: prefer the already labeled profile, then UUID.
            keep, drop = sorted((ra, rb), key=lambda p: (not bool(comp_labels[p]), p))
            parent[drop] = keep
            comp_labels[keep] |= comp_labels[drop]
            comp_locked[keep] = comp_locked[keep] or comp_locked[drop]
            proposal['target_person_uuid'] = keep
            proposal['source_person_uuid'] = drop
            proposal['action'] = 'auto_merge_proposal'
            if len(accepted) < max_proposals:
                accepted.append(proposal)
            else:
                skipped += 1
        else:
            proposal['action'] = 'review_only'
            if len(review) < max_proposals:
                review.append(proposal)
            else:
                skipped += 1
    # All face UUIDs are immutable; emit only identity profile-level proposals.
    return {'schema': 'norm.media.identity.v1', 'profiles': len(profiles),
            'appearances': len(appearances), 'embedded_profiles': len(ids), 'skipped_mixed_model_profiles': sorted(mixed_models),
            'proposed_auto_merges': accepted, 'review_only': review,
            'truncated_proposals': skipped, 'applied': False,
            'thresholds': {'auto': auto_threshold, 'review': review_threshold},
            'notes': 'No profile or face UUID has been changed. Profile merge proposals must be applied by the caller.'}
