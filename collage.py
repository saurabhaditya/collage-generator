#!/usr/bin/env python3
"""
Child Dedication Collage & Yearbook Generator

Generates dedication collage pages as PDFs, with face-aware cropping
and auto-rotation. Can merge all pages into a single yearbook PDF.

Usage:
    # Generate for all children
    python3 collage.py --auto \
        --baby-dir "./Baby Photos" \
        --children-dir "./Individual Photos" \
        --output-dir ./output

    # Generate for one child only
    python3 collage.py --auto \
        --baby-dir "./Baby Photos" \
        --children-dir "./Individual Photos" \
        --output-dir ./output \
        --only "Savit Baranwal"

    # Single child (manual mode)
    python3 collage.py --name "Savit Baranwal" \
        --baby-photo baby.jpg \
        --child-photos ./photos/ \
        --dedication "Text here" \
        --output savit.pdf

    # Merge existing PDFs into yearbook
    python3 collage.py --merge --output-dir ./output --yearbook yearbook.pdf
"""

import argparse
import base64
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from difflib import SequenceMatcher
from xml.etree import ElementTree

from PIL import Image, ImageOps
from face_utils import process_photo

IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}


# ---------------------------------------------------------------------------
# Image utilities
# ---------------------------------------------------------------------------

def image_to_base64_with_face(image_path: str, max_size: int = 1200) -> tuple[str, str]:
    """
    Process image with face detection, return (base64_data_uri, css_object_position).
    """
    img, (x_pct, y_pct), angle = process_photo(image_path, max_size=max_size)

    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=88)
    b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')

    data_uri = f"data:image/jpeg;base64,{b64}"
    obj_pos = f"{x_pct}% {y_pct}%"

    if abs(angle) >= 1.5:
        print(f"    Rotated {os.path.basename(image_path)} by {angle:.1f}°")

    return data_uri, obj_pos


def image_to_base64_simple(image_path: str, max_size: int = 1200) -> str:
    """Simple base64 encode without face detection (for baby photos)."""
    img = Image.open(image_path)
    img = ImageOps.exif_transpose(img)
    img.thumbnail((max_size, max_size), Image.LANCZOS)
    if img.mode in ('RGBA', 'P'):
        img = img.convert('RGB')
    buffer = io.BytesIO()
    img.save(buffer, format='JPEG', quality=88)
    b64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
    return f"data:image/jpeg;base64,{b64}"


def is_image(path: str) -> bool:
    return os.path.splitext(path.lower())[1] in IMAGE_EXTS


# ---------------------------------------------------------------------------
# DOCX reader (stdlib only)
# ---------------------------------------------------------------------------

def read_docx(path: str) -> str:
    """Extract plain text from a .docx file."""
    with zipfile.ZipFile(path) as z:
        xml_content = z.read('word/document.xml')
    tree = ElementTree.fromstring(xml_content)
    paragraphs = []
    for p in tree.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p'):
        texts = []
        for t in p.iter('{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'):
            if t.text:
                texts.append(t.text)
        if texts:
            paragraphs.append(''.join(texts))
    return '\n'.join(paragraphs)


# ---------------------------------------------------------------------------
# Auto-discovery
# ---------------------------------------------------------------------------

def normalize_name(s: str) -> str:
    s = os.path.splitext(s)[0]
    s = re.sub(r'[_\-]', ' ', s)
    s = re.sub(r'(?i)baby', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s.lower()


def find_baby_photo(child_name: str, baby_dir: str) -> str | None:
    child_norm = normalize_name(child_name)
    best_match = None
    best_score = 0.0

    for f in os.listdir(baby_dir):
        fpath = os.path.join(baby_dir, f)
        if not os.path.isfile(fpath) or not is_image(fpath):
            continue
        file_norm = normalize_name(f)
        score = SequenceMatcher(None, child_norm, file_norm).ratio()
        child_parts = child_norm.split()
        for part in child_parts:
            if part in file_norm:
                score = max(score, 0.6 + 0.1 * len(part) / max(len(file_norm), 1))
        if score > best_score:
            best_score = score
            best_match = fpath

    return best_match if best_score > 0.8 else None


def load_mapping(mapping_path: str, baby_dir: str) -> dict:
    """Load a name->filename mapping JSON and resolve to full paths."""
    if not mapping_path or not os.path.exists(mapping_path):
        return {}
    with open(mapping_path) as f:
        raw = json.load(f)
    resolved = {}
    for name, files in raw.items():
        if isinstance(files, str):
            files = [files]
        # Try each file until we find one that exists
        for fname in files:
            fpath = os.path.join(baby_dir, fname)
            if os.path.exists(fpath):
                resolved[name.lower()] = fpath
                break
    return resolved


def discover_children(baby_dir: str, children_dir: str,
                      mapping: dict = None) -> list[dict]:
    """mapping: dict of lowercase_name -> baby_photo_path from scrape."""
    if mapping is None:
        mapping = {}

    children = []
    for entry in sorted(os.listdir(children_dir)):
        child_path = os.path.join(children_dir, entry)
        if not os.path.isdir(child_path):
            continue

        photos = sorted([
            os.path.join(child_path, f)
            for f in os.listdir(child_path)
            if is_image(os.path.join(child_path, f))
        ])

        dedication = ""
        for f in os.listdir(child_path):
            fp = os.path.join(child_path, f)
            if f.lower().endswith('.docx'):
                try:
                    dedication = read_docx(fp)
                except Exception as e:
                    print(f"  Warning: Could not read {f}: {e}")
            elif f.lower().endswith('.txt'):
                with open(fp) as tf:
                    dedication = tf.read().strip()

        # Try mapping first (from scraped album), then filename matching
        baby_photo = mapping.get(entry.lower())
        if not baby_photo:
            baby_photo = find_baby_photo(entry, baby_dir)

        children.append({
            "name": entry,
            "baby_photo": baby_photo,
            "child_photos": photos,
            "dedication": dedication,
            "photo_dir": child_path,
        })

    return children


# ---------------------------------------------------------------------------
# HTML collage generation
# ---------------------------------------------------------------------------

def select_best_photos(child_photos: list, max_photos: int = 5) -> list:
    """Select the best photos, preferring ones with detected faces."""
    if len(child_photos) <= max_photos:
        return child_photos

    import cv2
    from face_utils import pil_to_cv, detect_face

    # Score each photo: has face = 1, no face = 0
    scored = []
    for cp in child_photos:
        try:
            img = Image.open(cp)
            img = ImageOps.exif_transpose(img)
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            img.thumbnail((400, 400), Image.LANCZOS)
            cv_img = pil_to_cv(img)
            face = detect_face(cv_img)
            scored.append((cp, 1 if face else 0))
        except Exception:
            scored.append((cp, 0))

    # Separate photos with and without faces
    with_faces = [cp for cp, s in scored if s == 1]
    without_faces = [cp for cp, s in scored if s == 0]

    # Only use face photos; fall back to non-face only if needed
    if len(with_faces) >= max_photos:
        step = len(with_faces) / max_photos
        return [with_faces[int(i * step)] for i in range(max_photos)]
    elif with_faces:
        # Use all face photos + fill remainder from non-face if needed
        result = with_faces[:]
        remaining = max_photos - len(result)
        if remaining > 0 and without_faces:
            result += without_faces[:remaining]
        return result[:max_photos]
    else:
        # No faces detected at all — use evenly spaced
        step = len(child_photos) / max_photos
        return [child_photos[int(i * step)] for i in range(max_photos)]


def build_photo_grid(child_data: list, name: str) -> str:
    num = len(child_data)
    items = ""

    def item(cd, col_start, col_end, row):
        obj_pos = cd.get('obj_pos', '50% 20%')
        return f'''
            <div class="grid-item" style="grid-column: {col_start} / {col_end}; grid-row: {row};">
                <img src="{cd['b64']}" alt="{name}" style="object-position: {obj_pos};">
            </div>'''

    if num >= 5:
        for i in range(3):
            items += item(child_data[i], i*2+1, i*2+3, 1)
        items += item(child_data[3], 1, 4, 2)
        items += item(child_data[4], 4, 7, 2)
    elif num == 4:
        for i in range(4):
            col = (i % 2) * 3 + 1
            row = i // 2 + 1
            items += item(child_data[i], col, col+3, row)
    elif num == 3:
        for i in range(3):
            items += item(child_data[i], i*2+1, i*2+3, 1)
    elif num == 2:
        for i in range(2):
            items += item(child_data[i], i*3+1, i*3+4, 1)
    elif num == 1:
        items += item(child_data[0], 2, 6, 1)

    return items


def generate_html(name: str, baby_photo: str | None, child_photos: list,
                  dedication: str) -> str:
    first_name = name.split()[0]
    full_name = name

    # Baby photo section
    baby_section = ""
    if baby_photo and dedication:
        baby_b64 = image_to_base64_simple(baby_photo, max_size=900)
        baby_section = f'''
        <div class="top-section">
            <div class="baby-photo-container">
                <div>
                    <div class="baby-photo-frame">
                        <img src="{baby_b64}" alt="{name} as a baby">
                    </div>
                    <div class="baby-label">Baby {first_name}</div>
                </div>
            </div>
            <div class="dedication-container">
                <div class="label">Dedication</div>
                <div class="text">{dedication}</div>
            </div>
        </div>'''
    elif baby_photo:
        baby_b64 = image_to_base64_simple(baby_photo, max_size=900)
        baby_section = f'''
        <div class="top-section" style="height: 2.2in; justify-content: center;">
            <div class="baby-photo-container" style="margin-left: 0;">
                <div>
                    <div class="baby-photo-frame">
                        <img src="{baby_b64}" alt="{name} as a baby">
                    </div>
                    <div class="baby-label">Baby {first_name}</div>
                </div>
            </div>
        </div>'''
    elif dedication:
        baby_section = f'''
        <div class="dedication-only">
            <div class="label">Dedication</div>
            <div class="text">{dedication}</div>
        </div>'''

    # Process child photos with face detection
    photos = select_best_photos(child_photos, max_photos=5)
    child_data = []
    for cp in photos:
        b64, obj_pos = image_to_base64_with_face(cp, max_size=1200)
        child_data.append({"b64": b64, "obj_pos": obj_pos})

    # Reorder: put last (most recent) photo first for prominence
    if len(child_data) >= 5:
        reordered = [child_data[4], child_data[0], child_data[2],
                     child_data[3], child_data[1]]
        child_data = reordered

    photo_items = build_photo_grid(child_data, name)
    grid_rows = "1.2fr 1fr" if len(child_data) >= 4 else "1fr"

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<style>
    @page {{ size: 8.5in 11in; margin: 0; }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}

    body {{
        width: 8.5in; height: 11in;
        font-family: 'Georgia', 'Times New Roman', serif;
        background: #fdf6e3; color: #3c3836;
        overflow: hidden;
    }}

    .page {{
        width: 8.5in; height: 11in;
        padding: 0.45in 0.55in;
        display: flex; flex-direction: column;
        position: relative; overflow: hidden;
    }}

    .page::before {{
        content: ''; position: absolute;
        top: 0.22in; left: 0.22in; right: 0.22in; bottom: 0.22in;
        border: 2.5px solid #b8860b; border-radius: 10px;
        pointer-events: none; z-index: 10;
    }}
    .page::after {{
        content: ''; position: absolute;
        top: 0.3in; left: 0.3in; right: 0.3in; bottom: 0.3in;
        border: 1px solid #d4a84b; border-radius: 7px;
        pointer-events: none; z-index: 10;
    }}

    .header {{
        text-align: center; margin-bottom: 0.15in; flex-shrink: 0;
    }}
    .header h1 {{
        font-size: {min(30, max(18, 300 // max(len(full_name), 1)))}pt;
        font-weight: 700; color: #4a3a10;
        letter-spacing: 3px; text-transform: uppercase;
        font-family: 'Georgia', serif; line-height: 1.1;
    }}
    .header .ornament {{
        font-size: 11pt; color: #b8860b;
        letter-spacing: 8px; line-height: 1.2;
    }}

    .top-section {{
        display: flex; gap: 0.2in; align-items: center;
        flex-shrink: 0; height: 2.5in; margin-bottom: 0.15in;
    }}
    .baby-photo-container {{
        flex: 0 0 auto; display: flex;
        align-items: center; justify-content: center;
        margin-left: 0.05in;
    }}
    .baby-photo-frame {{
        border: 3px solid #b8860b; border-radius: 8px;
        padding: 4px; background: white;
        box-shadow: 0 2px 10px rgba(0,0,0,0.12);
    }}
    .baby-photo-frame img {{
        display: block; width: 1.8in; height: 2.2in;
        border-radius: 5px; object-fit: cover;
    }}
    .baby-label {{
        text-align: center; font-size: 7.5pt;
        color: #8b7355; margin-top: 3px; font-style: italic;
    }}

    .dedication-container {{
        flex: 1; display: flex; flex-direction: column;
        justify-content: center; padding: 0.18in 0.25in;
        background: linear-gradient(135deg, rgba(218,165,32,0.07) 0%, rgba(184,134,11,0.02) 100%);
        border-radius: 8px; border: 1px solid rgba(184,134,11,0.18);
        height: 100%;
    }}
    .dedication-container .label, .dedication-only .label {{
        font-size: 8pt; text-transform: uppercase;
        letter-spacing: 4px; color: #b8860b;
        margin-bottom: 8px; font-weight: 600;
    }}
    .dedication-container .text, .dedication-only .text {{
        font-size: 12pt; line-height: 1.55;
        color: #4a4235; font-style: italic;
    }}
    .dedication-only {{
        padding: 0.2in 0.3in; margin-bottom: 0.15in;
        background: linear-gradient(135deg, rgba(218,165,32,0.07) 0%, rgba(184,134,11,0.02) 100%);
        border-radius: 8px; border: 1px solid rgba(184,134,11,0.18);
        flex-shrink: 0;
    }}

    .photos-section {{
        flex: 1; display: flex; flex-direction: column; min-height: 0;
    }}
    .photos-section .section-label {{
        font-size: 8pt; text-transform: uppercase;
        letter-spacing: 4px; color: #b8860b;
        margin-bottom: 0.07in; font-weight: 600;
        text-align: center; flex-shrink: 0;
    }}
    .photo-grid {{
        flex: 1; display: grid;
        grid-template-columns: repeat(6, 1fr);
        grid-template-rows: {grid_rows};
        gap: 0.1in; min-height: 0;
    }}
    .grid-item {{
        overflow: hidden; border-radius: 6px;
        border: 2.5px solid #c9a84a; background: white;
        box-shadow: 0 1px 4px rgba(0,0,0,0.08);
        min-height: 0;
    }}
    .grid-item img {{
        width: 100%; height: 100%;
        object-fit: cover; display: block;
    }}

    .footer {{
        text-align: center; padding-top: 0.12in; flex-shrink: 0;
    }}
    .footer .ornament {{
        font-size: 10pt; color: #b8860b; letter-spacing: 6px;
    }}
</style>
</head>
<body>
<div class="page">
    <div class="header">
        <div class="ornament">&bull; &bull; &bull;</div>
        <h1>{full_name}</h1>
        <div class="ornament">&bull; &bull; &bull;</div>
    </div>
    <div class="content" style="flex:1; display:flex; flex-direction:column; z-index:1; min-height:0;">
        {baby_section}
        <div class="photos-section">
            <div class="section-label">Growing Up</div>
            <div class="photo-grid">
                {photo_items}
            </div>
        </div>
    </div>
    <div class="footer">
        <div class="ornament">&diams; &diams; &diams;</div>
    </div>
</div>
</body>
</html>'''

    return html


# ---------------------------------------------------------------------------
# PDF conversion & merging
# ---------------------------------------------------------------------------

def find_chrome() -> str:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    import shutil
    for name in ["google-chrome", "google-chrome-stable", "chromium"]:
        p = shutil.which(name)
        if p:
            return p
    print("ERROR: Chrome/Chromium not found.", file=sys.stderr)
    sys.exit(1)


def html_to_pdf(html_content: str, output_path: str) -> bool:
    with tempfile.NamedTemporaryFile(suffix='.html', mode='w', delete=False, encoding='utf-8') as f:
        f.write(html_content)
        html_path = f.name
    try:
        cmd = [
            find_chrome(), "--headless", "--disable-gpu", "--no-sandbox",
            "--print-to-pdf=" + output_path,
            "--print-to-pdf-no-header", "--no-pdf-header-footer",
            html_path
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return os.path.exists(output_path)
    finally:
        os.unlink(html_path)


def merge_pdfs(pdf_paths: list, output_path: str):
    """Merge multiple PDFs into one using Python (PyPDF2 or pikepdf or fallback)."""
    try:
        from PyPDF2 import PdfMerger
        merger = PdfMerger()
        for p in pdf_paths:
            merger.append(p)
        merger.write(output_path)
        merger.close()
        return
    except ImportError:
        pass

    try:
        import pikepdf
        pdf = pikepdf.Pdf.new()
        for p in pdf_paths:
            src = pikepdf.Pdf.open(p)
            pdf.pages.extend(src.pages)
        pdf.save(output_path)
        return
    except ImportError:
        pass

    # Fallback: use macOS built-in python or /usr/bin/python3 with Quartz
    # Or use ghostscript
    import shutil
    gs = shutil.which("gs")
    if gs:
        cmd = [gs, "-dBATCH", "-dNOPAUSE", "-q", "-sDEVICE=pdfwrite",
               f"-sOutputFile={output_path}"] + pdf_paths
        subprocess.run(cmd, check=True)
        return

    # Last resort: try installing PyPDF2
    print("Installing PyPDF2 for PDF merging...")
    subprocess.run([sys.executable, "-m", "pip", "install", "PyPDF2", "--quiet"],
                   capture_output=True)
    from PyPDF2 import PdfMerger
    merger = PdfMerger()
    for p in pdf_paths:
        merger.append(p)
    merger.write(output_path)
    merger.close()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def generate_single(name: str, baby_photo: str | None, child_photos_dir: str,
                    dedication: str, output: str, html_only: bool = False) -> bool:
    photos = sorted([
        os.path.join(child_photos_dir, f)
        for f in os.listdir(child_photos_dir)
        if is_image(os.path.join(child_photos_dir, f))
    ])

    if not photos:
        print(f"  No photos found in {child_photos_dir}", file=sys.stderr)
        return False

    print(f"  Processing {len(photos)} photos with face detection...")
    html = generate_html(name, baby_photo, photos, dedication)

    html_out = output.replace('.pdf', '.html')
    with open(html_out, 'w') as f:
        f.write(html)

    if html_only:
        print(f"  HTML: {html_out}")
        return True

    if html_to_pdf(html, output):
        print(f"  PDF: {output}")
        return True
    return False


def generate_all(baby_dir: str, children_dir: str, output_dir: str,
                 only: str | None = None, html_only: bool = False,
                 yearbook: str | None = None, mapping_path: str | None = None):
    os.makedirs(output_dir, exist_ok=True)

    mapping = load_mapping(mapping_path, baby_dir) if mapping_path else {}
    if mapping:
        print(f"Loaded {len(mapping)} baby photo mappings from {mapping_path}")

    children = discover_children(baby_dir, children_dir, mapping=mapping)
    print(f"Discovered {len(children)} children\n")

    if only:
        children = [c for c in children if only.lower() in c['name'].lower()]
        if not children:
            print(f"No child matching '{only}' found.", file=sys.stderr)
            sys.exit(1)

    success = 0
    skipped = 0
    failed = 0
    pdf_paths = []

    for child in children:
        name = child['name']
        print(f"[{name}]")

        if not child['child_photos']:
            print(f"  Skipped: no photos")
            skipped += 1
            continue

        if not child['baby_photo']:
            print(f"  Warning: no baby photo found")
        if not child['dedication']:
            print(f"  Warning: no dedication text")

        safe_name = re.sub(r'[^\w\s-]', '', name).replace(' ', '_')
        output = os.path.join(output_dir, f"{safe_name}_collage.pdf")

        ok = generate_single(
            name=name,
            baby_photo=child['baby_photo'],
            child_photos_dir=child['photo_dir'],
            dedication=child['dedication'],
            output=output,
            html_only=html_only,
        )

        if ok:
            success += 1
            pdf_paths.append(output)
        else:
            failed += 1

    print(f"\nGenerated: {success}, Skipped: {skipped}, Failed: {failed}")

    # Merge into yearbook if requested
    if yearbook and pdf_paths and not html_only:
        yb_path = yearbook if os.path.isabs(yearbook) else os.path.join(output_dir, yearbook)
        print(f"\nMerging {len(pdf_paths)} pages into yearbook...")
        merge_pdfs(sorted(pdf_paths), yb_path)
        print(f"Yearbook: {yb_path}")


def do_merge(output_dir: str, yearbook: str):
    """Merge all existing collage PDFs in output_dir into a yearbook."""
    pdfs = sorted([
        os.path.join(output_dir, f)
        for f in os.listdir(output_dir)
        if f.endswith('_collage.pdf')
    ])
    if not pdfs:
        print("No collage PDFs found to merge.", file=sys.stderr)
        sys.exit(1)

    yb_path = yearbook if os.path.isabs(yearbook) else os.path.join(output_dir, yearbook)
    print(f"Merging {len(pdfs)} pages...")
    merge_pdfs(pdfs, yb_path)
    print(f"Yearbook: {yb_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Child Dedication Collage & Yearbook Generator',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('--name', help='Child name (single mode)')
    parser.add_argument('--baby-photo', help='Baby photo path (single mode)')
    parser.add_argument('--child-photos', help='Child photos dir (single mode)')
    parser.add_argument('--dedication', help='Dedication text (single mode)')
    parser.add_argument('--output', help='Output path (single mode)')

    parser.add_argument('--auto', action='store_true', help='Auto-discover all children')
    parser.add_argument('--baby-dir', help='Baby photos directory')
    parser.add_argument('--children-dir', help='Per-child photo directories')
    parser.add_argument('--output-dir', help='Output directory')
    parser.add_argument('--only', help='Generate only for this child')

    parser.add_argument('--mapping', help='Baby name→filename JSON mapping (from scrape_album.py)')
    parser.add_argument('--merge', action='store_true', help='Merge existing PDFs into yearbook')
    parser.add_argument('--yearbook', default='yearbook.pdf', help='Yearbook output filename')
    parser.add_argument('--html-only', action='store_true', help='Output HTML only')

    args = parser.parse_args()

    if args.merge:
        if not args.output_dir:
            parser.error("--merge requires --output-dir")
        do_merge(args.output_dir, args.yearbook)
    elif args.auto:
        if not all([args.baby_dir, args.children_dir, args.output_dir]):
            parser.error("--auto requires --baby-dir, --children-dir, --output-dir")
        generate_all(args.baby_dir, args.children_dir, args.output_dir,
                     only=args.only, html_only=args.html_only,
                     yearbook=args.yearbook, mapping_path=args.mapping)
    elif args.name and args.child_photos and args.output:
        generate_single(args.name, args.baby_photo, args.child_photos,
                        args.dedication or "", args.output, args.html_only)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
