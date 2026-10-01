from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import cv2
import kornia
import numpy as np
import torch


VARIANTS = {
    "light": [(0.0, 1.0, 1.0), (0.03, 1.16, 0.95), (-0.02, 1.08, 1.22)],
    "medium": [(0.0, 1.0, 1.0), (0.04, 1.18, 0.92), (-0.03, 1.12, 1.25), (0.06, 0.94, 1.08), (-0.01, 1.28, 0.72)],
    "high": [(0.0, 1.0, 1.0), (0.05, 1.20, 0.90), (-0.04, 1.14, 1.30), (0.07, 0.92, 1.10), (-0.02, 1.32, 0.68)],
}


def _norm01(t: torch.Tensor) -> torch.Tensor:
    lo = t.amin(dim=(-2, -1), keepdim=True)
    hi = t.amax(dim=(-2, -1), keepdim=True)
    return (t - lo) / (hi - lo + 1e-6)


def _rgba(original_rgb: np.ndarray, mask: np.ndarray) -> np.ndarray:
    alpha = (mask.astype(np.uint8) * 255)[..., None]
    return np.concatenate([original_rgb, alpha], axis=2)


def _enhance(rgb: np.ndarray, brightness: float, contrast: float, saturation: float, device: torch.device) -> np.ndarray:
    x = torch.from_numpy(rgb).to(device=device, dtype=torch.float32).permute(2, 0, 1).unsqueeze(0) / 255.0
    mean = x.mean(dim=(-2, -1), keepdim=True)
    x = (x - mean) * contrast + mean + brightness
    gray = kornia.color.rgb_to_grayscale(x)
    x = gray.repeat(1, 3, 1, 1) + (x - gray.repeat(1, 3, 1, 1)) * saturation
    x = x.clamp(0.0, 1.0)
    return (x.squeeze(0).permute(1, 2, 0).mul(255).byte().cpu().numpy())


def _artifact_score(rgb: np.ndarray, device: torch.device) -> np.ndarray:
    x = torch.from_numpy(rgb).to(device=device, dtype=torch.float32).permute(2, 0, 1).unsqueeze(0) / 255.0
    gray = kornia.color.rgb_to_grayscale(x)
    blur = kornia.filters.gaussian_blur2d(gray, (5, 5), (1.15, 1.15))
    detail = _norm01((gray - blur).abs())
    edge = _norm01(kornia.filters.sobel(gray))
    edge_band = torch.nn.functional.max_pool2d((edge > 0.18).float(), 7, stride=1, padding=3)
    chroma = x - gray.repeat(1, 3, 1, 1)
    chroma_blur = kornia.filters.gaussian_blur2d(chroma, (5, 5), (1.0, 1.0))
    chroma_detail = _norm01((chroma - chroma_blur).abs().mean(dim=1, keepdim=True))
    h, w = gray.shape[-2:]
    block = torch.zeros((1, 1, h, w), device=device)
    block[:, :, ::8, :] = 1.0
    block[:, :, :, ::8] = 1.0
    block_band = torch.nn.functional.max_pool2d(block, 3, stride=1, padding=1)
    local_support = torch.nn.functional.avg_pool2d((edge > 0.23).float(), 9, stride=1, padding=4)
    score = 0.46 * detail * edge_band + 0.23 * detail * block_band + 0.24 * chroma_detail * edge_band - 0.13 * local_support
    return score.clamp(0, 1).squeeze().detach().cpu().numpy()


def _detect_lines(rgb: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    med = float(np.median(gray))
    lo = int(max(20, 0.66 * med))
    hi = int(min(255, max(lo + 25, 1.33 * med)))
    edges = cv2.Canny(gray, lo, hi, apertureSize=3, L2gradient=True)
    min_len = max(24, int(rgb.shape[1] * 0.07))
    raw = cv2.HoughLinesP(edges, 1, np.pi / 180.0, threshold=max(30, min_len // 2), minLineLength=min_len, maxLineGap=8)
    lines: list[dict] = []
    if raw is not None:
        for item in np.asarray(raw).reshape(-1, 4):
            x1, y1, x2, y2 = map(int, item)
            dx, dy = x2 - x1, y2 - y1
            length = math.hypot(dx, dy)
            angle = math.degrees(math.atan2(dy, dx))
            a = abs(angle)
            if a <= 3 or a >= 177:
                kind = "horizontal"
            elif 87 <= a <= 93:
                kind = "vertical"
            else:
                kind = "diagonal"
            lines.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "length": round(length, 2), "angle": round(angle, 2), "kind": kind})
    lines.sort(key=lambda x: x["length"], reverse=True)
    return edges, lines[:250]


def _cluster_horizontal(all_lines: list[tuple[int, dict]], variants: int) -> list[dict]:
    candidates = [(idx, line) for idx, line in all_lines if line["kind"] == "horizontal"]
    groups: list[list[tuple[int, dict]]] = []
    for item in sorted(candidates, key=lambda z: (z[1]["y1"] + z[1]["y2"]) / 2):
        y = (item[1]["y1"] + item[1]["y2"]) / 2
        match = next((g for g in groups if abs(y - np.mean([(q[1]["y1"] + q[1]["y2"]) / 2 for q in g])) <= 3.0), None)
        if match is None:
            groups.append([item])
        else:
            match.append(item)
    out = []
    for group in groups:
        seen = len({idx for idx, _ in group})
        if seen < max(2, math.ceil(variants * 0.5)):
            continue
        ys = [(line["y1"] + line["y2"]) / 2 for _, line in group]
        out.append({"y": round(float(np.median(ys)), 2), "variant_support": seen, "consensus": round(seen / variants, 3), "max_length": round(max(line["length"] for _, line in group), 2)})
    return sorted(out, key=lambda x: (-x["consensus"], -x["max_length"]))[:40]


def _paint_line_mask(shape: tuple[int, int], lines: list[dict], kind: str, thickness: int = 2) -> np.ndarray:
    mask = np.zeros(shape, dtype=np.uint8)
    for line in lines:
        if line["kind"] == kind:
            cv2.line(mask, (line["x1"], line["y1"]), (line["x2"], line["y2"]), 255, thickness, cv2.LINE_AA)
    return mask > 0


def _save_layers(out_dir: Path, original: np.ndarray, artifact: np.ndarray, lines: list[dict], edges: np.ndarray) -> dict:
    h, w = original.shape[:2]
    artifact_mask = artifact >= 0.48
    horizontal = _paint_line_mask((h, w), lines, "horizontal", 2) & ~artifact_mask
    vertical = _paint_line_mask((h, w), lines, "vertical", 2) & ~artifact_mask & ~horizontal
    diagonal = _paint_line_mask((h, w), lines, "diagonal", 2) & ~artifact_mask & ~horizontal & ~vertical
    hsv = cv2.cvtColor(original, cv2.COLOR_RGB2HSV)
    sat_cut = max(48, int(np.percentile(hsv[..., 1], 70)))
    color_trace = (hsv[..., 1] >= sat_cut) & ~artifact_mask & ~horizontal & ~vertical & ~diagonal
    edge_detail = (edges > 0) & ~artifact_mask & ~horizontal & ~vertical & ~diagonal & ~color_trace
    background = ~(artifact_mask | horizontal | vertical | diagonal | color_trace | edge_detail)
    masks = [("01_background", background), ("02_edge_detail", edge_detail), ("03_color_traces", color_trace), ("04_vertical", vertical), ("05_diagonal", diagonal), ("06_horizontal", horizontal), ("07_artifacts", artifact_mask)]
    paths = {}
    reconstruction = np.zeros_like(original)
    for name, mask in masks:
        path = out_dir / f"{name}.png"
        cv2.imwrite(str(path), cv2.cvtColor(_rgba(original, mask), cv2.COLOR_RGBA2BGRA))
        reconstruction[mask] = original[mask]
        paths[name] = str(path)
    recon_path = out_dir / "reconstruction.png"
    cv2.imwrite(str(recon_path), cv2.cvtColor(reconstruction, cv2.COLOR_RGB2BGR))
    mae = float(np.abs(reconstruction.astype(np.int16) - original.astype(np.int16)).mean())
    similarity = 1.0 - mae / 255.0
    return {"layers": paths, "reconstruction": str(recon_path), "reconstruction_similarity": round(similarity, 6), "artifact_fraction": round(float(artifact_mask.mean()), 6)}




def _color_family_masks(hsv: np.ndarray) -> dict[str, np.ndarray]:
    h, s, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    return {
        "red": (((h <= 12) | (h >= 168)) & (s >= 45) & (v >= 60)),
        "green": ((h >= 35) & (h <= 95) & (s >= 45) & (v >= 45)),
        "blue": ((h >= 96) & (h <= 135) & (s >= 45) & (v >= 45)),
        "orange": ((h >= 8) & (h <= 32) & (s >= 45) & (v >= 60)),
    }


def _row_local_median(values: np.ndarray, radius: int = 10) -> np.ndarray:
    h, w = values.shape
    padded = np.pad(values.astype(np.int16), ((0, 0), (radius, radius)), mode="edge")
    out = np.empty((h, w), dtype=np.int16)
    for x in range(w):
        out[:, x] = np.median(padded[:, x : x + 2 * radius + 1], axis=1).astype(np.int16)
    return out


def _layer_feature_table(original: np.ndarray) -> dict[str, np.ndarray]:
    hsv = cv2.cvtColor(original, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(original, cv2.COLOR_RGB2GRAY)
    edge = cv2.Canny(gray, 50, 150).astype(np.uint8)
    value = hsv[..., 2].astype(np.int16)
    local_med = _row_local_median(value, 10)
    darkness_delta = (local_med - value).astype(np.int16)
    return {
        "h": hsv[..., 0],
        "s": hsv[..., 1],
        "v": hsv[..., 2],
        "edge": edge,
        "darkness_delta": darkness_delta,
    }


def _detect_occluded_traces(original: np.ndarray, out_dir: Path, pane_top: int | None = None, pane_bottom: int | None = None) -> dict:
    """Infer thin foreground/background line continuations through broad same-color fills.

    `z_order` here is inferred render order, not physical depth.  A candidate says:
    broad fill occupies a region, while a narrow darker line of the same color family
    continues into/through it and is therefore likely partially occluded.
    """
    hsv = cv2.cvtColor(original, cv2.COLOR_RGB2HSV)
    features = _layer_feature_table(original)
    h_img, w_img = original.shape[:2]
    masks = _color_family_masks(hsv)
    candidates: list[dict] = []
    overlay = original.copy()
    crop_index = 0

    for family, fam_mask in masks.items():
        # Broad, lighter regions are candidate fills (e.g. volume bars).
        soft = fam_mask & (features["s"] <= 135) & (features["v"] >= 185)
        soft8 = (soft.astype(np.uint8) * 255)
        # Use two views of broad fills. The raw/closed view preserves ordinary bars;
        # the horizontal-open view removes thin vertical connectors so a wick cannot
        # merge candle + volume into one sparse component. Unioning both avoids tuning
        # the detector to only one chart style.
        raw_fill = cv2.morphologyEx(soft8, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        horizontal_span = max(9, int(round(w_img * 0.008)))
        broad_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal_span, 3))
        opened_fill = cv2.morphologyEx(soft8, cv2.MORPH_OPEN, broad_kernel)
        opened_fill = cv2.morphologyEx(opened_fill, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        boxes: list[tuple[int, int, int, int, float]] = []
        for fill_mask in (raw_fill, opened_fill):
            num, labels, stats, _ = cv2.connectedComponentsWithStats((fill_mask > 0).astype(np.uint8), 8)
            for i in range(1, num):
                x0, y0, bw, bh, area = map(int, stats[i])
                fill = area / max(1, bw * bh)
                same_pane = (pane_top is None or y0 >= pane_top) and (pane_bottom is None or (y0 < pane_bottom and y0 + bh <= pane_bottom + 2))
                if bw < 8 or bh < 20 or fill < 0.42 or not same_pane:
                    continue
                candidate = (x0, y0, bw, bh, float(fill))
                if not any(abs(x0-q[0]) <= 3 and abs(y0-q[1]) <= 3 and abs(bw-q[2]) <= 5 and abs(bh-q[3]) <= 5 for q in boxes):
                    boxes.append(candidate)

        for x0, y0, bw, bh, fill in boxes:
            x1 = min(w_img, x0 + bw)
            y1 = min(h_img, y0 + bh)
            for x in range(x0, x1):
                ys0 = max(0, y0 - 90)
                ys1 = y1
                good = []
                for y in range(ys0, ys1):
                    if not fam_mask[y, x]:
                        good.append(False)
                        continue
                    delta = int(features["darkness_delta"][y, x])
                    # Count similarly-dark neighbors. A thin line should occupy only a few columns.
                    xa = max(x0, x - 5)
                    xb = min(x1, x + 6)
                    row_delta = features["darkness_delta"][y, xa:xb]
                    narrow = int(np.sum(row_delta >= 14))
                    good.append(delta >= 14 and narrow <= 5)
                if not any(good):
                    continue
                arr = (np.asarray(good, dtype=np.uint8) * 255).reshape(-1, 1)
                arr = cv2.morphologyEx(arr, cv2.MORPH_CLOSE, np.ones((7, 1), np.uint8)).reshape(-1) > 0
                idx = np.where(arr)[0]
                if not len(idx):
                    continue
                runs: list[tuple[int, int]] = []
                s0 = prev = int(idx[0])
                for q0 in idx[1:]:
                    q = int(q0)
                    if q <= prev + 1:
                        prev = q
                    else:
                        runs.append((s0, prev))
                        s0 = prev = q
                runs.append((s0, prev))
                for a, b in runs:
                    ya, yb = ys0 + a, ys0 + b
                    length = yb - ya + 1
                    inside_depth = max(0, yb - max(ya, y0) + 1)
                    above_length = max(0, min(yb, y0 - 1) - ya + 1)
                    # Two useful forms:
                    # 1) line enters the fill from above; 2) line begins at the fill edge and continues deeply inside.
                    enters = ya < y0 and inside_depth >= 8
                    edge_buried = abs(ya - y0) <= 3 and inside_depth >= 20
                    if length < 18 or not (enters or edge_buried):
                        continue
                    score = float(length + 1.5 * inside_depth + min(above_length, 30))
                    candidates.append({
                        "color_family": family,
                        "x": int(x),
                        "y_start": int(ya),
                        "y_end": int(yb),
                        "length": int(length),
                        "inside_depth": int(inside_depth),
                        "above_length": int(above_length),
                        "occluder_box": [int(x0), int(y0), int(bw), int(bh)],
                        "occluder_fill": round(fill, 4),
                        "inferred_z": {"trace": 0, "broad_fill": 1},
                        "relationship": "thin_trace_continues_through_broad_same_color_fill",
                        "score": round(score, 3),
                    })

    # De-duplicate neighboring x samples of the same physical trace.
    selected: list[dict] = []
    for c in sorted(candidates, key=lambda z: z["score"], reverse=True):
        if c["score"] < 80.0:
            continue
        if any(
            c["color_family"] == q["color_family"]
            and abs(c["x"] - q["x"]) <= 4
            and abs(c["y_start"] - q["y_start"]) <= 10
            and abs(c["y_end"] - q["y_end"]) <= 10
            for q in selected
        ):
            continue
        selected.append(c)
        if len(selected) >= 24:
            break

    for i, c in enumerate(selected):
        color = (255, 215, 0) if i < 8 else (255, 0, 255)
        cv2.line(overlay, (c["x"], c["y_start"]), (c["x"], c["y_end"]), color, 2)
        x0, y0, bw, bh = c["occluder_box"]
        cv2.rectangle(overlay, (x0, y0), (x0 + bw - 1, y0 + bh - 1), color, 1)
        xa, xb = max(0, c["x"] - 50), min(w_img, c["x"] + 51)
        ya, yb = max(0, c["y_start"] - 35), min(h_img, c["y_end"] + 35)
        crop_path = out_dir / f"occlusion_crop_{i + 1:02d}.png"
        crop_rgb = original[ya:yb, xa:xb]
        cv2.imwrite(str(crop_path), cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR))
        zoom_path = out_dir / f"occlusion_crop_{i + 1:02d}_zoom4x.png"
        zoom = cv2.resize(crop_rgb, None, fx=4.0, fy=4.0, interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(str(zoom_path), cv2.cvtColor(zoom, cv2.COLOR_RGB2BGR))
        c["crop_box"] = [int(xa), int(ya), int(xb), int(yb)]
        c["crop_path"] = str(crop_path)
        c["zoom_crop_path"] = str(zoom_path)

    overlay_path = out_dir / "occlusion_overlay.png"
    cv2.imwrite(str(overlay_path), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    table_path = out_dir / "layer_feature_table.npz"
    np.savez_compressed(
        table_path,
        h=features["h"], s=features["s"], v=features["v"],
        edge=features["edge"], darkness_delta=features["darkness_delta"],
    )
    scene_objects = []
    scene_relationships = []
    seen_boxes = {}
    for idx, c in enumerate(selected, 1):
        box_key = tuple(c["occluder_box"])
        if box_key not in seen_boxes:
            oid = f"fill-{len(seen_boxes)+1:03d}"
            seen_boxes[box_key] = oid
            scene_objects.append({
                "object_id": oid, "object_role": "data", "primitive_type": "broad_color_fill",
                "color_family": c["color_family"], "box": c["occluder_box"],
                "inferred_z": 1, "confidence": min(1.0, 0.5 + c["occluder_fill"] / 2.0),
            })
        tid = f"trace-{idx:03d}"
        scene_objects.append({
            "object_id": tid, "object_role": "data", "primitive_type": "thin_vertical_trace",
            "color_family": c["color_family"], "x": c["x"],
            "y_start": c["y_start"], "y_end": c["y_end"],
            "inferred_z": 0, "confidence": min(1.0, c["score"] / 150.0),
            "crop_path": c["crop_path"], "zoom_crop_path": c["zoom_crop_path"],
        })
        scene_relationships.append({
            "type": "occluded_by", "subject": tid, "object": seen_boxes[box_key],
            "evidence": "same color family + narrow darker vertical continuity through broad fill",
        })
    scene = {
        "z_semantics": "inferred render/layer order; lower z may be visually behind a higher-z occluder",
        "objects": scene_objects, "relationships": scene_relationships,
    }
    scene_path = out_dir / "scene_graph.json"
    scene_path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "occlusion_candidates": selected,
        "occlusion_overlay": str(overlay_path),
        "feature_table": str(table_path),
        "scene_graph": str(scene_path),
        "feature_dimensions": ["x", "y", "pane_id", "h", "s", "v", "edge", "darkness_delta", "object_id", "primitive_type", "inferred_z", "occluded_by", "confidence"],
    }

def _augment_scene_with_reference_geometry(scene_path: str, consensus: list[dict], pane_top: int, pane_bottom: int | None, width: int) -> list[dict]:
    """Add stable chart-coordinate primitives as reference objects.

    These are constants/reference geometry used to measure data objects; they are not market data.
    """
    bottom = pane_bottom if pane_bottom is not None else 10**9
    refs = []
    for row in consensus:
        y = int(round(row.get("y", -1)))
        if not (pane_top <= y <= bottom):
            continue
        if row.get("consensus", 0) < 0.66 or row.get("max_length", 0) < 0.45 * width:
            continue
        refs.append({
            "object_id": f"reference-hgrid-{len(refs)+1:03d}",
            "object_role": "reference",
            "primitive_type": "horizontal_grid_or_pane_line",
            "y": y,
            "x_start": 0,
            "x_end": width - 1,
            "inferred_z": -10,
            "confidence": float(row.get("consensus", 0)),
            "measurement_role": "constant_coordinate_reference",
        })
    sp=Path(scene_path)
    scene=json.loads(sp.read_text(encoding="utf-8"))
    scene.setdefault("layer_taxonomy", {
        "reference": "stable coordinate/background geometry used to measure data",
        "data": "variable chart content such as candles, wicks, volume, averages, indicators",
        "annotation_ui": "labels, text, controls, and non-data chrome",
    })
    scene.setdefault("objects", []).extend(refs)
    scene["measurement_semantics"] = "data geometry is interpreted against reference geometry; occlusion may occur between data objects without changing the reference coordinate system"
    sp.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")
    return refs


def _infer_primary_pane_bottom(consensus: list[dict], height: int, width: int) -> int | None:
    """Infer the first major full-width separator below the main price pane.

    Conservative: only consider strong horizontal consensus in the middle band of the image.
    """
    candidates = [
        int(round(row["y"]))
        for row in consensus
        if 0.28 * height <= row["y"] <= 0.60 * height
        and row.get("consensus", 0) >= 0.66
        and row.get("max_length", 0) >= 0.45 * width
    ]
    return min(candidates) if candidates else None


def analyze(path: Path, out_dir: Path, profile: str) -> dict:
    started = time.monotonic()
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError(f"unsupported or unreadable image: {path}")
    original = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    settings = VARIANTS[profile]
    out_dir.mkdir(parents=True, exist_ok=True)
    variant_paths, scores, all_lines = [], [], []
    base_edges = None
    base_lines: list[dict] = []
    for idx, (brightness, contrast, saturation) in enumerate(settings):
        enhanced = _enhance(original, brightness, contrast, saturation, device)
        score = _artifact_score(enhanced, device)
        edges, lines = _detect_lines(enhanced)
        if idx == 0:
            base_edges, base_lines = edges, lines
        scores.append(score)
        all_lines.extend((idx, line) for line in lines)
        variant_path = out_dir / f"variant_{idx + 1:02d}_b{brightness:+.2f}_c{contrast:.2f}_s{saturation:.2f}.png"
        cv2.imwrite(str(variant_path), cv2.cvtColor(enhanced, cv2.COLOR_RGB2BGR))
        variant_paths.append({"path": str(variant_path), "brightness": brightness, "contrast": contrast, "saturation": saturation, "line_count": len(lines)})
    artifact = np.median(np.stack(scores, axis=0), axis=0)
    artifact_path = out_dir / "artifact_probability.png"
    cv2.imwrite(str(artifact_path), np.clip(artifact * 255, 0, 255).astype(np.uint8))
    clean = original.copy()
    median = cv2.medianBlur(cv2.cvtColor(original, cv2.COLOR_RGB2BGR), 3)
    median = cv2.cvtColor(median, cv2.COLOR_BGR2RGB)
    replace = artifact >= 0.48
    clean[replace] = median[replace]
    clean_path = out_dir / "artifact_suppressed.png"
    cv2.imwrite(str(clean_path), cv2.cvtColor(clean, cv2.COLOR_RGB2BGR))
    consensus = _cluster_horizontal(all_lines, len(settings))
    overlay = original.copy()
    for row in consensus[:20]:
        y = int(round(row["y"]))
        cv2.line(overlay, (0, y), (overlay.shape[1] - 1, y), (255, 215, 0), 1, cv2.LINE_AA)
    overlay_path = out_dir / "geometry_overlay.png"
    cv2.imwrite(str(overlay_path), cv2.cvtColor(overlay, cv2.COLOR_RGB2BGR))
    layers = _save_layers(out_dir, original, artifact, base_lines, base_edges)
    pane_top = int(round(original.shape[0] * 0.10))
    pane_bottom = _infer_primary_pane_bottom(consensus, original.shape[0], original.shape[1])
    occlusion = _detect_occluded_traces(original, out_dir, pane_top=pane_top, pane_bottom=pane_bottom)
    reference_geometry = _augment_scene_with_reference_geometry(
        occlusion["scene_graph"], consensus, pane_top, pane_bottom, original.shape[1]
    )
    result = {
        "source": str(path),
        "profile": profile,
        "device": str(device),
        "width": int(original.shape[1]),
        "height": int(original.shape[0]),
        "variants": variant_paths,
        "artifact_probability": str(artifact_path),
        "artifact_suppressed": str(clean_path),
        "geometry_overlay": str(overlay_path),
        "horizontal_consensus": consensus,
        "layers": layers["layers"],
        "reconstruction": layers["reconstruction"],
        "reconstruction_similarity": layers["reconstruction_similarity"],
        "artifact_fraction": layers["artifact_fraction"],
        "occlusion_candidates": occlusion["occlusion_candidates"],
        "occlusion_overlay": occlusion["occlusion_overlay"],
        "layer_feature_table": occlusion["feature_table"],
        "scene_graph": occlusion["scene_graph"],
        "reference_geometry": reference_geometry,
        "feature_dimensions": occlusion["feature_dimensions"] + ["object_role", "measurement_role"],
        "primary_pane_top": pane_top,
        "primary_pane_bottom": pane_bottom,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    metadata = out_dir / "analysis.json"
    metadata.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["analysis_json"] = str(metadata)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", choices=tuple(VARIANTS), default="light")
    args = parser.parse_args()
    result = analyze(Path(args.image).resolve(), Path(args.output).resolve(), args.profile)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
