"""
Face detection utilities: detect faces, compute tilt angle, crop around face center.
Uses OpenCV's Haar cascades (no extra dependencies).
"""

import math
import cv2
import numpy as np
from PIL import Image, ImageOps


def pil_to_cv(pil_img: Image.Image) -> np.ndarray:
    """Convert PIL Image to OpenCV BGR array."""
    rgb = np.array(pil_img.convert('RGB'))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def cv_to_pil(cv_img: np.ndarray) -> Image.Image:
    """Convert OpenCV BGR array to PIL Image."""
    rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def detect_face(cv_img: np.ndarray) -> tuple | None:
    """
    Detect the largest face in the image.
    Returns (x, y, w, h) of the face bounding box, or None if no face found.
    """
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)

    # Try frontal face first
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30))

    if len(faces) == 0:
        # Try with more lenient settings
        faces = cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=3, minSize=(20, 20))

    if len(faces) == 0:
        # Try profile face
        profile_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_profileface.xml')
        faces = profile_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3, minSize=(30, 30))

    if len(faces) == 0:
        return None

    # Return the largest face
    areas = [w * h for (x, y, w, h) in faces]
    idx = np.argmax(areas)
    return tuple(faces[idx])


def detect_eyes(cv_img: np.ndarray, face_rect: tuple) -> list:
    """
    Detect eyes within a face region.
    Returns list of (x, y, w, h) in image coordinates.
    """
    x, y, w, h = face_rect
    face_roi = cv_img[y:y+h, x:x+w]
    gray_roi = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)

    eye_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_eye.xml')
    eyes = eye_cascade.detectMultiScale(gray_roi, scaleFactor=1.1, minNeighbors=5, minSize=(10, 10))

    # Convert to image coordinates and filter to upper half of face
    result = []
    for (ex, ey, ew, eh) in eyes:
        if ey < h * 0.6:  # eyes should be in upper 60% of face
            result.append((x + ex, y + ey, ew, eh))

    return result


def compute_face_angle(cv_img: np.ndarray, face_rect: tuple) -> float:
    """
    Estimate face tilt angle in degrees using eye positions.
    Returns angle to rotate clockwise to straighten. Returns 0 if can't determine.
    """
    eyes = detect_eyes(cv_img, face_rect)
    if len(eyes) < 2:
        return 0.0

    # Sort eyes by x coordinate (left eye, right eye)
    eyes_sorted = sorted(eyes, key=lambda e: e[0])
    left_eye = eyes_sorted[0]
    right_eye = eyes_sorted[-1]

    # Eye centers
    lx = left_eye[0] + left_eye[2] / 2
    ly = left_eye[1] + left_eye[3] / 2
    rx = right_eye[0] + right_eye[2] / 2
    ry = right_eye[1] + right_eye[3] / 2

    # Angle between eyes
    angle = math.degrees(math.atan2(ry - ly, rx - lx))

    # Only correct if angle is reasonable (< 30 degrees)
    if abs(angle) > 30:
        return 0.0

    return angle


def rotate_image(pil_img: Image.Image, angle: float) -> Image.Image:
    """Rotate image by angle degrees (counterclockwise) to straighten."""
    if abs(angle) < 1.0:
        return pil_img
    # Use expand=True to avoid cropping, then we'll re-crop later
    return pil_img.rotate(-angle, resample=Image.BICUBIC, expand=True, fillcolor=(255, 255, 255))


def face_center_crop(pil_img: Image.Image, target_ratio: float = None) -> Image.Image:
    """
    Crop image centered on the detected face, leaving generous headroom.
    If no face detected, returns center crop.
    target_ratio: if set, crop to this w/h ratio
    """
    cv_img = pil_to_cv(pil_img)
    face = detect_face(cv_img)
    w, h = pil_img.size

    if face is not None:
        fx, fy, fw, fh = face
        # Face center
        cx = fx + fw // 2
        cy = fy + fh // 2
        # Add generous headroom above face (30% of face height)
        cy = cy - int(fh * 0.15)
    else:
        # Default to upper-center (assumes subject is typically in upper portion)
        cx = w // 2
        cy = h * 4 // 10

    return pil_img, (cx, cy)


def process_photo(image_path: str, max_size: int = 1200) -> Image.Image:
    """
    Full photo processing pipeline:
    1. Load and auto-orient
    2. Detect face
    3. Compute and correct tilt angle
    4. Return processed image with face metadata
    """
    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img)

    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')

    # Detect face and compute angle on a smaller version for speed
    small = img.copy()
    small.thumbnail((800, 800), Image.LANCZOS)
    scale = img.size[0] / small.size[0]

    cv_small = pil_to_cv(small)
    face = detect_face(cv_small)

    angle = 0.0
    face_center = None

    face_box = None
    if face:
        angle = compute_face_angle(cv_small, face)
        fx, fy, fw, fh = face
        # Scale face coordinates back to original
        face_center = (
            int((fx + fw / 2) * scale),
            int((fy + fh / 2) * scale)
        )
        face_box = (
            int(fx * scale), int(fy * scale),
            int(fw * scale), int(fh * scale)
        )

    # Rotate if needed
    if abs(angle) >= 1.5:
        img = rotate_image(img, angle)
        # Redetect face after rotation
        small2 = img.copy()
        small2.thumbnail((800, 800), Image.LANCZOS)
        scale2 = img.size[0] / small2.size[0]
        cv_small2 = pil_to_cv(small2)
        face2 = detect_face(cv_small2)
        if face2:
            fx2, fy2, fw2, fh2 = face2
            face_center = (
                int((fx2 + fw2 / 2) * scale2),
                int((fy2 + fh2 / 2) * scale2)
            )
            face_box = (
                int(fx2 * scale2), int(fy2 * scale2),
                int(fw2 * scale2), int(fh2 * scale2)
            )

    # Resize
    img.thumbnail((max_size, max_size), Image.LANCZOS)

    # Compute CSS object-position based on face location
    # Goal: position the crop so face + hair are visible and centered in view
    if face_center and face_box:
        orig_w, orig_h = img.size[0] * scale, img.size[1] * scale
        fb_x, fb_y, fb_w, fb_h = face_box

        # Use the TOP of the face box minus hair offset (50% of face height above)
        # This ensures forehead and hair are included
        hair_top_y = max(0, fb_y - int(fb_h * 0.5))

        # The "ideal anchor" is between the hair top and face center
        # This gives a balanced view with hair visible
        anchor_y = (hair_top_y + face_center[1]) / 2
        anchor_x = face_center[0]

        x_pct = int(anchor_x / orig_w * 100)
        y_pct = int(anchor_y / orig_h * 100)

        # Clamp
        x_pct = max(5, min(95, x_pct))
        y_pct = max(5, min(85, y_pct))
    else:
        x_pct = 50
        y_pct = 30  # Default: show upper portion

    return img, (x_pct, y_pct), angle
