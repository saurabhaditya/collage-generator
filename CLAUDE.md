# Collage Generator

## Project Overview
Yearbook collage generator that creates dedication pages for children. Each page has a name header, optional baby photo + dedication text, 5 child photos in a grid, and decorative borders. Outputs individual PDFs and a merged yearbook PDF.

## Architecture
- `collage.py` — Main generator with face-aware cropping (Haar cascades + eye validation)
- `collage_no_face_detection.py` — Variant with no face detection; images EXIF-rotated only, default 50% 40% centering
- `collage_full_photos.py` — Variant showing full images with no crop (`object-fit: contain`), no information loss
- `face_utils.py` — Face detection, tilt correction, and face-centered pre-cropping
- `qa_faces.py` — Visual diagnostic QA showing original → pre-cropped → cell-cropped panels
- `qa_llm.py` — LLM-based QA using Claude vision API to grade each yearbook page
- `scrape_album.py` — Google Photos album scraper (Playwright) mapping baby names to filenames

## Key Technical Learnings

### Face Detection (Haar Cascades)
- OpenCV Haar cascades produce false positives on: sand textures, metal structures (Eiffel Tower), sign text, tree bark/branches, gate metalwork, handwriting, and artwork patterns
- **Eye-based validation is the most reliable filter**: before cropping, if a detected face is small (< 15% of image) and has 0 detectable eyes, reject it as a false positive
- For group photos (3+ faces detected), apply eye validation up to 20% face size threshold — the "largest face" in a group may be a false positive on background elements (gym walls, windows)
- `minNeighbors=8` (strict cascade) does NOT reliably filter false positives — the same textures survive even at high confidence
- Size thresholds alone (0.08, 0.10, etc.) are fragile — false positives can appear at 0.088–0.165

### Photo Selection
- Sort by face score descending and pick top N (best faces first) rather than evenly spacing through filename-sorted photos
- Filter out unreliable detections (face_pct < 0.08) before scoring

### Post-crop Validation
- Two-method approach: (1) strict cascade pass (minNeighbors=8), or (2) regular detection + eye confirmation
- If post-crop validation fails, fall back to uncropped image with default centering

### Layout
- Grid uses CSS Grid with 6 columns: Row 1 has 3 cells (span 2 each), Row 2 has 2 cells (span 3 each)
- Photos reordered as [4,0,2,3,1] so the last (most recent) photo gets top-left prominence
- `object-fit: cover` with `object-position` set by face detection for cropped variants
- `object-fit: contain` with background color for the full-photos variant (no information loss)
- Default `object-position: 50% 40%` when no face detected (slightly above center works better than 50% 50% for people photos)

### PDF Generation
- Uses headless Chrome (`--print-to-pdf`) for HTML→PDF conversion
- Images embedded as base64 data URIs in HTML
- `pdftoppm` (poppler) for PDF→PNG rendering in QA scripts; use temp file output, not stdout pipe

## Directory Layout (at runtime)
- Children's photos: `../Individual Child_s Photos/{Name}/`
- Baby photos: `../Baby Photos/`
- Output: `../collage-output/` (face detection), `../collage-output-no-face/`, `../collage-output-full/`
- Baby mapping: `folder_baby_mapping.json`

## Commands
```bash
# Generate yearbook with face detection
python3 collage.py --auto --baby-dir "../Baby Photos" --children-dir "../Individual Child_s Photos" --output-dir ../collage-output --mapping folder_baby_mapping.json --yearbook yearbook.pdf

# Generate without face detection
python3 collage_no_face_detection.py --auto --baby-dir "../Baby Photos" --children-dir "../Individual Child_s Photos" --output-dir ../collage-output-no-face --mapping folder_baby_mapping.json --yearbook yearbook_no_face.pdf

# Generate with full photos (no crop)
python3 collage_full_photos.py --auto --baby-dir "../Baby Photos" --children-dir "../Individual Child_s Photos" --output-dir ../collage-output-full --mapping folder_baby_mapping.json --yearbook yearbook_full.pdf

# Merge existing PDFs
python3 collage.py --merge --output-dir ../collage-output --yearbook yearbook.pdf

# Run LLM QA
python3 qa_llm.py ../collage-output/yearbook.pdf

# Run visual face QA diagnostics
python3 qa_faces.py "Claire Chen"
python3 qa_faces.py all
```

## Dependencies
- Pillow, pillow-heif, opencv-python-headless, PyPDF2
- anthropic (for qa_llm.py)
- playwright (for scrape_album.py)
- Google Chrome/Chromium (for PDF generation)
- poppler/pdftoppm (for QA PDF rendering)
