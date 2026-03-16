"""
Face detection utilities: detect faces, compute tilt angle, crop around face center.
Uses OpenCV's Haar cascades (no extra dependencies).
"""

import math
import cv2
import numpy as np
from PIL import Image, ImageOps
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass


def pil_to_cv(pil_img: Image.Image) -> np.ndarray:
    """Convert PIL Image to OpenCV BGR array."""
    rgb = np.array(pil_img.convert('RGB'))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def cv_to_pil(cv_img: np.ndarray) -> Image.Image:
    """Convert OpenCV BGR array to PIL Image."""
    rgb = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
    return Image.fromarray(rgb)


def _run_cascade(gray: np.ndarray, min_size: int = 30) -> list:
    """Run face cascades and return all detected faces."""
    cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
    faces = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(min_size, min_size))

    if len(faces) == 0:
        faces = cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=3, minSize=(max(20, min_size - 10), max(20, min_size - 10)))

    if len(faces) == 0:
        profile_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_profileface.xml')
        faces = profile_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=3, minSize=(min_size, min_size))

    return list(faces) if len(faces) > 0 else []


def detect_face(cv_img: np.ndarray) -> tuple | None:
    """
    Detect the largest face in the image with false-positive filtering.
    In tall portrait images, prefers faces in the upper portion (where heads
    typically are) over false positives on clothing/body parts lower down.
    Returns (x, y, w, h) of the face bounding box, or None if no face found.
    """
    h_img, w_img = cv_img.shape[:2]
    gray = cv2.cvtColor(cv_img, cv2.COLOR_BGR2GRAY)
    is_tall_portrait = (h_img / max(w_img, 1)) > 1.3

    faces = _run_cascade(gray)

    if len(faces) == 0:
        return None

    if is_tall_portrait and len(faces) >= 1:
        # In tall portraits (full-body shots), faces detected in the lower
        # half are often false positives on clothing/knee pads/etc.
        # Strategy: prefer faces in the upper 45% of the image.
        upper_faces = [f for f in faces if f[1] + f[3] / 2 < h_img * 0.45]

        if upper_faces:
            # Pick the largest face in the upper portion
            areas = [w * h for (x, y, w, h) in upper_faces]
            idx = np.argmax(areas)
            return tuple(upper_faces[idx])

        # No faces in upper portion — try detecting just the upper half
        # at higher resolution (the face may be too small for full-image detection)
        upper_half = gray[:h_img // 2, :]
        upper_faces_retry = _run_cascade(upper_half, min_size=15)
        if upper_faces_retry:
            areas = [w * h for (x, y, w, h) in upper_faces_retry]
            idx = np.argmax(areas)
            return tuple(upper_faces_retry[idx])

        # Still nothing in upper half — the lower detection might be valid
        # (e.g., person crouching), but only trust it if it's reasonably
        # large (>5% of image width) to filter out tiny false positives
        large_faces = [f for f in faces if f[2] > w_img * 0.05]
        if large_faces:
            areas = [w * h for (x, y, w, h) in large_faces]
            idx = np.argmax(areas)
            return tuple(large_faces[idx])

        # Fall back to no detection rather than a likely false positive
        return None

    # Non-portrait or simple case: return the largest face
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


def process_photo(image_path: str, max_size: int = 1200,
                  target_cell_ratio: float = None) -> Image.Image:
    """
    Full photo processing pipeline:
    1. Load and auto-orient
    2. Detect face
    3. Compute and correct tilt angle
    4. Pre-crop to match target cell aspect ratio, centered on the face
    5. Return processed image with face metadata

    target_cell_ratio: width/height ratio of the CSS grid cell this photo
        will be placed in. If provided, the pre-crop will match this ratio
        so that object-fit:cover does minimal additional cropping.
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

    # Count total faces for multi-person detection
    gray_small = cv2.cvtColor(cv_small, cv2.COLOR_BGR2GRAY)
    all_faces = _run_cascade(gray_small)
    num_faces = len(all_faces)

    angle = 0.0
    face_center = None

    face_box = None
    if face:
        fx, fy, fw, fh = face
        # Only compute rotation angle if face is large enough relative to image
        # (small faces give unreliable eye detection)
        face_area_ratio = (fw * fh) / (small.size[0] * small.size[1])
        if face_area_ratio > 0.02:  # face occupies >2% of image
            angle = compute_face_angle(cv_small, face)
            # Cap rotation at 12 degrees — larger angles are likely false positives
            if abs(angle) > 12:
                angle = 0.0

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
            # Only use post-rotation face if it's near the original position
            # (prevents switching to a different face after rotation)
            orig_cx = face_center[0] / (img.size[0])
            new_cx = (fx2 + fw2 / 2) / small2.size[0]
            orig_cy = face_center[1] / (img.size[1])
            new_cy = (fy2 + fh2 / 2) / small2.size[1]
            if abs(orig_cx - new_cx) < 0.3 and abs(orig_cy - new_cy) < 0.3:
                face_center = (
                    int((fx2 + fw2 / 2) * scale2),
                    int((fy2 + fh2 / 2) * scale2)
                )
                face_box = (
                    int(fx2 * scale2), int(fy2 * scale2),
                    int(fw2 * scale2), int(fh2 * scale2)
                )

    # Pre-crop around face to ensure the face is always visible after
    # object-fit:cover crops the image to fit the grid cell.
    #
    # KEY INSIGHT: After pre-cropping, object-fit:cover will further crop
    # the image to match the cell's aspect ratio. If the pre-cropped image
    # has a very different aspect ratio than the cell, the face can get cut.
    #
    # IMPROVED STRATEGY:
    # 1. Define the face region (face box + hair/context margins) that MUST
    #    be visible in the final rendered cell.
    # 2. Compute a crop rectangle that:
    #    a. Matches the target cell aspect ratio (so object-fit:cover does
    #       NO additional cropping)
    #    b. Is centered on the face region
    #    c. Is large enough to contain the entire face region
    #    d. Stays within image bounds

    # RELIABILITY CHECK: Validate face detection before cropping.
    # Uses eye detection to confirm real faces and reject false positives
    # (textures, text, metalwork that look face-like to Haar cascades).
    if face_center and face_box:
        w, h = img.size
        fb_x, fb_y, fb_w, fb_h = face_box
        face_size_ratio = max(fb_w / w, fb_h / h)

        # For small-to-medium faces, require eye detection to confirm
        # it's a real face (not a false positive on environment/text)
        skip_crop = False
        if face_size_ratio < 0.15:
            eyes = detect_eyes(cv_small, face)
            if len(eyes) == 0:
                # No eyes detected — likely false positive
                skip_crop = True

        # For group photos (3+ faces), apply eye check even for slightly
        # larger faces, since the "largest face" may be a false positive
        # on background elements (gym walls, windows, signs)
        if num_faces >= 3 and face_size_ratio < 0.20:
            eyes = detect_eyes(cv_small, face)
            if len(eyes) == 0:
                skip_crop = True

        if skip_crop:
            face_center = None
            face_box = None

    if face_center and face_box:
        w, h = img.size
        fb_x, fb_y, fb_w, fb_h = face_box

        # Define the "face region" we must keep visible:
        # face box + generous margin for hair and context
        margin_x = int(fb_w * 0.8)
        margin_y_top = int(fb_h * 1.2)   # extra room above for hair
        margin_y_bot = int(fb_h * 0.6)   # room below chin/neck
        face_left = max(0, fb_x - margin_x)
        face_top = max(0, fb_y - margin_y_top)
        face_right = min(w, fb_x + fb_w + margin_x)
        face_bottom = min(h, fb_y + fb_h + margin_y_bot)
        face_region_w = face_right - face_left
        face_region_h = face_bottom - face_top

        # Anchor point: center of the face region (shifted up slightly
        # to give more headroom for hair)
        anchor_x = (face_left + face_right) // 2
        anchor_y = (face_top + face_bottom) // 2 - int(fb_h * 0.1)

        if target_cell_ratio is not None and target_cell_ratio > 0:
            # Match the target cell aspect ratio so object-fit:cover
            # does NO additional cropping.
            # Start with a crop that just contains the face region,
            # then expand to match the target ratio.
            crop_w = face_region_w
            crop_h = face_region_h
            current_ratio = crop_w / max(crop_h, 1)

            if current_ratio < target_cell_ratio:
                # Need wider crop: expand width
                crop_w = int(crop_h * target_cell_ratio)
            else:
                # Need taller crop: expand height
                crop_h = int(crop_w / target_cell_ratio)

            # Add extra context while maintaining the target ratio.
            # Scale depends on face size relative to image — small faces
            # (full-body shots) need less context to keep the face prominent.
            face_size_ratio = max(fb_w / w, fb_h / h)
            if face_size_ratio < 0.1:
                context_scale = 1.15  # small face: tight crop
            elif face_size_ratio < 0.2:
                context_scale = 1.25  # medium face
            else:
                context_scale = 1.4   # large/close-up face: more context
            crop_w = int(crop_w * context_scale)
            crop_h = int(crop_h * context_scale)

            # Ensure we don't exceed image dimensions
            # If we do, shrink the other dimension to maintain ratio
            if crop_w > w:
                crop_w = w
                crop_h = int(crop_w / target_cell_ratio)
            if crop_h > h:
                crop_h = h
                crop_w = int(crop_h * target_cell_ratio)
            crop_w = min(crop_w, w)
            crop_h = min(crop_h, h)

        else:
            # No target ratio: use the old approach (generous crop)
            crop_w = max(face_region_w, int(w * 0.7))
            crop_h = max(face_region_h, int(h * 0.7))
            crop_w = min(crop_w, w)
            crop_h = min(crop_h, h)

        # Center the crop on the anchor point
        left = anchor_x - crop_w // 2
        top = anchor_y - crop_h // 2

        # Clamp to image bounds
        left = max(0, min(left, w - crop_w))
        top = max(0, min(top, h - crop_h))
        right = left + crop_w
        bottom = top + crop_h

        # SAFETY CHECK: ensure the face region is fully visible in the crop.
        # If not, shift the crop to include it (may break the aspect ratio
        # slightly, but face visibility takes priority).
        if left > face_left:
            left = max(0, face_left)
            right = left + crop_w
            if right > w:
                right = w
        if top > face_top:
            top = max(0, face_top)
            bottom = top + crop_h
            if bottom > h:
                bottom = h
        if right < face_right:
            right = min(w, face_right)
            left = right - crop_w
            if left < 0:
                left = 0
        if bottom < face_bottom:
            bottom = min(h, face_bottom)
            top = bottom - crop_h
            if top < 0:
                top = 0

        # Only crop if it meaningfully removes content
        crop_ratio_w = (right - left) / w
        crop_ratio_h = (bottom - top) / h
        if crop_ratio_w < 0.98 or crop_ratio_h < 0.98:
            cropped = img.crop((left, top, right, bottom))

            # POST-CROP VALIDATION: verify a REAL face (not a false positive)
            # is visible in the cropped result. Uses multiple checks:
            # 1. High-confidence cascade detection (minNeighbors=8)
            # 2. Eye detection to confirm it's a real face
            # 3. Size and position checks
            verify = cropped.copy()
            verify.thumbnail((400, 400), Image.LANCZOS)
            cv_verify = pil_to_cv(verify)
            vimg_w, vimg_h = verify.size
            face_ok = False

            # Method 1: Strict cascade (high confidence)
            gray_v = cv2.cvtColor(cv_verify, cv2.COLOR_BGR2GRAY)
            cascade_v = cv2.CascadeClassifier(
                cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
            strict_min = max(int(vimg_w * 0.10), 20)
            strict_faces = cascade_v.detectMultiScale(
                gray_v, scaleFactor=1.1, minNeighbors=8,
                minSize=(strict_min, strict_min))
            if len(strict_faces) > 0:
                face_ok = True

            # Method 2: Regular detection + eye confirmation
            if not face_ok:
                face_verify = detect_face(cv_verify)
                if face_verify is not None:
                    vx, vy, vw, vh = face_verify
                    if vw > vimg_w * 0.10:
                        # Check for eyes to confirm real face
                        eyes = detect_eyes(cv_verify, face_verify)
                        if len(eyes) >= 1:
                            face_ok = True
                        # Large face (>20% of crop) is likely real even without eyes
                        elif vw > vimg_w * 0.20:
                            face_ok = True

            if face_ok:
                img = cropped
                # Recompute face anchor in cropped coords for object-position
                new_w, new_h = img.size
                new_anchor_x = anchor_x - left
                new_anchor_y = anchor_y - top
                x_pct = int(new_anchor_x / new_w * 100)
                y_pct = int(new_anchor_y / new_h * 100)
            else:
                # Face not found or at edge of crop — likely false positive.
                # Fall back to no crop, default positioning.
                x_pct = 50
                y_pct = 40
        else:
            # No meaningful crop: use face position as-is
            x_pct = int(anchor_x / w * 100)
            y_pct = int(anchor_y / h * 100)

        # Clamp object-position percentages
        x_pct = max(20, min(80, x_pct))
        y_pct = max(15, min(75, y_pct))
    else:
        x_pct = 50
        y_pct = 40  # Default: show center-upper portion

    # Resize after cropping
    img.thumbnail((max_size, max_size), Image.LANCZOS)

    return img, (x_pct, y_pct), angle
