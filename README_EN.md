[中文](README.md) | [English](README_EN.md)

# PDF2MD — PDF to Markdown Converter

A PDF-to-Markdown conversion web service built with **Foxit PDF SDK (Python)** and **FastAPI**.

---

## Features

- **Smart Text Extraction** — Character-level extraction of font name, size, bold/italic attributes, and coordinates
- **Automatic Heading Detection** — Prioritizes PDF bookmarks for heading hierarchy; falls back to SDK Layout Recognition (LR) module; ultimately uses font-size heuristics
- **Table Extraction** — Detects and extracts tables via the SDK Layout Recognition module, outputting Markdown pipe-table syntax
- **Pseudo-Table Correction** — Identifies section headings misclassified as tables (e.g., numbered headings spanning columns) and restores them as proper Markdown headings
- **Smart Paragraph Merging** — Detects line continuations and mid-paragraph breaks, merging multi-line text into coherent paragraphs and eliminating extraneous blank lines
- **Image Extraction** — Automatically detects and exports embedded page images as PNG files
- **Hyperlink Preservation** — Recognizes URLs in text and converts them to Markdown link syntax
- **Header/Footer Filtering** — Frequency-based detection to automatically skip repeated headers and footers
- **Table of Contents Generation** — Builds a nested Markdown TOC from the PDF bookmark tree
- **Web UI** — Drag-and-drop upload, real-time progress, source/preview tabs, one-click download and copy
- **REST API** — Ready for integration with other systems
- **CLI Tool** — Command-line conversion for scripting and batch processing

---

## Project Structure

```
pdf2md/
├── app/
│   ├── __init__.py          # Package init
│   ├── config.py            # SDK license & directory config
│   ├── pdf_parser.py        # Foxit SDK PDF parsing core
│   ├── md_converter.py      # Structured content → Markdown engine
│   └── main.py              # FastAPI routes & web service
├── templates/
│   └── index.html           # Web UI page
├── static/                  # Static assets
├── uploads/                 # Temporary storage for uploaded PDFs (auto-created)
├── output/                  # Markdown output & extracted images
│   └── images/              # Extracted images directory
├── convert.py               # CLI conversion script
├── requirements.txt         # Python dependencies
├── run.py                   # Web service entry point
└── README.md
```

---

## Requirements

| Item | Requirement |
|------|-------------|
| OS | Windows 10/11 or Linux |
| Python | 3.8 — 3.12 |
| Foxit PDF SDK | Install via pip: `FoxitPDFSDKPython3` |
| Memory | 4 GB+ recommended |

---

## Setup

### 1. Clone the Repository

```bash
git clone git@github.com:AmyLin2013/pdf2md.git
cd pdf2md
```

### 2. Create a Virtual Environment (Recommended)

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

Dependencies:
- `FoxitPDFSDKPython3` — Foxit PDF SDK Python binding
- `fastapi` — Web framework
- `uvicorn[standard]` — ASGI server
- `python-multipart` — File upload support
- `Jinja2` — HTML template engine
- `aiofiles` — Async file operations

### 4. Configure Foxit PDF SDK License (Required)

> **⚠️ Important:** The trial license bundled with this project has expired. You must replace it with your own valid license before running.
>
> Visit the [Foxit PDF SDK website](https://developers.foxit.com/products/pdf-sdk/) to request a trial or purchase a commercial license. You will receive an `SN` and a `Key`.

The license is configured via two variables in `app/config.py` — `FOXIT_SN` and `FOXIT_KEY`:

```python
# app/config.py
FOXIT_SN = os.environ.get("FOXIT_SN", "your_sn_here")
FOXIT_KEY = os.environ.get("FOXIT_KEY", "your_key_here")
```

There are two ways to set your license:

**Option A: Edit the config file directly**

Modify `app/config.py` and replace the default values of `FOXIT_SN` and `FOXIT_KEY` with your license values.

**Option B: Set environment variables (recommended — avoids committing the license to the repository)**

```bash
# Windows PowerShell
$env:FOXIT_SN = "your_sn_here"
$env:FOXIT_KEY = "your_key_here"

# Linux / macOS
export FOXIT_SN="your_sn_here"
export FOXIT_KEY="your_key_here"
```

If the license is invalid or expired, the service will fail with an `e_ErrInvalidLicense` error on startup.

### 5. Start the Service

```bash
python run.py
```

The service will start at **http://0.0.0.0:8000**.

Startup parameters can be adjusted in `run.py`:

```python
uvicorn.run(
    "app.main:app",
    host="0.0.0.0",   # Listen address; 0.0.0.0 allows external access
    port=8000,          # Port number
    reload=True,        # Dev mode hot-reload (disable in production)
)
```

> **Production tip:** Set `reload=False` and consider using `workers=4` (multi-process) for better concurrency.

---

## Usage

### Option 1: Web UI

1. Open **http://localhost:8000** in your browser
2. Drag and drop a PDF file onto the upload area, or click to select a file
3. Choose options:
   - ✅ **Extract Images** — Export embedded images from the PDF
   - ✅ **Generate TOC** — Build a table of contents from bookmarks
4. Click the **"Start Conversion"** button
5. After conversion:
   - View raw Markdown in the **"Source"** tab
   - View rendered output in the **"Preview"** tab
   - Click **"Download .md File"** to save locally
   - Click **"Copy Markdown"** to copy to clipboard

### Option 2: CLI

```bash
python convert.py input.pdf                      # Output to input.md
python convert.py input.pdf -o output.md         # Specify output path
python convert.py input.pdf --no-images          # Skip image extraction
python convert.py input.pdf --toc                # Generate TOC
python convert.py input.pdf --keep-header-footer # Keep headers/footers
```

### Option 3: REST API

#### Convert PDF

```bash
curl -X POST http://localhost:8000/api/convert \
  -F "file=@/path/to/your/document.pdf" \
  -F "include_images=true" \
  -F "include_toc=true"
```

**Response example:**

```json
{
  "success": true,
  "markdown": "# Document Title\n\n## Chapter 1\n\n...",
  "filename": "document_1709012345678.md",
  "download_url": "/api/download/document_1709012345678.md",
  "stats": {
    "page_count": 10,
    "text_blocks": 156,
    "images_extracted": 3,
    "links_found": 8,
    "bookmarks": 12,
    "parse_time_ms": 450,
    "convert_time_ms": 23,
    "title": "Sample Document",
    "author": "Author Name"
  }
}
```

#### Download Markdown File

```bash
curl -O http://localhost:8000/api/download/document_1709012345678.md
```

#### Health Check

```bash
curl http://localhost:8000/api/health
# {"status": "ok", "service": "pdf2md"}
```

#### Swagger Docs

Interactive API documentation auto-generated by FastAPI:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## API Reference

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI page |
| `POST` | `/api/convert` | Upload a PDF and convert to Markdown |
| `GET` | `/api/download/{filename}` | Download a converted .md file |
| `GET` | `/api/health` | Service health check |

### `POST /api/convert` Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `file` | File | ✅ | — | PDF file |
| `include_images` | bool | — | `true` | Extract images |
| `include_toc` | bool | — | `true` | Generate table of contents |

---

## How It Works

### PDF Parsing Pipeline

```
PDF File
  │
  ▼
Library.Initialize(sn, key)   ← SDK initialization
  │
  ▼
PDFDoc(path) → doc.Load()     ← Load document
  │
  ├── Metadata → title, author
  ├── Bookmark → TOC tree
  │
  ▼
Iterate each PDFPage:
  │
  ├── TextPage(page) → GetCharInfo() per-character extraction
  │   ├── font_name, font_size  → heading detection
  │   ├── is_bold, is_italic    → style recognition
  │   └── char_box (x,y,w,h)   → layout ordering
  │
  ├── LR (Layout Recognition)
  │   ├── LRHeading → section headings (with level)
  │   ├── LRTable → table row/column data
  │   └── Pseudo-table detection → single-row headings
  │       misidentified as tables are restored
  │
  ├── GraphicsObjects → ImageObject → Bitmap → PNG
  │
  └── PageTextLinks → URL + text range
```

### Heading Detection Strategy

A three-tier heading detection strategy, applied in priority order:

1. **Bookmark-first**: If the PDF contains bookmarks, bookmark titles are fuzzy-matched against page text blocks; matched blocks are tagged with the corresponding heading level
2. **LR Layout Analysis**: The Foxit SDK Layout Recognition module automatically identifies heading regions and levels. When the SDK misclassifies multi-line numbered headings (e.g., `3.2 Corporate Development…`) as single-row tables, the parser detects and corrects this via regex matching of section number patterns (`X.Y`, `Chapter X`, etc.)
3. **Font-size Heuristic**: Computes the document-wide font-size distribution to find the "body font size" (the size used by the most characters), then maps heading levels by ratio:

   | Font Size / Body Size Ratio | Heading Level |
   |-----------------------------|---------------|
   | ≥ 2.0 | H1 |
   | ≥ 1.7 | H2 |
   | ≥ 1.4 | H3 |
   | ≥ 1.2 (bold) | H4 |
   | ≥ 1.05 (bold) | H5 |
   | = 1.0 (bold) | H6 |

### Paragraph Merging Strategy

Intelligently merges multi-line text split by PDF typesetting into coherent paragraphs:

1. **Continuation Detection**: Uses geometric position (left indent, line spacing) and text content (trailing punctuation, list markers) to determine whether adjacent lines belong to the same paragraph
2. **Internal Whitespace Collapsing**: Redundant spaces and line breaks within PDF text blocks are collapsed to a single space
3. **Blank Line Cleanup**: Post-processing removes accidentally inserted blank lines between paragraphs (where the preceding line doesn't end with terminal punctuation and the following line starts with CJK or Latin characters)

### Table Processing Strategy

1. **LR Table Extraction**: The SDK layout analysis detects table regions, extracts cell text by row and column, and outputs Markdown pipe-table format
2. **Pseudo-Table Filtering**: Heuristic detection of single-row, few-column "tables" — if the merged text matches a section number pattern, it is reclassified as a heading rather than a table

---

## FAQ

### Q: SDK initialization fails?

The most common cause is an invalid license. The trial license bundled with this project has expired — see [Configure Foxit PDF SDK License](#4-configure-foxit-pdf-sdk-license-required) above to obtain and set your own license.

Check that `FOXIT_SN` and `FOXIT_KEY` in `app/config.py` are correct, or that the environment variables are set. Error code meanings:
- `e_ErrInvalidLicense` — License is invalid or expired
- `e_ErrParam` — Parameter format error

### Q: Garbled text in Chinese PDFs?

Foxit SDK includes built-in Chinese font support, so garbled text is uncommon. If it occurs, ensure Chinese fonts are installed on the system.

### Q: Images are not extracted?

- Confirm the "Extract Images" option is checked
- Some images in PDFs exist as vector paths (PathObject) rather than bitmaps (ImageObject); these are not currently supported
- Check write permissions for the `output/images/` directory

### Q: Conversion is slow?

- Large PDFs take longer to parse; the main bottleneck is per-character information extraction
- If precise text styling isn't needed, modify `pdf_parser.py` to use `TextPage.GetText()` instead of per-character iteration
- Disable `reload` mode in production

### Q: How to handle encrypted PDFs?

The current version does not support password-protected PDFs. Uploading an encrypted file will return a 400 error.

---

## License

This project uses the Foxit PDF SDK and is subject to the Foxit commercial license agreement.
