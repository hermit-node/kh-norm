import argparse
import importlib.util
import json
from pathlib import Path

import cv2
import numpy as np

BASE_PATH = Path(__file__).with_name("image_analyzer_v2.py")
spec = importlib.util.spec_from_file_location("norm_image_analyzer_v2", BASE_PATH)
base = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(base)


def _row_local_median(values: np.ndarray, radius: int = 10) -> np.ndarray:
    h, w = values.shape
    padded = np.pad(values.astype(np.int16), ((0, 0), (radius, radius)), mode="edge")
    out = np.empty((h, w), dtype=np.int16)
    for x in range(w):
        out[:, x] = np.median(padded[:, x:x + 2 * radius + 1], axis=1).astype(np.int16)
    return out


def _trace_strength_map(rgb: np.ndarray, family: str, pane_top: int, pane_bottom: int | None):
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    fam = base._color_family_masks(hsv).get(family, np.zeros(gray.shape, dtype=bool))
    value = hsv[..., 2].astype(np.int16)
    local = _row_local_median(value, 10)
    dark = np.clip((local - value - 4) / 36.0, 0.0, 1.0).astype(np.float32)
    strong = (fam & (dark >= 0.12)).astype(np.float32)
    continuity = cv2.blur(strong, (1, 11))
    horizontal_count = cv2.blur(strong, (11, 1)) * 11.0
    thinness = np.clip(1.0 - np.maximum(horizontal_count - 2.0, 0.0) / 7.0, 0.0, 1.0)

    fgray = gray.astype(np.float32) / 255.0
    gx = np.abs(cv2.Sobel(fgray, cv2.CV_32F, 1, 0, ksize=3))
    gy = np.abs(cv2.Sobel(fgray, cv2.CV_32F, 0, 1, ksize=3))
    verticality = gx / (gx + gy + 1e-6)
    sat = np.clip((hsv[..., 1].astype(np.float32) - 35.0) / 150.0, 0.0, 1.0)

    score = fam.astype(np.float32)
    for channel in (0.15 + 0.85 * dark, 0.20 + 0.80 * thinness,
                    0.20 + 0.80 * continuity, 0.25 + 0.75 * verticality,
                    0.25 + 0.75 * sat):
        score *= channel

    bottom = pane_bottom if pane_bottom is not None else score.shape[0]
    pane_mask = np.zeros_like(score, dtype=bool)
    pane_mask[max(0, pane_top):min(score.shape[0], bottom), :] = True
    score[~pane_mask] = 0.0
    vals = score[pane_mask & fam]
    vals = vals[np.isfinite(vals)]
    stats = {
        "p95": float(np.percentile(vals, 95)) if vals.size else 0.0,
        "p99": float(np.percentile(vals, 99)) if vals.size else 0.0,
        "max": float(vals.max()) if vals.size else 0.0,
        "count": int(vals.size),
    }
    return score, stats


def _build_views(original: np.ndarray):
    views = [("original", original)]
    views.append(("contrast_up", cv2.convertScaleAbs(original, alpha=1.25, beta=-18)))
    views.append(("contrast_down", cv2.convertScaleAbs(original, alpha=0.88, beta=14)))
    hsv = cv2.cvtColor(original, cv2.COLOR_RGB2HSV)
    sat = hsv.copy()
    sat[..., 1] = np.clip(sat[..., 1].astype(np.float32) * 1.25, 0, 255).astype(np.uint8)
    views.append(("saturation_up", cv2.cvtColor(sat, cv2.COLOR_HSV2RGB)))
    gamma = np.clip(((original.astype(np.float32) / 255.0) ** 0.82) * 255.0, 0, 255).astype(np.uint8)
    views.append(("gamma_bright", gamma))
    gamma2 = np.clip(((original.astype(np.float32) / 255.0) ** 1.18) * 255.0, 0, 255).astype(np.uint8)
    views.append(("gamma_dark", gamma2))
    return views


def _focus_rescan(original: np.ndarray, candidates: list[dict], out_dir: Path,
                  pane_top: int, pane_bottom: int | None):
    if not candidates:
        return []
    views = _build_views(original)
    families = sorted({c["color_family"] for c in candidates})
    maps = {(fam, name): _trace_strength_map(view, fam, pane_top, pane_bottom)
            for fam in families for name, view in views}
    focused = []
    for idx, c in enumerate(candidates, 1):
        fam = c["color_family"]
        xa, xb = max(0, c["x"] - 2), min(original.shape[1], c["x"] + 3)
        ya, yb = max(pane_top, c["y_start"]), min(original.shape[0], c["y_end"] + 1)
        view_stats = []
        refined_end = int(c["y_end"])
        for name, _ in views:
            score_map, global_stats = maps[(fam, name)]
            region = score_map[ya:yb, xa:xb]
            peak = float(region.max()) if region.size else 0.0
            pane = score_map[max(0, pane_top):(pane_bottom or score_map.shape[0]), :].ravel()
            pane = pane[pane > 0]
            percentile = float(100.0 * np.mean(pane <= peak)) if pane.size else 0.0
            tail0 = max(ya, c["y_end"] - 5)
            tail1 = min(score_map.shape[0], c["y_end"] + 35)
            tail = score_map[tail0:tail1, xa:xb]
            if tail.size:
                strong = np.where(tail.max(axis=1) >= max(0.12, global_stats["p95"]))[0]
                if len(strong):
                    refined_end = max(refined_end, tail0 + int(strong.max()))
            view_stats.append({
                "view": name,
                "peak": round(peak, 6),
                "whole_pane_percentile": round(percentile, 3),
                "p95": round(global_stats["p95"], 6),
                "p99": round(global_stats["p99"], 6),
            })

        agreement = float(np.mean([v["whole_pane_percentile"] >= 95.0 for v in view_stats]))
        median_pct = float(np.median([v["whole_pane_percentile"] for v in view_stats]))
        geometry = min(1.0, c["inside_depth"] / 30.0) * min(1.0, c["score"] / 130.0)
        confidence = 0.45 * (median_pct / 100.0) + 0.35 * agreement + 0.20 * geometry
        if confidence >= 0.82 and agreement >= 0.75:
            status = "high_confidence"
        elif confidence >= 0.62:
            status = "supported"
        else:
            status = "uncertain"

        c2 = dict(c)
        c2.update({
            "focus_scan_status": status,
            "focus_confidence": round(float(confidence), 4),
            "cross_view_agreement": round(agreement, 3),
            "median_whole_pane_percentile": round(median_pct, 3),
            "refined_y_end": refined_end,
            "focus_views": view_stats,
        })
        x0, y0, x1, y1 = c["crop_box"]
        crop = original[y0:y1, x0:x1]
        path = out_dir / f"focus_scan_{idx:02d}_8x.png"
        zoom = cv2.resize(crop, None, fx=8.0, fy=8.0, interpolation=cv2.INTER_NEAREST)
        cv2.imwrite(str(path), cv2.cvtColor(zoom, cv2.COLOR_RGB2BGR))
        c2["focus_crop_path"] = str(path)
        focused.append(c2)
    return focused


def _merge_focus_into_scene(scene_path: str, focused: list[dict]):
    path = Path(scene_path)
    scene = json.loads(path.read_text(encoding="utf-8"))
    by_x = {obj.get("x"): obj for obj in scene.get("objects", [])
            if obj.get("primitive_type") == "thin_vertical_trace"}
    for item in focused:
        obj = by_x.get(item["x"])
        if obj is None:
            continue
        obj["focus_scan_status"] = item["focus_scan_status"]
        obj["focus_confidence"] = item["focus_confidence"]
        obj["cross_view_agreement"] = item["cross_view_agreement"]
        obj["whole_pane_percentile"] = item["median_whole_pane_percentile"]
        obj["refined_y_end"] = item["refined_y_end"]
        obj["focus_crop_path"] = item["focus_crop_path"]
    scene["focus_policy"] = (
        "ambiguous/high-impact regions are rescanned across non-geometric views; "
        "features are promoted only when cross-view evidence converges"
    )
    path.write_text(json.dumps(scene, ensure_ascii=False, indent=2), encoding="utf-8")


def analyze(path: Path, out_dir: Path, profile: str):
    result = base.analyze(path, out_dir, profile)
    bgr = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if bgr is None:
        return result
    original = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    pane_top = int(result.get("primary_pane_top") or round(original.shape[0] * 0.10))
    pane_bottom = result.get("primary_pane_bottom")
    candidates = result.get("occlusion_candidates") or []
    focused = _focus_rescan(original, candidates, out_dir, pane_top, pane_bottom)
    result["occlusion_candidates"] = focused
    result["focus_rescans"] = focused
    result["analysis_weaknesses"] = [{
        "type": "ambiguous_or_occluded_data_geometry",
        "count": sum(1 for c in focused if c["focus_scan_status"] != "high_confidence"),
        "policy": "rescan locally; do not promote unless cross-view evidence converges",
    }]
    _merge_focus_into_scene(result["scene_graph"], focused)
    metadata = Path(result["analysis_json"])
    metadata.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--output", required=True)
    parser.add_argument("--profile", choices=tuple(base.VARIANTS), default="light")
    args = parser.parse_args()
    result = analyze(Path(args.image).resolve(), Path(args.output).resolve(), args.profile)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
