import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
import io
import cv2
import numpy as np
import torch
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from PIL import Image
from safetensors.torch import load_file
from transformers import (
    Mask2FormerConfig,
    Mask2FormerForUniversalSegmentation,
    Mask2FormerImageProcessor,
)
from dotenv import load_dotenv
import os
load_dotenv()


MODEL_ENV = os.getenv("MODEL_ENV")
CONFIG_ENV = os.getenv("CONFIG_ENV")
PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_MODEL_PATH = PROJECT_ROOT / "model.safetensors"
BASE_MODEL_ID = os.getenv("BASE_MODEL_ID")
EXTERNAL_MODEL_PATH = Path(
    r"C:\Users\Tci1\Desktop\Models\yolo\Transformer\content\drive"
    r"\MyDrive\mask2former\model.safetensors"
)

model: Mask2FormerForUniversalSegmentation | None = None
processor: Mask2FormerImageProcessor | None = None
labels: list[str] = []
score_threshold = float(os.getenv("SCORE_THRESHOLD","0.5"))
domains = os.getenv("DOMAINS", "http://localhost:8000")

origins = [origin.strip() for origin in domains.split(",") if origin.strip()]



def _model_path() -> Path:
    # configured = os.getenv(MODEL_ENV)
    # if configured:
    #     configured_path = Path(configured).expanduser()
    #     if configured_path.is_absolute():
    #         return configured_path
    #     project_relative = PROJECT_ROOT / configured_path
    #     if project_relative.is_file():
    #         return project_relative
    #     working_directory_relative = Path.cwd() / configured_path
    #     if working_directory_relative.is_file():
    #         return working_directory_relative
    #     if EXTERNAL_MODEL_PATH.is_file():
    #         return EXTERNAL_MODEL_PATH
    #     return project_relative
    # if DEFAULT_MODEL_PATH.is_file():
    #     return DEFAULT_MODEL_PATH
    # return EXTERNAL_MODEL_PATH
    return Path(MODEL_ENV)


def _labels() -> list[str]:
    configured = "Signature,Stamp,Handwriting"
    return [label.strip() for label in configured.split(",") if label.strip()]


def _artifact_path(weights_path: Path, name: str) -> Path | None: #-- check again----
    candidates = (
        PROJECT_ROOT / name,
        weights_path.parent / name,
    )
    return next((candidate for candidate in candidates if candidate.is_file()), None)


def _build_config(config_path: Path | None) -> Mask2FormerConfig:
    if config_path:
        if not config_path.is_file():
            raise FileNotFoundError(f"Mask2Former config does not exist: {config_path}")
        return Mask2FormerConfig.from_json_file(str(config_path))

    config = Mask2FormerConfig.from_pretrained(BASE_MODEL_ID)
    config.num_labels = 3
    config.id2label = {0: "Signature", 1: "Stamp", 2: "Handwriting"}
    config.label2id = {label: index for index, label in config.id2label.items()}
    return config


def load_model() -> tuple[Mask2FormerForUniversalSegmentation, Mask2FormerImageProcessor]:
    weights_path = _model_path()
    if not weights_path.is_file():
        raise FileNotFoundError(
            f"Mask2Former weights not found at {weights_path}. "
            f"Set {MODEL_ENV} to the .safetensors file."
        )

    config_value = CONFIG_ENV
    config_path = None
    if config_value:
        configured_config = Path(config_value).expanduser()
        config_path = (
            configured_config
            if configured_config.is_absolute()
            else PROJECT_ROOT / configured_config
        )
    if config_path is None:
        config_path = _artifact_path(weights_path, "config.json")
    config = _build_config(config_path)
    config.num_labels = 3
    config.id2label = {0: "Signature", 1: "Stamp", 2: "Handwriting"}
    config.label2id = {label: index for index, label in config.id2label.items()}
    loaded_model = Mask2FormerForUniversalSegmentation(config)
    state_dict = load_file(str(weights_path), device="cpu")
    incompatible = loaded_model.load_state_dict(state_dict, strict=False)
    missing_keys = [
        key
        for key in incompatible.missing_keys
        if not key.endswith("relative_position_index")
    ]
    unexpected_keys = [
        key
        for key in incompatible.unexpected_keys
        if not key.startswith("model.pixel_level_module.encoder.swin.layernorm.")
    ]
    if missing_keys or unexpected_keys:
        raise RuntimeError(
            "Mask2Former checkpoint does not match the configured architecture. "
            f"Missing keys: {missing_keys[:5]}; "
            f"unexpected keys: {unexpected_keys[:5]}. "
            f"Provide the matching {CONFIG_ENV}."
        )
    loaded_model.to("cpu").eval()

    processor_path = _artifact_path(weights_path, "preprocessor_config.json")
    if processor_path:
        image_processor = Mask2FormerImageProcessor.from_pretrained(
            str(processor_path.parent),
        )
    else:
        image_processor = Mask2FormerImageProcessor.from_pretrained(
            BASE_MODEL_ID,
        )
    return loaded_model, image_processor

CLASS_COLORS_BGR = {
    0: (212, 182, 6),    # 0: Handwriting -> Electric Cyan  (#06b6d4)
    1: (94, 63, 244),    # 1: Stamp       -> Crimson / Coral (#f43f5e)
    2: (129, 185, 16),   # 2: Signature   -> Vivid Emerald   (#10b981)
}


def _draw_segments(
    image: np.ndarray,
    segmentation: torch.Tensor,
    segments_info: list[dict[str, Any]],
) -> tuple[np.ndarray, int, float, str]:
    annotated = image.copy()
    detection_count = 0
    max_confidence = 0.0
    top_label = "NONE"

    for i, segment in enumerate(segments_info):
        score = float(segment.get("score", 0.0))
        if score < score_threshold:
            continue
        mask = segmentation[i].cpu().numpy() == 1

        if not np.any(mask):
            continue

        class_id = int(segment.get("label_id", -1))
        label = labels[class_id] if 0 <= class_id < len(labels) else str(class_id)
        color = CLASS_COLORS_BGR.get(class_id, (255, 255, 255))
        overlay = annotated.copy()
        overlay[mask] = color
        annotated = cv2.addWeighted(annotated, 0.65, overlay, 0.35, 0)

        ys, xs = np.where(mask)
        x, y = int(xs.min()), int(ys.min())
        cv2.rectangle(annotated, (x, y), (int(xs.max()), int(ys.max())), color, 2)
        cv2.putText(
            annotated,
            f"{label} {score:.2f}",
            (x, max(20, y - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            color,
            2,
            cv2.LINE_AA,
        )

        detection_count += 1
        if score > max_confidence:
            max_confidence = score
            top_label = label

    return annotated, detection_count, max_confidence, top_label


@asynccontextmanager
async def lifespan(app: FastAPI):
    global model, processor, labels, score_threshold
    labels = _labels()
    score_threshold = float(os.getenv("SCORE_THRESHOLD","0.5"))
    if not 0 <= score_threshold <= 1:
        raise ValueError(f"Threshold must be between 0 and 1")
    print(f"Loading Mask2Former from {_model_path()} on CPU...")
    model, processor = load_model()
    print("Mask2Former loaded successfully into memory.")
    yield
    model = None
    processor = None



app = FastAPI(title="Mask2Former Inference Service", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Detections", "X-Max-Confidence", "X-Top-Label"],
)


@app.get("/")
async def read_index():
    return FileResponse("index.html")


@app.get("/health")
def health():
    return {
        "status": "healthy" if model is not None else "loading",
        "device": "cpu",
        "model": "Mask2Former",
        "weights": str(_model_path()),
        "score_threshold": score_threshold,
    }


@app.post("/predict", responses={200: {"content": {"image/jpeg": {}}}})
def predict(file: UploadFile = File(...)):
    if model is None or processor is None:
        raise HTTPException(status_code=503, detail="Model is not loaded.")
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Uploaded file is not an image.")

    contents = file.file.read()

    is_tiff = file.content_type in ("image/tiff", "image/x-tiff") or (
        file.filename and file.filename.lower().endswith((".tif", ".tiff"))
    )


    if is_tiff:
        try:
            with Image.open(io.BytesIO(contents)) as pil_img:
                # Handle multi-page TIFFs by selecting the first frame
                pil_img.seek(0)

                # Normalize 16-bit / 32-bit images to 8-bit uint8
                if pil_img.mode in ("I;16", "I", "F"):
                    arr = np.array(pil_img, dtype=np.float32)
                    arr_min, arr_max = arr.min(), arr.max()
                    if arr_max > arr_min:
                        arr = (arr - arr_min) / (arr_max - arr_min) * 255.0
                    else:
                        arr = np.zeros_like(arr)
                    pil_img = Image.fromarray(arr.astype(np.uint8))

                # Convert RGBA, CMYK, or Grayscale into 8-bit RGB
                pil_img = pil_img.convert("RGB")
                rgb_image = np.array(pil_img)
                image = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2BGR)

        except Exception as e:
            raise HTTPException(
                status_code=400, detail=f"Failed to decode TIFF image: {e}"
            )
    else:
        # Standard decoding for PNG, JPEG, WebP, etc.
        image = cv2.imdecode(np.frombuffer(contents, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise HTTPException(status_code=400, detail="Invalid image content.")
        rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)



    inputs = processor(images=Image.fromarray(rgb_image), return_tensors="pt")
    with torch.inference_mode():
        outputs = model(**inputs)

    processed = processor.post_process_instance_segmentation(
        outputs,
        threshold=score_threshold,
        target_sizes=[rgb_image.shape[:2]],
        return_binary_maps=True
    )[0]
    annotated, count, confidence, top_label = _draw_segments(
        image, processed["segmentation"], processed["segments_info"]
    )
    success, encoded_img = cv2.imencode(".jpg", annotated)
    if not success:
        raise HTTPException(status_code=500, detail="Failed to encode output image.")

    return Response(
        content=encoded_img.tobytes(),
        media_type="image/jpeg",
        headers={
            "X-Detections": str(count),
            "X-Max-Confidence": f"{confidence:.6f}",
            "X-Top-Label": top_label,
        },
    )

@app.get("/api/config")
async def get_frontend_config():
    return {
        # Defaults to empty string or relative path if hosted together
        "backendUrl": os.getenv("BACKEND_URL", "http://localhost:8000"),
        "defaultCustomer": os.getenv("DEFAULT_CUSTOMER_ID", "CUST-8492"),
    }