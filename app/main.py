"""Copyright (c) 2026 Zhi Lin. All rights reserved.
Author: zhi_lin@qq.com
"""

"""
FastAPI web service for PDF-to-Markdown conversion.
Provides REST API endpoints and a simple web UI.
"""

import os
import time
import logging
import shutil
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import UPLOAD_DIR, OUTPUT_DIR, IMAGES_DIR
from .pdf_parser import parse_pdf
from .md_converter import convert_to_markdown

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="PDF2MD Service",
    description="Convert PDF documents to Markdown using Foxit PDF SDK",
    version="1.0.0",
)

# Mount static files
templates_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
static_dir = os.path.join(os.path.dirname(__file__), "..", "static")
os.makedirs(templates_dir, exist_ok=True)
os.makedirs(static_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
app.mount("/output", StaticFiles(directory=OUTPUT_DIR), name="output")
templates = Jinja2Templates(directory=templates_dir)


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Serve the web UI."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/api/convert")
async def convert_pdf(
    file: UploadFile = File(...),
    include_images: bool = Form(True),
    include_toc: bool = Form(False),
    skip_header_footer: bool = Form(True),
    html_merged_table: bool = Form(False),
):
    """
    Upload a PDF and get Markdown output.
    
    Returns JSON with:
    - markdown: the converted text
    - filename: the output filename
    - stats: conversion statistics
    """
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    # Save uploaded file
    timestamp = int(time.time() * 1000)
    safe_name = Path(file.filename).stem
    safe_name = "".join(c for c in safe_name if c.isalnum() or c in "-_ ")[:100]
    upload_path = os.path.join(UPLOAD_DIR, f"{safe_name}_{timestamp}.pdf")

    try:
        with open(upload_path, "wb") as f:
            content = await file.read()
            f.write(content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save uploaded file: {e}")

    # Parse PDF
    try:
        t0 = time.time()
        pdf_content = parse_pdf(upload_path)
        parse_time = time.time() - t0
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("PDF parsing failed")
        raise HTTPException(status_code=500, detail=f"PDF parsing failed: {e}")

    # Convert to Markdown
    try:
        t0 = time.time()
        markdown_text = convert_to_markdown(
            pdf_content,
            include_images=include_images,
            include_toc=include_toc,
            skip_header_footer=skip_header_footer,
            image_base_path="/output/images",
            html_merged_table=html_merged_table,
        )
        convert_time = time.time() - t0
    except Exception as e:
        logger.exception("Markdown conversion failed")
        raise HTTPException(status_code=500, detail=f"Conversion failed: {e}")

    # Save Markdown file
    md_filename = f"{safe_name}_{timestamp}.md"
    md_path = os.path.join(OUTPUT_DIR, md_filename)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(markdown_text)

    # Stats
    total_text_blocks = sum(len(p.text_blocks) for p in pdf_content.pages)
    total_images = sum(len(p.image_blocks) for p in pdf_content.pages)
    total_links = sum(len(p.link_blocks) for p in pdf_content.pages)
    total_tables = sum(len(p.table_blocks) for p in pdf_content.pages)

    # Decide download format: ZIP (md + images) or plain md
    has_images = total_images > 0
    if has_images:
        zip_filename = f"{safe_name}_{timestamp}.zip"
        zip_path = os.path.join(OUTPUT_DIR, zip_filename)
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                # Add the markdown file (rewrite image paths as relative)
                md_for_zip = markdown_text.replace("/output/images/", "images/")
                zf.writestr(md_filename, md_for_zip)
                # Add extracted images
                for pg in pdf_content.pages:
                    for img_block in pg.image_blocks:
                        img_abs = os.path.join(IMAGES_DIR, img_block.image_path)
                        if os.path.isfile(img_abs):
                            zf.write(img_abs, f"images/{img_block.image_path}")
            download_url = f"/api/download/{zip_filename}"
            download_filename = zip_filename
        except Exception as e:
            logger.warning(f"Failed to create ZIP: {e}")
            download_url = f"/api/download/{md_filename}"
            download_filename = md_filename
            has_images = False
    else:
        download_url = f"/api/download/{md_filename}"
        download_filename = md_filename

    return JSONResponse({
        "success": True,
        "markdown": markdown_text,
        "filename": download_filename,
        "download_url": download_url,
        "has_images": has_images,
        "stats": {
            "page_count": pdf_content.page_count,
            "text_blocks": total_text_blocks,
            "images_extracted": total_images,
            "tables_extracted": total_tables,
            "links_found": total_links,
            "bookmarks": len(pdf_content.bookmarks),
            "parse_time_ms": round(parse_time * 1000),
            "convert_time_ms": round(convert_time * 1000),
            "title": pdf_content.title,
            "author": pdf_content.author,
        },
    })


@app.get("/api/download/{filename}")
async def download_file(filename: str):
    """Download a converted Markdown (.md) or packaged (.zip) file."""
    filepath = os.path.join(OUTPUT_DIR, filename)
    if not os.path.isfile(filepath):
        raise HTTPException(status_code=404, detail="File not found.")

    if filename.lower().endswith(".zip"):
        media = "application/zip"
    else:
        media = "text/markdown"

    return FileResponse(
        filepath,
        media_type=media,
        filename=filename,
    )


@app.get("/api/health")
async def health():
    """Health check endpoint."""
    return {"status": "ok", "service": "pdf2md"}
