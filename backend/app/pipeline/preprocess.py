"""Image preprocessing: orientation, grayscale, contrast, denoise, deskew.

Pure Pillow + numpy (no OpenCV dependency). Deskew finds the rotation (±10°) that maximises the
variance of horizontal projection profiles, the classic text-line heuristic.
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageFilter, ImageOps

MAX_SIDE = 3000


def to_grayscale(img: Image.Image) -> Image.Image:
    return ImageOps.grayscale(img)


def limit_size(img: Image.Image, max_side: int = MAX_SIDE) -> Image.Image:
    w, h = img.size
    scale = max(w, h) / max_side
    if scale <= 1:
        return img
    return img.resize((int(w / scale), int(h / scale)), Image.Resampling.LANCZOS)


def estimate_skew(gray: Image.Image, max_angle: float = 10.0, step: float = 0.5) -> float:
    small = gray.copy()
    small.thumbnail((800, 800))
    arr = np.asarray(small, dtype=np.float32)
    binary = (arr < arr.mean()).astype(np.float32)  # ink = 1
    best_angle, best_score = 0.0, -1.0
    angle = -max_angle
    while angle <= max_angle:
        rotated = Image.fromarray((binary * 255).astype(np.uint8)).rotate(angle, expand=False, fillcolor=0)
        profile = np.asarray(rotated, dtype=np.float32).sum(axis=1)
        score = float(profile.var())
        if score > best_score:
            best_angle, best_score = angle, score
        angle += step
    return -best_angle  # the image's skew (positive = rotated counter-clockwise)


def deskew(gray: Image.Image) -> tuple[Image.Image, float]:
    angle = estimate_skew(gray)
    if abs(angle) < 0.3:
        return gray, 0.0
    return gray.rotate(-angle, expand=True, fillcolor=255, resample=Image.Resampling.BICUBIC), angle


def denoise(gray: Image.Image) -> Image.Image:
    return gray.filter(ImageFilter.MedianFilter(size=3))


def preprocess_image(img: Image.Image, *, do_deskew: bool = True) -> Image.Image:
    """The OCR input: oriented (EXIF), size-limited, grayscale, denoised, auto-contrast, deskewed."""
    img = ImageOps.exif_transpose(img) or img
    img = limit_size(img)
    gray = to_grayscale(img)
    gray = denoise(gray)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    if do_deskew:
        gray, _ = deskew(gray)
    return gray


def perceptual_hash(img: Image.Image, size: int = 8) -> str:
    """dHash (difference hash) as 16 hex chars: near-duplicate detection across channels."""
    gray = ImageOps.grayscale(img).resize((size + 1, size), Image.Resampling.LANCZOS)
    arr = np.asarray(gray, dtype=np.int16)
    diff = arr[:, 1:] > arr[:, :-1]
    bits = 0
    for b in diff.flatten():
        bits = (bits << 1) | int(b)
    return f"{bits:0{size * size // 4}x}"


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def quality_hints(img: Image.Image) -> dict[str, float | bool]:
    """Blur / glare / low-contrast hints (also used by the camera capture UI)."""
    small = ImageOps.grayscale(limit_size(img, 1200))
    gray = np.asarray(small, dtype=np.float32)
    smooth = np.asarray(small.filter(ImageFilter.GaussianBlur(1.0)), dtype=np.float32)
    gy, gx = np.gradient(smooth)
    grad = np.hypot(gx, gy)
    sharpness = float(
        np.percentile(grad, 99)
    )  # strong edges of text strokes; noise barely reaches the top percentile
    glare = float((gray > 250).mean())
    contrast = float(gray.std())
    return {
        "sharpness": sharpness,
        "blurry": sharpness < 35,
        "glare_ratio": glare,
        "glare": glare > 0.08,
        "contrast": contrast,
        "low_contrast": contrast < 25,
    }
