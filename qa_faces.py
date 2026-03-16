#!/usr/bin/env python3
"""
QA diagnostic script for face-aware cropping in yearbook collage generator.

For each photo, generates a visual debug image showing:
  1. Original image with face bounding box and margins
  2. Pre-cropped result with face box
  3. Simulated grid cell crop (object-fit:cover) for both row 1 and row 2 aspect ratios

Usage:
    python3 qa_faces.py "Ariel Chen"
    python3 qa_faces.py all
    python3 qa_faces.py "Ariel Chen" --photo-index 1   # specific photo only
"""

import argparse
import os
import sys
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from face_utils import pil_to_cv, detect_face, process_photo

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.heic', '.heif'}

# Grid cell aspect ratios (width / height) based on the CSS grid layout:
# Page: 8.5x11in, padding 0.45in top/bottom + 0.55in left/right
# Usable: ~7.4in wide, and the photo grid takes roughly 6.5in of height
# Grid gap: 0.1in
# Row 1: 3 cells spanning 2 cols each, row height = 1.2fr
# Row 2: 2 cells spanning 3 cols each, row height = 1fr
# grid-template-rows: 1.2fr 1fr => row1 gets 1.2/2.2 of total, row2 gets 1.0/2.2

GRID_WIDTH_IN = 7.4   # usable width (8.5 - 2*0.55)
GRID_GAP_IN = 0.1
GRID_TOTAL_HEIGHT_IN = 6.5  # approximate grid height

# Row heights (proportional with gap)
ROW1_HEIGHT = GRID_TOTAL_HEIGHT_IN * (1.2 / 2.2) - GRID_GAP_IN / 2
ROW2_HEIGHT = GRID_TOTAL_HEIGHT_IN * (1.0 / 2.2) - GRID_GAP_IN / 2

# Cell widths
ROW1_CELL_WIDTH = (GRID_WIDTH_IN - 2 * GRID_GAP_IN) / 3   # 3 cells with 2 gaps
ROW2_CELL_WIDTH = (GRID_WIDTH_IN - 1 * GRID_GAP_IN) / 2   # 2 cells with 1 gap

ROW1_RATIO = ROW1_CELL_WIDTH / ROW1_HEIGHT  # ~0.70 portrait-ish
ROW2_RATIO = ROW2_CELL_WIDTH / ROW2_HEIGHT  # ~1.24 landscape-ish

# Photo positions in the grid (after reordering in collage.py):
# With 5 photos reordered as [4, 0, 2, 3, 1]:
# Position 0 (photo[4]): row 1, col 1-2 => ROW1_RATIO
# Position 1 (photo[0]): row 1, col 3-4 => ROW1_RATIO
# Position 2 (photo[2]): row 1, col 5-6 => ROW1_RATIO
# Position 3 (photo[3]): row 2, col 1-3 => ROW2_RATIO
# Position 4 (photo[1]): row 2, col 4-6 => ROW2_RATIO

def get_cell_ratio_for_position(pos_index: int, total_photos: int) -> float:
    """Return target cell aspect ratio (w/h) for a given grid position."""
    if total_photos >= 5:
        if pos_index < 3:
            return ROW1_RATIO
        else:
            return ROW2_RATIO
    elif total_photos == 4:
        # 2x2 grid, each spanning 3 cols
        return ROW2_RATIO
    elif total_photos == 3:
        return ROW1_RATIO
    elif total_photos == 2:
        return ROW2_RATIO
    else:
        # Single photo spanning cols 2-5
        return (GRID_WIDTH_IN * 4 / 6) / GRID_TOTAL_HEIGHT_IN


def simulate_object_fit_cover(img_w: int, img_h: int, cell_ratio: float,
                              obj_pos_x_pct: float, obj_pos_y_pct: float):
    """
    Simulate CSS object-fit:cover cropping.

    Returns (crop_x, crop_y, crop_w, crop_h) - the region of the image
    that would be visible in the cell.
    """
    img_ratio = img_w / img_h

    if img_ratio > cell_ratio:
        # Image is wider than cell: crop sides
        visible_w = int(img_h * cell_ratio)
        visible_h = img_h
        # object-position X% determines where we anchor horizontally
        max_offset = img_w - visible_w
        crop_x = int(max_offset * obj_pos_x_pct / 100)
        crop_y = 0
    else:
        # Image is taller than cell: crop top/bottom
        visible_w = img_w
        visible_h = int(img_w / cell_ratio)
        # object-position Y% determines where we anchor vertically
        max_offset = img_h - visible_h
        crop_x = 0
        crop_y = int(max_offset * obj_pos_y_pct / 100)

    return crop_x, crop_y, visible_w, visible_h


def draw_face_box(draw, face_rect, color='red', width=3, label=None):
    """Draw a face bounding box on the image."""
    if face_rect is None:
        return
    x, y, w, h = face_rect
    draw.rectangle([x, y, x + w, y + h], outline=color, width=width)
    if label:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
        except Exception:
            font = ImageFont.load_default()
        draw.text((x, y - 18), label, fill=color, font=font)


def draw_margin_box(draw, face_rect, color='lime', width=2):
    """Draw the margin region (face + hair margin) on the image."""
    if face_rect is None:
        return
    x, y, w, h = face_rect
    margin_x = int(w * 0.8)
    margin_y_top = int(h * 1.2)
    margin_y_bot = int(h * 0.6)
    draw.rectangle(
        [x - margin_x, y - margin_y_top, x + w + margin_x, y + h + margin_y_bot],
        outline=color, width=width
    )


def detect_face_on_image(pil_img):
    """Detect face and return (face_rect, scale) or (None, 1.0)."""
    small = pil_img.copy()
    small.thumbnail((800, 800), Image.LANCZOS)
    scale = pil_img.size[0] / small.size[0]
    cv_small = pil_to_cv(small)
    face = detect_face(cv_small)
    if face:
        fx, fy, fw, fh = face
        return (int(fx * scale), int(fy * scale),
                int(fw * scale), int(fh * scale)), scale
    return None, scale


def create_diagnostic(image_path: str, photo_index: int, total_photos: int,
                      grid_position: int, child_name: str) -> Image.Image:
    """
    Create a diagnostic image for a single photo showing:
    - Panel 1: Original with face box + margin box
    - Panel 2: Pre-cropped result (current algorithm) with face box
    - Panel 3: Simulated object-fit:cover for the target cell
    """
    # Load original
    orig = Image.open(image_path)
    orig = ImageOps.exif_transpose(orig)
    if orig.mode in ('RGBA', 'P'):
        orig = orig.convert('RGB')

    cell_ratio = get_cell_ratio_for_position(grid_position, total_photos)
    cell_type = "Row1 (portrait)" if cell_ratio < 1.0 else "Row2 (landscape)"

    # Detect face on original
    face_orig, _ = detect_face_on_image(orig)

    # Process with current algorithm (passing the target cell ratio)
    processed_img, (x_pct, y_pct), angle = process_photo(image_path, max_size=1200,
                                                          target_cell_ratio=cell_ratio)

    # Detect face on processed
    face_proc, _ = detect_face_on_image(processed_img)

    # Simulate object-fit:cover
    proc_w, proc_h = processed_img.size
    cx, cy, cw, ch = simulate_object_fit_cover(proc_w, proc_h, cell_ratio,
                                                x_pct, y_pct)

    # Create panels at uniform height
    panel_h = 500
    spacing = 20

    def resize_to_height(img, h):
        ratio = h / img.size[1]
        return img.resize((int(img.size[0] * ratio), h), Image.LANCZOS)

    # Panel 1: Original with face box
    p1 = resize_to_height(orig, panel_h)
    p1_draw = ImageDraw.Draw(p1)
    if face_orig:
        scale1 = panel_h / orig.size[1]
        fb1 = tuple(int(v * scale1) for v in face_orig)
        draw_face_box(p1_draw, fb1, color='red', width=3, label='Face')
        draw_margin_box(p1_draw, fb1, color='lime', width=2)

    # Panel 2: Pre-cropped with face box
    p2 = resize_to_height(processed_img, panel_h)
    p2_draw = ImageDraw.Draw(p2)
    if face_proc:
        scale2 = panel_h / processed_img.size[1]
        fb2 = tuple(int(v * scale2) for v in face_proc)
        draw_face_box(p2_draw, fb2, color='red', width=3, label='Face')
    # Draw object-position crosshair
    op_x = int(x_pct / 100 * p2.size[0])
    op_y = int(y_pct / 100 * p2.size[1])
    p2_draw.line([(op_x - 15, op_y), (op_x + 15, op_y)], fill='cyan', width=2)
    p2_draw.line([(op_x, op_y - 15), (op_x, op_y + 15)], fill='cyan', width=2)

    # Panel 3: Simulated cell crop
    # Crop the processed image as object-fit:cover would
    crop_region = processed_img.crop((cx, cy, cx + cw, cy + ch))
    # Scale to a reasonable display size
    cell_display_h = panel_h
    cell_display_w = int(cell_display_h * cell_ratio)
    p3 = crop_region.resize((cell_display_w, cell_display_h), Image.LANCZOS)
    p3_draw = ImageDraw.Draw(p3)
    # Detect face in cropped view
    face_cell, _ = detect_face_on_image(p3)
    if face_cell:
        draw_face_box(p3_draw, face_cell, color='yellow', width=3, label='Face')

    # Also draw crop region on panel 2
    scale2 = panel_h / processed_img.size[1]
    cx_s = int(cx * scale2)
    cy_s = int(cy * scale2)
    cw_s = int(cw * scale2)
    ch_s = int(ch * scale2)
    p2_draw.rectangle([cx_s, cy_s, cx_s + cw_s, cy_s + ch_s],
                      outline='yellow', width=2)

    # Compose final diagnostic
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
        font_small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 13)
    except Exception:
        font = ImageFont.load_default()
        font_small = font

    label_h = 60
    total_w = p1.size[0] + spacing + p2.size[0] + spacing + p3.size[0]
    total_h = panel_h + label_h + 40  # top margin + labels

    canvas = Image.new('RGB', (total_w + 40, total_h + 20), (40, 40, 40))
    draw = ImageDraw.Draw(canvas)

    # Title
    fname = os.path.basename(image_path)
    title = f"{child_name} - Photo {photo_index + 1}/{total_photos} - {fname}"
    draw.text((20, 8), title, fill='white', font=font)

    # Paste panels
    y_offset = 35
    x = 20

    # Panel 1
    canvas.paste(p1, (x, y_offset))
    draw.text((x, y_offset + panel_h + 5),
              f"Original ({orig.size[0]}x{orig.size[1]})",
              fill='white', font=font_small)
    face_str = f"Face: {face_orig}" if face_orig else "No face detected"
    draw.text((x, y_offset + panel_h + 22), face_str, fill='#aaa', font=font_small)
    x += p1.size[0] + spacing

    # Panel 2
    canvas.paste(p2, (x, y_offset))
    draw.text((x, y_offset + panel_h + 5),
              f"Pre-cropped ({proc_w}x{proc_h}) obj-pos: {x_pct}% {y_pct}%",
              fill='white', font=font_small)
    draw.text((x, y_offset + panel_h + 22),
              f"Yellow box = cell crop region | Angle: {angle:.1f}deg",
              fill='#aaa', font=font_small)
    x += p2.size[0] + spacing

    # Panel 3
    canvas.paste(p3, (x, y_offset))
    draw.text((x, y_offset + panel_h + 5),
              f"Cell view ({cell_type}, ratio={cell_ratio:.2f})",
              fill='white', font=font_small)
    face_visible = "FACE VISIBLE" if face_cell else "FACE NOT VISIBLE / CLIPPED"
    color = '#0f0' if face_cell else '#f00'
    draw.text((x, y_offset + panel_h + 22), face_visible, fill=color, font=font_small)

    return canvas


def get_child_photos(child_name: str):
    """Get list of photo paths for a child."""
    children_dir = "/Users/asb/Downloads/Individual Child_s Photos"
    baby_dir = "/Users/asb/Downloads/Baby Photos"

    child_path = os.path.join(children_dir, child_name)
    if not os.path.isdir(child_path):
        # Try fuzzy match
        for entry in os.listdir(children_dir):
            if child_name.lower() in entry.lower():
                child_path = os.path.join(children_dir, entry)
                child_name = entry
                break
        else:
            print(f"Child directory not found: {child_name}")
            return child_name, [], None

    photos = sorted([
        os.path.join(child_path, f)
        for f in os.listdir(child_path)
        if os.path.isfile(os.path.join(child_path, f)) and
           os.path.splitext(f.lower())[1] in IMAGE_EXTS
    ])

    # Find baby photo
    baby_photo = None
    if os.path.isdir(baby_dir):
        from face_utils import detect_face as _
        from collage import find_baby_photo
        baby_photo = find_baby_photo(child_name, baby_dir)

    return child_name, photos, baby_photo


def get_grid_positions(num_photos: int) -> list[int]:
    """
    Return the grid position index for each photo after selection and reordering.
    With 5 photos, reorder is [4, 0, 2, 3, 1], positions 0-4.
    """
    if num_photos >= 5:
        # After select_best_photos picks 5 and reorder [4,0,2,3,1]
        # Original indices [0,1,2,3,4] map to grid positions:
        # photo[0] -> reorder pos 1 -> grid pos 1 (row1)
        # photo[1] -> reorder pos 4 -> grid pos 4 (row2)
        # photo[2] -> reorder pos 2 -> grid pos 2 (row1)
        # photo[3] -> reorder pos 3 -> grid pos 3 (row2)
        # photo[4] -> reorder pos 0 -> grid pos 0 (row1)
        return [1, 4, 2, 3, 0]  # grid position for each original photo index
    else:
        return list(range(num_photos))


def run_qa(child_name: str, output_dir: str, photo_index: int = None):
    """Run QA for a single child."""
    child_name, photos, baby_photo = get_child_photos(child_name)

    if not photos:
        print(f"No photos found for {child_name}")
        return

    # Simulate photo selection (pick up to 5)
    from collage import select_best_photos
    selected = select_best_photos(photos, max_photos=5)
    num_selected = len(selected)

    # Map selected photos back to original indices for grid position
    grid_positions = get_grid_positions(num_selected)

    # Map selected photo paths to their index in the selected list
    selected_indices = {}
    for i, p in enumerate(selected):
        selected_indices[p] = i

    child_out = os.path.join(output_dir, child_name.replace(' ', '_'))
    os.makedirs(child_out, exist_ok=True)

    print(f"\n{'='*60}")
    print(f"QA: {child_name}")
    print(f"Total photos: {len(photos)}, Selected: {num_selected}")
    print(f"Baby photo: {baby_photo or 'None'}")
    print(f"Cell ratios: Row1={ROW1_RATIO:.3f} (portrait), Row2={ROW2_RATIO:.3f} (landscape)")
    print(f"Output: {child_out}")
    print(f"{'='*60}")

    for i, photo_path in enumerate(selected):
        if photo_index is not None and i != photo_index:
            continue

        sel_idx = i
        grid_pos = grid_positions[sel_idx]
        cell_ratio = get_cell_ratio_for_position(grid_pos, num_selected)
        cell_type = "Row1-portrait" if cell_ratio < 1.0 else "Row2-landscape"

        print(f"\n  Photo {i+1}/{num_selected}: {os.path.basename(photo_path)}")
        print(f"    Grid position: {grid_pos}, Cell: {cell_type} (ratio={cell_ratio:.2f})")

        try:
            diag = create_diagnostic(photo_path, i, num_selected, grid_pos, child_name)
            out_path = os.path.join(child_out,
                                    f"photo_{i+1}_pos{grid_pos}_{cell_type}.jpg")
            diag.save(out_path, 'JPEG', quality=92)
            print(f"    Saved: {out_path}")
        except Exception as e:
            print(f"    ERROR: {e}")
            import traceback
            traceback.print_exc()

    # Also check baby photo if present
    if baby_photo and (photo_index is None):
        print(f"\n  Baby photo: {os.path.basename(baby_photo)}")
        try:
            baby_img = Image.open(baby_photo)
            baby_img = ImageOps.exif_transpose(baby_img)
            if baby_img.mode in ('RGBA', 'P'):
                baby_img = baby_img.convert('RGB')
            face, _ = detect_face_on_image(baby_img)
            print(f"    Face detected: {face is not None}")
            if face:
                print(f"    Face box: {face}")

            # Baby photo uses object-fit:cover in a 1.8in x 2.2in frame
            baby_ratio = 1.8 / 2.2  # ~0.818
            diag = create_diagnostic(baby_photo, 0, 1, 0, f"{child_name} (baby)")
            out_path = os.path.join(child_out, "baby_photo_diag.jpg")
            diag.save(out_path, 'JPEG', quality=92)
            print(f"    Saved: {out_path}")
        except Exception as e:
            print(f"    ERROR: {e}")


def main():
    parser = argparse.ArgumentParser(description='QA diagnostic for face-aware cropping')
    parser.add_argument('name', help='Child name or "all"')
    parser.add_argument('--photo-index', type=int, default=None,
                        help='Only process this photo index (0-based in selected set)')
    parser.add_argument('--output-dir', default='/Users/asb/Downloads/collage-output/qa',
                        help='Output directory for diagnostic images')
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    if args.name.lower() == 'all':
        children_dir = "/Users/asb/Downloads/Individual Child_s Photos"
        for entry in sorted(os.listdir(children_dir)):
            child_path = os.path.join(children_dir, entry)
            if os.path.isdir(child_path):
                run_qa(entry, args.output_dir, args.photo_index)
    else:
        run_qa(args.name, args.output_dir, args.photo_index)

    print(f"\nDone. Diagnostics saved to: {args.output_dir}")


if __name__ == '__main__':
    main()
