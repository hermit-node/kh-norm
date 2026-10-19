"""CPU-default regression coverage for Vision Parse; no downloads or GPU inference."""
from __future__ import annotations

import inspect
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))
from plugins.vision_parse.src import main, media_faces, media_vision
from norm_runtime.plugin_identity import verify_identity

class VisionCPUDefaultTests(unittest.TestCase):
    def test_entrypoints_default_to_cpu(self):
        for fn in (main.vision_media_faces, main.vision_media_scan_video_faces,
                   media_vision.detect_faces, media_vision.scan_video_faces,
                   media_faces.detect):
            self.assertEqual(inspect.signature(fn).parameters["backend"].default, "cpu", fn.__name__)

    def test_cpu_and_legacy_auto_do_not_invoke_cuda(self):
        import numpy as np
        image = np.zeros((112, 112, 3), dtype=np.uint8)
        class Detector:
            def setInputSize(self, size):
                self.size = size
            def detect(self, image):
                return None, None
        detector = Detector()
        fake_cv2 = types.SimpleNamespace(
            FaceDetectorYN=types.SimpleNamespace(create=lambda *a, **kw: detector))
        with patch.dict(sys.modules, {"cv2": fake_cv2}),              patch.object(media_faces, "_ort_session", side_effect=AssertionError("unexpected CUDA use")):
            for requested in ("cpu", "auto"):
                actual = "cpu" if requested == "auto" else requested
                faces, backend, session = media_faces._face_rows(
                    image, Path("unused.png"), backend=actual, folder=Path("unused_models"))
                self.assertEqual(backend, "cpu_opencv")
                self.assertEqual(faces, [])
                self.assertIsNone(session)

    def test_detect_legacy_auto_selects_cpu(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "source.png"
            path.write_bytes(b"test image placeholder")
            valid = {"yunet": {"sha256_valid": True}, "sface": {"sha256_valid": True}}
            with patch.object(media_faces, "models_status", return_value={"models": valid}),                  patch.object(media_faces, "_face_rows", return_value=([], "cpu_opencv", None)) as rows,                  patch("cv2.imread", return_value=np.zeros((64, 64, 3), dtype=np.uint8)):
                result = media_faces.detect(str(path),backend="auto")
            self.assertEqual(rows.call_args.kwargs["backend"], "cpu")
            self.assertEqual(result["backend"], "cpu_opencv")

    def test_video_scan_uses_cpu_default(self):
        with patch.object(media_vision, "_duration", return_value=62),              patch.object(media_vision, "detect_faces", return_value={"faces": []}) as detect:
            result = media_vision.scan_video_faces("sample.mp4", samples_per_scene=2)
            self.assertEqual(result["samples"], 2)
            self.assertEqual(detect.call_count, 2)
            for call in detect.call_args_list:
                self.assertEqual(call.kwargs["backend"], "cpu")

    def test_no_default_gpu_requirement_and_valid_manifest(self):
        requirements = (ROOT / "core/requirements.txt").read_text(encoding="utf-8")
        self.assertFalse(any(
            line.strip().lower().startswith(("onnxruntime", "onnxruntime-gpu"))
            for line in requirements.splitlines()))
        info = verify_identity(ROOT / "plugins/vision_parse")
        self.assertEqual(info["name"], "vision_parse")

if __name__ == "__main__":
    unittest.main(verbosity=2)
