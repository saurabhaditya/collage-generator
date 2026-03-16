# Child Dedication Collage & Yearbook Generator

Generate beautiful dedication collage pages for children as PDFs, with face-aware cropping, auto-rotation, and yearbook merging.

## Features

- **Face detection**: Automatically detects faces in photos and positions crops to keep face + hair centered
- **Auto-rotation**: Straightens tilted faces using eye detection
- **Auto-discovery**: Matches baby photos to child folders by name
- **Dedication text**: Reads `.docx` or `.txt` files from each child's folder (no python-docx dependency)
- **Elegant layout**: Gold-bordered collage with baby photo, dedication text, and growing-up photo grid
- **Yearbook merge**: Combine all individual pages into a single PDF
- **No LLM required**: Pure template-based generation — no API calls needed

## Requirements

- Python 3.10+
- Chrome or Chromium (for HTML-to-PDF conversion)
- `pip install -r requirements.txt`

## Directory Structure

```
Baby Photos/
├── SavitBaranwalBaby.JPG
├── Ethan Yang.JPG
└── ...

Individual Photos/
├── Savit Baranwal/
│   ├── Dedication.docx
│   ├── photo1.jpg
│   └── photo2.jpg
├── Ethan Yang/
│   ├── Dedication.docx
│   └── ...
└── ...
```

## Usage

### Generate for all children
```bash
python3 collage.py --auto \
    --baby-dir "./Baby Photos" \
    --children-dir "./Individual Photos" \
    --output-dir ./output \
    --yearbook yearbook.pdf
```

### Generate for one child
```bash
python3 collage.py --auto \
    --baby-dir "./Baby Photos" \
    --children-dir "./Individual Photos" \
    --output-dir ./output \
    --only "Savit Baranwal"
```

### Manual single-child mode
```bash
python3 collage.py --name "Savit Baranwal" \
    --baby-photo baby.jpg \
    --child-photos ./photos/ \
    --dedication "Your text here" \
    --output savit.pdf
```

### Merge existing PDFs into yearbook
```bash
python3 collage.py --merge --output-dir ./output --yearbook yearbook.pdf
```

### HTML-only output (for debugging)
```bash
python3 collage.py --auto ... --html-only
```

## How It Works

1. **Discovery**: Scans the children directory for per-child folders, matches baby photos by fuzzy name matching
2. **Face processing**: For each photo, detects face position, computes tilt angle from eye positions, rotates to straighten
3. **Smart cropping**: Generates per-photo CSS `object-position` values that keep the face and hair centered in the crop
4. **HTML generation**: Creates a letter-sized HTML page with gold-bordered layout, baby photo, dedication text, and a responsive photo grid
5. **PDF conversion**: Uses headless Chrome to render pixel-perfect PDFs
6. **Yearbook merge**: Combines all individual PDFs into one document

## Layout

Each page includes:
- Child's first name as header
- Baby photo with gold frame (left)
- Dedication text in italic serif (right)
- "Growing Up" photo grid (3 top + 2 bottom for 5 photos)
- Decorative double gold border

## License

MIT
