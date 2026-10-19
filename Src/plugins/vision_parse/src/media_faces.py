"""YuNet detection + SFace embeddings, CUDA via ONNX Runtime or CPU OpenCV.

No person names or media catalog dependencies. Never downloads a model implicitly.
"""
from __future__ import annotations
import hashlib
import math
import uuid
from pathlib import Path
from functools import lru_cache
from threading import Lock
from typing import Any

MODEL_FILES = {
    'yunet': ('face_detection_yunet_2023mar.onnx', '8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4'),
    'sface': ('face_recognition_sface_2021dec.onnx', '0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79'),
}
MODEL_URLS = {
    name: f'https://huggingface.co/opencv/opencv_zoo/resolve/main/models/{"face_detection_yunet" if name == "yunet" else "face_recognition_sface"}/{filename}'
    for name, (filename, _digest) in MODEL_FILES.items()
}
NAMESPACE = uuid.UUID('13cbb20f-4ddc-47a3-9b5d-11d0556f9afd')
GPU_INFERENCE_LOCK = Lock()
DEST_112 = [[38.2946, 51.6963], [73.5318, 51.5014],
            [56.0252, 71.7366], [41.5493, 92.3655], [70.7299, 92.2041]]


def model_root() -> Path:
    return Path(__file__).resolve().parents[1] / 'models'


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for part in iter(lambda: f.read(1024 * 1024), b''):
            digest.update(part)
    return digest.hexdigest()


def models_status(directory: str = '') -> dict:
    folder = Path(directory).expanduser() if directory else model_root()
    models = {}
    for kind, (name, expected) in MODEL_FILES.items():
        file = folder / name
        valid = bool(file.is_file() and _sha(file) == expected)
        models[kind] = {'path': str(file), 'present': file.is_file(), 'sha256_valid': valid}
    try:
        import cv2
        cv2_ready = all(hasattr(cv2, attr) for attr in ('FaceDetectorYN', 'FaceRecognizerSF'))
    except (ImportError, OSError):
        cv2_ready = False
    try:
        import onnxruntime as ort
        providers = ort.get_available_providers()
    except (ImportError, OSError):
        providers = []
    return {'schema': 'norm.media.models.v1', 'models': models, 'opencv_cpu_ready': cv2_ready,
            'onnxruntime_cuda_advertised': 'CUDAExecutionProvider' in providers,
            'ready': cv2_ready and all(v['sha256_valid'] for v in models.values()),
            'cuda_requires_smoke_test': True}


def setup_models(directory: str = '', approved: bool = False) -> dict:
    """Explicit opt-in: bounded atomic download and pinned SHA-256 verification."""
    if not approved:
        raise PermissionError('Model download requires approved=True; no implicit network access')
    import os
    import tempfile
    import urllib.request
    folder = Path(directory).expanduser() if directory else model_root()
    folder.mkdir(parents=True, exist_ok=True)
    installed = []
    for kind, (name, expected) in MODEL_FILES.items():
        output = folder / name
        if output.is_file() and _sha(output) == expected:
            installed.append({'model': kind, 'status': 'reused'})
            continue
        fd, tmp = tempfile.mkstemp(prefix='.vision-model-', suffix='.part', dir=str(folder))
        os.close(fd)
        try:
            req = urllib.request.Request(MODEL_URLS[kind], headers={'User-Agent': 'Norm-Vision/0.4'})
            with urllib.request.urlopen(req, timeout=90) as response, open(tmp, 'wb') as dst:
                size = 0
                while block := response.read(1 << 20):
                    size += len(block)
                    if size > 90_000_000:
                        raise ValueError('Model exceeds permitted download size')
                    dst.write(block)
            if _sha(Path(tmp)) != expected:
                raise ValueError(f'{name} failed pinned SHA-256 verification')
            os.replace(tmp, output)
            installed.append({'model': kind, 'status': 'installed'})
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return {'installed': installed, 'status': models_status(str(folder))}


@lru_cache(maxsize=4)
def _ort_session(path: Path, prefer_cuda: bool):
    import onnxruntime as ort
    # Prefer compatible CUDA/cuDNN from the existing PyTorch environment.
    if hasattr(ort, 'preload_dlls'):
        try:
            ort.preload_dlls()
        except (OSError, RuntimeError):
            pass
    providers = ort.get_available_providers()
    if prefer_cuda and 'CUDAExecutionProvider' not in providers:
        raise RuntimeError('ONNX Runtime CUDAExecutionProvider unavailable')
    cuda_opts = {'gpu_mem_limit': str(768 * 1024 * 1024),
                 'arena_extend_strategy': 'kSameAsRequested'}
    choices = [('CUDAExecutionProvider', cuda_opts), 'CPUExecutionProvider'] if prefer_cuda else ['CPUExecutionProvider']
    opts = ort.SessionOptions()
    opts.enable_mem_pattern = False
    session = ort.InferenceSession(str(path), sess_options=opts, providers=choices)
    if prefer_cuda and 'CUDAExecutionProvider' not in session.get_providers():
        raise RuntimeError('CUDA provider failed to initialize')
    return session


def _decode_yunet(outputs: list, names: list[str], image_shape: tuple[int,int], *, threshold: float = .85):
    """Decode the official OpenCV Zoo YuNet stride 8/16/32 ONNX output tensors."""
    import numpy as np
    import cv2
    h, w = image_shape
    by_name = {key: np.asarray(value).reshape(-1) for key, value in zip(names, outputs)}
    found = []
    if len(outputs) == 1:
        rows = np.asarray(outputs[0]).reshape(-1, 15)
        for row in rows:
            if float(row[14]) >= threshold:
                found.append([float(x) for x in row[:15]])
        return found
    for step in (8, 16, 32):
        suffix = str(step)
        cls = by_name.get('cls_' + suffix)
        obj = by_name.get('obj_' + suffix)
        bbox = by_name.get('bbox_' + suffix)
        kps = by_name.get('kps_' + suffix)
        if cls is None or obj is None or bbox is None or kps is None:
            raise RuntimeError('Unexpected YuNet output names: ' + ', '.join(names))
        cols = int(math.ceil(w / step)); rows = int(math.ceil(h / step))
        for i, (class_score, object_score) in enumerate(zip(cls, obj)):
            conf = math.sqrt(max(0, float(class_score)) * max(0, float(object_score)))
            if conf < threshold:
                continue
            r, c = divmod(i, cols)
            if r >= rows:
                break
            dx, dy, dw, dh = bbox[i*4:i*4+4]
            cx, cy = (c + float(dx)) * step, (r + float(dy)) * step
            bw, bh = math.exp(min(12, float(dw))) * step, math.exp(min(12, float(dh))) * step
            landmarks = []
            for j in range(5):
                px, py = kps[i*10+j*2:i*10+j*2+2]
                landmarks.extend([(c + float(px))*step, (r + float(py))*step])
            found.append([cx-bw/2, cy-bh/2, bw, bh, *landmarks, conf])
    if not found:
        return []
    boxes = [[int(x) for x in row[:4]] for row in found]
    confidences = [float(row[14]) for row in found]
    keep = cv2.dnn.NMSBoxes(boxes, confidences, threshold, .3)
    return [found[int(idx)] for idx in np.asarray(keep).reshape(-1)]


def _face_rows(image, path: Path, *, backend: str, folder: Path):
    import cv2
    import numpy as np
    h, w = image.shape[:2]
    if min(h, w) < 32:
        return [], 'none', None
    scale = min(1.0, 640.0 / max(h, w))
    preview = cv2.resize(image, (round(w * scale), round(h * scale))) if scale < 1 else image
    ph, pw = preview.shape[:2]
    detector_model = folder / MODEL_FILES['yunet'][0]
    face_model = folder / MODEL_FILES['sface'][0]
    if backend == 'cuda':
        try:
            session = _ort_session(detector_model, True)
            model_input = cv2.resize(preview, (320, 320))
            blob = model_input.astype(np.float32).transpose(2, 0, 1)[None]
            with GPU_INFERENCE_LOCK:
                outputs = session.run(None, {session.get_inputs()[0].name: blob})
            rows = _decode_yunet(outputs, [o.name for o in session.get_outputs()], (320, 320))
            factor_x, factor_y = w / 320, h / 320
            for row in rows:
                for j in range(0,14,2):
                    # bbox x/y, width/height and landmarks are x/y pairs
                    row[j] *= factor_x; row[j+1] *= factor_y
            return rows, 'cuda_onnxruntime', session
        except (ImportError, OSError, RuntimeError, ValueError) as exc:
            if backend == 'cuda':
                raise RuntimeError(f'CUDA face detection unavailable: {exc}') from exc
    detector = cv2.FaceDetectorYN.create(str(detector_model), '', (pw, ph), score_threshold=.85)
    detector.setInputSize((pw, ph))
    _, faces = detector.detect(preview)
    rows = [] if faces is None else faces.tolist()
    if scale < 1:
        for row in rows:
            for j in range(14):
                row[j] /= scale
    return rows, 'cpu_opencv', None


@lru_cache(maxsize=2)
def _cpu_recognizer(model_path: Path):
    import cv2
    return cv2.FaceRecognizerSF.create(str(model_path), '')


def _embedding(image, face: list, folder: Path, backend: str) -> list[float]:
    import cv2
    import numpy as np
    recognizer_path = folder / MODEL_FILES['sface'][0]
    if backend == 'cuda_onnxruntime':
        # SFace five-point alignment, followed by CUDA ONNX inference.
        src = np.asarray(face[4:14], dtype=np.float32).reshape(5, 2)
        dest = np.asarray(DEST_112, dtype=np.float32)
        matrix, _ = cv2.estimateAffinePartial2D(src, dest, method=cv2.LMEDS)
        if matrix is None:
            raise RuntimeError('Cannot align face landmarks')
        aligned = cv2.warpAffine(image, matrix, (112, 112))
        blob = aligned[:, :, ::-1].astype(np.float32).transpose(2, 0, 1)[None]
        session = _ort_session(recognizer_path, True)
        with GPU_INFERENCE_LOCK:
            feat = session.run(None, {session.get_inputs()[0].name: blob})[0]
    else:
        model = _cpu_recognizer(recognizer_path)
        aligned = model.alignCrop(image, np.asarray(face, dtype=np.float32))
        feat = model.feature(aligned)
    data = np.asarray(feat).reshape(-1).astype(np.float64)
    norm = float(np.linalg.norm(data))
    if norm < 1e-12:
        raise RuntimeError('Zero-length SFace embedding')
    return np.round(data / norm, 7).tolist()


def detect(image_path: str, *, timestamp_sec: float = 0.0, backend: str = 'cpu',
           directory: str = '', include_embeddings: bool = True,
           source_uuid: str = '') -> dict[str, Any]:
    """Read still image or requested video frame; deterministic observation UUIDs.

    Repeated exact samples produce the same UUID. Changed sampled timestamps or
    detection coordinates can change IDs; callers should reconcile rescans.
    """
    import cv2
    file = Path(image_path).expanduser()
    if not file.is_file():
        raise FileNotFoundError(file)
    if backend not in ('auto', 'cpu', 'cuda'):
        raise ValueError('backend must be auto, cpu or cuda')
    folder = Path(directory).expanduser() if directory else model_root()
    status = models_status(str(folder))
    if not all(x['sha256_valid'] for x in status['models'].values()):
        raise RuntimeError('YuNet/SFace model files missing or checksum invalid; run setup explicitly')
    suffix = file.suffix.lower()
    if suffix in {'.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.wmv'}:
        cap = cv2.VideoCapture(str(file))
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0, timestamp_sec) * 1000)
            ok, image = cap.read()
        finally:
            cap.release()
        if not ok:
            raise RuntimeError('Cannot decode video frame')
    else:
        image = cv2.imread(str(file))
        if image is None:
            raise ValueError('Cannot decode image')
    # Keep legacy 'auto' accepted, but it resolves to CPU and never probes CUDA.
    selected_backend = 'cpu' if backend == 'auto' else backend
    faces, engine, _ = _face_rows(image, file, backend=selected_backend, folder=folder)
    height, width = image.shape[:2]
    stable_source = source_uuid or str(file.resolve()).casefold()
    base = f'{stable_source}|{round(timestamp_sec*1000)}'
    results = []
    for idx, row in enumerate(sorted(faces, key=lambda r: (round(float(r[0])), round(float(r[1]))))):
        x, y, w, h = [float(v) for v in row[:4]]
        quantized = f'{int(round(x / 4))}:{int(round(y / 4))}:{int(round(w / 4))}:{int(round(h / 4))}'
        observation_id = str(uuid.uuid5(NAMESPACE, f'{base}|{quantized}|{idx}'))
        entry = {'face_uuid': observation_id, 'face_index': idx, 'person_uuid': None,
                 'timestamp_sec': round(float(timestamp_sec),3),
                 'bbox_xywh': [round(x,2),round(y,2),round(w,2),round(h,2)],
                 'landmarks': [[round(float(row[4+i*2]),2), round(float(row[5+i*2]),2)] for i in range(5)],
                 'detector_confidence': round(float(row[14]),5),
                 'embedding_model': 'opencv_sface_2021dec' if include_embeddings else None}
        if include_embeddings:
            try:
                entry['embedding'] = _embedding(image, row, folder, engine)
            except (RuntimeError, ValueError, cv2.error) as exc:
                if backend == 'auto' and engine == 'cuda_onnxruntime':
                    try:
                        entry['embedding'] = _embedding(image, row, folder, 'cpu_opencv')
                        entry['embedding_backend'] = 'cpu_opencv_fallback'
                    except Exception as fallback_exc:
                        entry['embedding_error'] = str(fallback_exc)
                elif backend == 'cuda':
                    raise RuntimeError(f'CUDA SFace unavailable: {exc}') from exc
                else:
                    entry['embedding_error'] = str(exc)
        results.append(entry)
    return {'schema': 'norm.media.faces.v1', 'source': str(file), 'source_uuid': stable_source,
            'timestamp_sec': timestamp_sec, 'dimensions': [width,height],
            'backend': engine, 'faces': results, 'count': len(results)}
