"""Copyright (c) 2026 Zhi Lin. All rights reserved.
Author: zhi_lin@qq.com
"""

"""
PDF Parser using Foxit PDF SDK.
Extracts structured content from PDF: text blocks with font info,
images, bookmarks, links, and tables.
"""

import gc
import os
import re
import logging
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import FoxitPDFSDKPython3 as fsdk

from .config import FOXIT_SN, FOXIT_KEY, IMAGES_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes for parsed content
# ---------------------------------------------------------------------------

@dataclass
class TextBlock:
    """A contiguous block of text with uniform style."""
    text: str
    font_name: str = ""
    font_size: float = 0.0
    is_bold: bool = False
    is_italic: bool = False
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)  # left, bottom, right, top
    page_index: int = 0


@dataclass
class ImageBlock:
    """An extracted image."""
    image_path: str  # saved file path
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)
    page_index: int = 0
    width: int = 0
    height: int = 0


@dataclass
class LinkBlock:
    """A hyperlink found in text."""
    url: str
    text: str = ""
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)
    page_index: int = 0


@dataclass
class BookmarkItem:
    """A bookmark (outline) entry."""
    title: str
    level: int = 0
    page_index: int = -1
    children: List["BookmarkItem"] = field(default_factory=list)


@dataclass
class TableCell:
    """A single cell in a table."""
    text: str = ""
    colspan: int = 1
    rowspan: int = 1
    is_header: bool = False
    bbox: Optional[Tuple[float, float, float, float]] = None  # (left, bottom, right, top)


@dataclass
class TableBlock:
    """A table extracted from a page via LR module."""
    rows: List[List[TableCell]] = field(default_factory=list)
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)
    page_index: int = 0


@dataclass
class LRHeadingInfo:
    """Heading detected by the LR (Layout Recognition) module."""
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)
    level: int = 1  # 1-6
    text: str = ""


@dataclass
class CodeBlock:
    """A code block reconstructed from a misidentified table."""
    lines: List[str] = field(default_factory=list)
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)
    page_index: int = 0


@dataclass
class PageContent:
    """All extracted content from a single page."""
    page_index: int
    width: float = 0.0
    height: float = 0.0
    text_blocks: List[TextBlock] = field(default_factory=list)
    image_blocks: List[ImageBlock] = field(default_factory=list)
    link_blocks: List[LinkBlock] = field(default_factory=list)
    table_blocks: List[TableBlock] = field(default_factory=list)
    code_blocks: List[CodeBlock] = field(default_factory=list)
    lr_headings: List[LRHeadingInfo] = field(default_factory=list)
    lr_paragraphs: List[Tuple[float, float, float, float]] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class PDFContent:
    """Complete parsed PDF content."""
    title: str = ""
    author: str = ""
    page_count: int = 0
    bookmarks: List[BookmarkItem] = field(default_factory=list)
    pages: List[PageContent] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SDK Initialization
# ---------------------------------------------------------------------------

_sdk_initialized = False
_sdk_lock = threading.Lock()


def _ensure_sdk():
    """Initialize Foxit SDK if not already done (thread-safe)."""
    global _sdk_initialized
    if _sdk_initialized:
        return
    with _sdk_lock:
        if not _sdk_initialized:
            error_code = fsdk.Library.Initialize(FOXIT_SN, FOXIT_KEY)
            if error_code != fsdk.e_ErrSuccess:
                raise RuntimeError(
                    f"Failed to initialize Foxit PDF SDK. Error code: {error_code}"
                )
            _sdk_initialized = True
            logger.info("Foxit PDF SDK initialized successfully.")


# ---------------------------------------------------------------------------
# Bookmark extraction
# ---------------------------------------------------------------------------

def _extract_bookmarks(
    root_bookmark: fsdk.Bookmark,
    doc: fsdk.PDFDoc,
    level: int = 0,
) -> List[BookmarkItem]:
    """Recursively extract bookmarks.

    *doc* is required by ``Destination.GetPageIndex(doc)`` in SDK 11.0.
    """
    items = []
    try:
        if root_bookmark.IsEmpty():
            return items
    except Exception:
        return items

    current = root_bookmark
    while True:
        try:
            if current.IsEmpty():
                break
        except Exception:
            break

        try:
            title = current.GetTitle()
        except Exception:
            title = ""

        page_index = -1
        try:
            dest = current.GetDestination()
            if not dest.IsEmpty():
                page_index = dest.GetPageIndex(doc)
        except Exception:
            pass

        item = BookmarkItem(title=title, level=level, page_index=page_index)

        # Recurse into children
        try:
            first_child = current.GetFirstChild()
            if not first_child.IsEmpty():
                item.children = _extract_bookmarks(first_child, doc, level + 1)
        except Exception:
            pass

        items.append(item)

        # Move to next sibling
        try:
            current = current.GetNextSibling()
            if current.IsEmpty():
                break
        except Exception:
            break

    return items


# ---------------------------------------------------------------------------
# Text extraction with style info
# ---------------------------------------------------------------------------

def _extract_text_blocks(
    page: fsdk.PDFPage,
    page_index: int,
    exclude_bboxes: Optional[List[Tuple[float, float, float, float]]] = None,
    include_bbox: Optional[Tuple[float, float, float, float]] = None,
) -> Tuple[List[TextBlock], str]:
    """
    Extract text blocks from a page. Groups consecutive characters with 
    similar font/size into blocks. Also returns the raw page text.

    Characters whose centre falls inside any box in *exclude_bboxes*
    (typically table regions) are skipped so that table text is not
    duplicated in the normal text flow.

    If *include_bbox* is given, only characters whose centre falls
    inside this rectangle are included (used for re-extracting text
    from a specific table region).
    """
    text_page = fsdk.TextPage(page, fsdk.TextPage.e_ParseTextNormal)
    
    raw_text = ""
    try:
        raw_text = text_page.GetText(fsdk.TextPage.e_TextDisplayOrder)
    except Exception:
        pass

    char_count = text_page.GetCharCount()
    if char_count <= 0:
        return [], raw_text

    blocks: List[TextBlock] = []
    current_text = ""
    current_font_name = ""
    current_font_size = 0.0
    dominant_font_size = 0.0  # primary (non-superscript) font size in block
    current_is_bold = False
    current_is_italic = False
    current_bbox = [float('inf'), float('inf'), float('-inf'), float('-inf')]

    def _flush_block():
        nonlocal current_text, current_font_name, current_font_size
        nonlocal dominant_font_size
        nonlocal current_is_bold, current_is_italic, current_bbox
        stripped = current_text.strip()
        if stripped:
            blocks.append(TextBlock(
                text=stripped,
                font_name=current_font_name,
                font_size=round(
                    dominant_font_size if dominant_font_size > 0 else current_font_size, 1
                ),
                is_bold=current_is_bold,
                is_italic=current_is_italic,
                bbox=tuple(current_bbox),
                page_index=page_index,
            ))
        current_text = ""
        current_font_name = ""
        current_font_size = 0.0
        dominant_font_size = 0.0
        current_is_bold = False
        current_is_italic = False
        current_bbox = [float('inf'), float('inf'), float('-inf'), float('-inf')]

    for i in range(char_count):
        try:
            char_info = text_page.GetCharInfo(i)
        except Exception:
            continue

        # Skip characters inside table regions
        if exclude_bboxes:
            try:
                cbox = char_info.char_box
                cx = (cbox.left + cbox.right) / 2.0
                cy = (cbox.bottom + cbox.top) / 2.0
                if _point_in_any_table(cx, cy, exclude_bboxes):
                    continue
            except Exception:
                pass

        # Only include characters inside include_bbox
        if include_bbox:
            try:
                cbox = char_info.char_box
                cx = (cbox.left + cbox.right) / 2.0
                cy = (cbox.bottom + cbox.top) / 2.0
                il, ib, ir, it = include_bbox
                if not (il - 2 <= cx <= ir + 2 and ib - 2 <= cy <= it + 2):
                    continue
            except Exception:
                pass

        # Get character text
        try:
            ch = text_page.GetChars(i, 1)
        except Exception:
            ch = ""

        if not ch:
            continue

        # Get font info
        font_name = ""
        font_size = 0.0
        is_bold = False
        is_italic = False
        try:
            font = char_info.font
            font_size = char_info.font_size
            if not font.IsEmpty():
                font_name = font.GetName()
                is_bold = font.IsBold()
                is_italic = font.IsItalic()
        except Exception:
            pass

        # Update bounding box
        try:
            cbox = char_info.char_box
            if cbox.left < current_bbox[0]:
                current_bbox[0] = cbox.left
            if cbox.bottom < current_bbox[1]:
                current_bbox[1] = cbox.bottom
            if cbox.right > current_bbox[2]:
                current_bbox[2] = cbox.right
            if cbox.top > current_bbox[3]:
                current_bbox[3] = cbox.top
        except Exception:
            pass

        # --- Superscript / subscript detection ---
        # Use the block's dominant (body) font size as reference
        ref_size = dominant_font_size if dominant_font_size > 0 else current_font_size

        is_super_or_sub = (
            ref_size > 0 and font_size > 0 and
            font_size < ref_size * 0.82
        )

        # Returning from superscript back to normal text
        is_return_from_super = (
            current_font_size > 0 and font_size > 0 and
            ref_size > 0 and
            current_font_size < ref_size * 0.85 and
            font_size >= ref_size * 0.85
        )

        # Only split on significant *structural* style changes,
        # NOT on inline superscript / subscript / minor font variations
        style_changed = False
        if current_text and not is_super_or_sub and not is_return_from_super:
            size_diff = abs(font_size - ref_size) if ref_size > 0 else 0
            if size_diff > 2.0 and font_size > ref_size * 1.2:
                # Font size increased significantly → likely heading boundary
                style_changed = True
            elif is_bold != current_is_bold and size_diff > 2.0:
                # Bold change with notable size difference → structural
                style_changed = True

        # Also split on newlines
        if ch in ('\n', '\r'):
            _flush_block()
            continue

        if style_changed:
            _flush_block()

        current_text += ch

        # Update tracking: only update dominant style for non-superscript chars
        if not is_super_or_sub:
            current_font_name = font_name or current_font_name
            current_font_size = font_size if font_size > 0 else current_font_size
            current_is_bold = is_bold
            current_is_italic = is_italic
            if font_size > dominant_font_size:
                dominant_font_size = font_size
        elif not current_font_name and font_name:
            current_font_name = font_name

    _flush_block()
    return blocks, raw_text


# ---------------------------------------------------------------------------
# Image extraction
# ---------------------------------------------------------------------------

def _extract_images(page: fsdk.PDFPage, page_index: int, pdf_name: str) -> List[ImageBlock]:
    """Extract images from page graphics objects.

    PDFPage inherits from GraphicsObjects, so we can call the
    graphics-object enumeration methods directly on *page*.

    SDK 11.0 notes
    ---------------
    - GraphicsObject has NO ``IsEmpty()`` method.
    - Use ``gfx_obj.GetImageObject()`` instead of ``fsdk.ImageObject(gfx_obj)``.
    - Use ``img_obj.CloneBitmap(page)`` instead of ``img_obj.GetBitmap()``.
      ``CloneBitmap`` requires a *GraphicsObjects* argument; *PDFPage* inherits
      from *GraphicsObjects* so passing *page* works.
    """
    images: List[ImageBlock] = []
    img_counter = 0

    try:
        pos = page.GetFirstGraphicsObjectPosition(
            fsdk.GraphicsObject.e_TypeImage
        )

        while pos:
            try:
                gfx_obj = page.GetGraphicsObject(pos)
                pos = page.GetNextGraphicsObjectPosition(
                    pos, fsdk.GraphicsObject.e_TypeImage
                )
            except Exception:
                break

            if gfx_obj is None:
                continue

            try:
                img_obj = gfx_obj.GetImageObject()
                if img_obj is None:
                    continue

                bitmap = img_obj.CloneBitmap(page)
                if bitmap is None or bitmap.IsEmpty():
                    continue

                w = bitmap.GetWidth()
                h = bitmap.GetHeight()

                # Skip tiny decorations (< 20×20 px)
                if w < 20 or h < 20:
                    continue

                # Bounding box
                rect = gfx_obj.GetRect()
                bbox = (rect.left, rect.bottom, rect.right, rect.top)

                # Save image
                img_counter += 1
                img_filename = (
                    f"{pdf_name}_p{page_index + 1}_img{img_counter}.png"
                )
                img_path = os.path.join(IMAGES_DIR, img_filename)

                image = fsdk.Image()
                image.AddFrame(bitmap)
                image.SaveAs(img_path)

                images.append(ImageBlock(
                    image_path=img_filename,
                    bbox=bbox,
                    page_index=page_index,
                    width=w,
                    height=h,
                ))
                logger.info(
                    f"Extracted image p{page_index+1} #{img_counter}: "
                    f"{w}x{h}  -> {img_filename}"
                )
            except Exception as e:
                logger.warning(
                    f"Failed to extract image #{img_counter+1} "
                    f"on page {page_index}: {e}"
                )
                continue

    except Exception as e:
        logger.warning(f"Image extraction failed on page {page_index}: {e}")

    return images


# ---------------------------------------------------------------------------
# FormXObject (embedded vector drawing) detection
# ---------------------------------------------------------------------------

def _collect_formxobject_bboxes(
    page: fsdk.PDFPage,
) -> List[Tuple[float, float, float, float]]:
    """Return bounding boxes of all FormXObject graphics on *page*.

    FormXObjects are self-contained vector drawings (org charts, flow
    diagrams, etc.) embedded in the PDF.  Normal tables do NOT contain
    FormXObjects, so their presence inside a detected LR-Table region
    is a strong signal that the "table" is actually a chart/diagram.
    """
    bboxes: List[Tuple[float, float, float, float]] = []
    try:
        pos = page.GetFirstGraphicsObjectPosition(
            fsdk.GraphicsObject.e_TypeFormXObject
        )
        while pos:
            try:
                gobj = page.GetGraphicsObject(pos)
                pos = page.GetNextGraphicsObjectPosition(
                    pos, fsdk.GraphicsObject.e_TypeFormXObject
                )
            except Exception:
                break
            if gobj is None:
                continue
            rect = gobj.GetRect()
            bboxes.append((rect.left, rect.bottom, rect.right, rect.top))
    except Exception:
        pass
    return bboxes


def _bbox_overlap_ratio(
    inner: Tuple[float, float, float, float],
    outer: Tuple[float, float, float, float],
) -> float:
    """Return the fraction of *inner*'s area that overlaps with *outer*.

    Both bboxes are (left, bottom, right, top).  Returns 0.0 – 1.0.
    """
    x1 = max(inner[0], outer[0])
    y1 = max(inner[1], outer[1])
    x2 = min(inner[2], outer[2])
    y2 = min(inner[3], outer[3])
    if x2 <= x1 or y2 <= y1:
        return 0.0
    overlap_area = (x2 - x1) * (y2 - y1)
    inner_area = (inner[2] - inner[0]) * (inner[3] - inner[1])
    if inner_area <= 0:
        return 0.0
    return overlap_area / inner_area


# ---------------------------------------------------------------------------
# Table extraction via LR (Layout Recognition) module
# ---------------------------------------------------------------------------

# LR element type constants (module-level for reuse)
_LR_DIV = fsdk.LRElement.e_ElementTypeDiv
_LR_TABLE = fsdk.LRElement.e_ElementTypeTable
_LR_TR = fsdk.LRElement.e_ElementTypeTableRow
_LR_TD = fsdk.LRElement.e_ElementTypeTableDataCell
_LR_TH = fsdk.LRElement.e_ElementTypeTableHeaderCell
_LR_TBODY = fsdk.LRElement.e_ElementTypeTableBodyGroup
_LR_THEAD = fsdk.LRElement.e_ElementTypeTableHeaderGroup
_LR_TFOOT = fsdk.LRElement.e_ElementTypeTableFootGroup

# Paragraph element type
_LR_PARAGRAPH = fsdk.LRElement.e_ElementTypeParagraph

# Figure element type
_LR_FIGURE = fsdk.LRElement.e_ElementTypeFigure

# Heading element types
_LR_HEADING = fsdk.LRElement.e_ElementTypeHeading
_LR_H1 = fsdk.LRElement.e_ElementTypeHeading1
_LR_H2 = fsdk.LRElement.e_ElementTypeHeading2
_LR_H3 = fsdk.LRElement.e_ElementTypeHeading3
_LR_H4 = fsdk.LRElement.e_ElementTypeHeading4
_LR_H5 = fsdk.LRElement.e_ElementTypeHeading5
_LR_H6 = fsdk.LRElement.e_ElementTypeHeading6
_LR_HN = fsdk.LRElement.e_ElementTypeHeadingN
_LR_TITLE = fsdk.LRElement.e_ElementTypeTitle

_LR_HEADING_MAP = {
    _LR_HEADING: 1, _LR_H1: 1, _LR_H2: 2, _LR_H3: 3,
    _LR_H4: 4, _LR_H5: 5, _LR_H6: 6, _LR_HN: 1, _LR_TITLE: 1,
}


def _text_in_rect(
    text_page: fsdk.TextPage,
    bbox,
    char_count: int,
) -> str:
    """Extract text from *text_page* whose character centres fall inside *bbox*."""
    chars = []
    for i in range(char_count):
        try:
            info = text_page.GetCharInfo(i)
            cx = (info.char_box.left + info.char_box.right) / 2.0
            cy = (info.char_box.bottom + info.char_box.top) / 2.0
            if (bbox.left - 2 <= cx <= bbox.right + 2 and
                    bbox.bottom - 2 <= cy <= bbox.top + 2):
                ch = text_page.GetChars(i, 1)
                if ch:
                    chars.append(ch)
        except Exception:
            pass
    return "".join(chars).strip()


def _collect_lr_elements(
    elem, tables: list, headings: list, paragraphs: list,
    figures: Optional[list] = None,
) -> None:
    """Recursively collect Table, Heading, Paragraph, and Figure elements from an LR tree."""
    et = elem.GetElementType()
    if et == _LR_TABLE:
        tables.append(elem)
        return
    if et in _LR_HEADING_MAP:
        headings.append(elem)
        return
    if et == _LR_PARAGRAPH:
        # Collect paragraph bbox for paragraph-merging in converter
        try:
            se = fsdk.LRStructureElement(elem)
            bbox = se.GetBBox()
            paragraphs.append((bbox.left, bbox.bottom, bbox.right, bbox.top))
        except Exception:
            pass
        return  # paragraphs are leaf-level in LR for our purposes
    if et == _LR_FIGURE and figures is not None:
        try:
            se = fsdk.LRStructureElement(elem)
            bbox = se.GetBBox()
            figures.append((bbox.left, bbox.bottom, bbox.right, bbox.top))
        except Exception:
            pass
        return
    if elem.IsStructureElement():
        se = fsdk.LRStructureElement(elem)
        for i in range(se.GetChildCount()):
            child = se.GetChild(i)
            if not child.IsEmpty():
                _collect_lr_elements(child, tables, headings, paragraphs, figures)


def _analyze_lr_subtree(elem) -> Tuple[int, bool]:
    """Count Figure descendants and check for Table descendants.

    Returns ``(figure_count, has_table)``.
    """
    et = elem.GetElementType()
    if et == _LR_FIGURE:
        return 1, False
    if et == _LR_TABLE:
        return 0, True
    fig_count = 0
    has_table = False
    if elem.IsStructureElement():
        se = fsdk.LRStructureElement(elem)
        for i in range(se.GetChildCount()):
            child = se.GetChild(i)
            if not child.IsEmpty():
                fc, ht = _analyze_lr_subtree(child)
                fig_count += fc
                has_table = has_table or ht
    return fig_count, has_table


_FIGURE_CONTAINER_MIN_FIGURES = 3   # min Figure descendants for a Div to be a container
_FIGURE_CONTAINER_MAX_AREA = 0.40   # max fraction of page area


def _find_figure_container_bboxes(
    elem,
    page_area: float,
    results: List[Tuple[float, float, float, float]],
) -> None:
    """Find Div elements whose subtrees contain significant Figure content.

    A Div that has ≥ ``_FIGURE_CONTAINER_MIN_FIGURES`` Figure descendants
    and no Table descendants is treated as a *figure container*.  Its bbox
    properly encompasses text labels and annotations that belong to the
    figure diagram but are not themselves Figure elements in the LR tree.
    """
    if not elem.IsStructureElement():
        return
    se = fsdk.LRStructureElement(elem)
    et = elem.GetElementType()

    # Never recurse into Table elements — Divs inside a table's LR
    # subtree must not be treated as figure containers.
    if et == _LR_TABLE:
        return

    if et == _LR_DIV:
        fig_count, has_table = _analyze_lr_subtree(elem)
        if fig_count >= _FIGURE_CONTAINER_MIN_FIGURES and not has_table:
            try:
                bbox = se.GetBBox()
                area = (bbox.right - bbox.left) * (bbox.top - bbox.bottom)
                if 0 < area and area / page_area <= _FIGURE_CONTAINER_MAX_AREA:
                    results.append((bbox.left, bbox.bottom, bbox.right, bbox.top))
                    return  # don't recurse — entire Div is one figure region
            except Exception:
                pass

    for i in range(se.GetChildCount()):
        child = se.GetChild(i)
        if not child.IsEmpty():
            _find_figure_container_bboxes(child, page_area, results)


# ---------------------------------------------------------------------------
# Figure region rendering
# ---------------------------------------------------------------------------

_FIGURE_RENDER_DPI = 150
_FIGURE_MIN_AREA_RATIO = 0.01   # ignore clusters < 1% of page area
_FIGURE_MAX_AREA_RATIO = 0.35   # ignore clusters > 35% of page area (likely false positives)
_FIGURE_CLUSTER_GAP = 20.0     # max gap (pt) to merge adjacent figures


def _cluster_figure_bboxes(
    fig_bboxes: List[Tuple[float, float, float, float]],
    page_width: float,
    page_height: float,
) -> List[Tuple[float, float, float, float]]:
    """Cluster nearby LR Figure bboxes into composite figure regions.

    Returns a list of merged bounding boxes for figure clusters that
    are large enough to be meaningful (> _FIGURE_MIN_AREA_RATIO of page).
    """
    if not fig_bboxes:
        return []

    page_area = page_width * page_height
    gap = _FIGURE_CLUSTER_GAP

    # Simple greedy merge: expand each bbox by `gap`, merge overlapping.
    # Start with a copy sorted by bottom coordinate.
    clusters: List[List[float]] = []
    for l, b, r, t in sorted(fig_bboxes, key=lambda x: x[1]):
        merged = False
        for c in clusters:
            # Check overlap with gap tolerance
            if (l - gap <= c[2] and r + gap >= c[0] and
                    b - gap <= c[3] and t + gap >= c[1]):
                c[0] = min(c[0], l)
                c[1] = min(c[1], b)
                c[2] = max(c[2], r)
                c[3] = max(c[3], t)
                merged = True
                break
        if not merged:
            clusters.append([l, b, r, t])

    # Iteratively merge clusters that now overlap after expansion
    changed = True
    while changed:
        changed = False
        new_clusters: List[List[float]] = []
        for c in clusters:
            merged = False
            for nc in new_clusters:
                if (c[0] - gap <= nc[2] and c[2] + gap >= nc[0] and
                        c[1] - gap <= nc[3] and c[3] + gap >= nc[1]):
                    nc[0] = min(nc[0], c[0])
                    nc[1] = min(nc[1], c[1])
                    nc[2] = max(nc[2], c[2])
                    nc[3] = max(nc[3], c[3])
                    merged = True
                    changed = True
                    break
            if not merged:
                new_clusters.append(c)
        clusters = new_clusters

    # Filter by area bounds and add small margin
    result = []
    margin = 3.0
    for l, b, r, t in clusters:
        ratio = (r - l) * (t - b) / page_area
        if _FIGURE_MIN_AREA_RATIO <= ratio <= _FIGURE_MAX_AREA_RATIO:
            result.append((
                max(0, l - margin),
                max(0, b - margin),
                min(page_width, r + margin),
                min(page_height, t + margin),
            ))
    return result


def _render_page_region(
    page: fsdk.PDFPage,
    bbox: Tuple[float, float, float, float],
    page_index: int,
    pdf_name: str,
    fig_counter: int,
    dpi: int = _FIGURE_RENDER_DPI,
) -> Optional[ImageBlock]:
    """Render a rectangular region of a PDF page to a PNG image.

    Uses the Foxit SDK Renderer to rasterize the page content within
    *bbox* (left, bottom, right, top in PDF coordinates).
    """
    fig_left, fig_bottom, fig_right, fig_top = bbox
    fig_w = fig_right - fig_left
    fig_h = fig_top - fig_bottom
    if fig_w <= 0 or fig_h <= 0:
        return None

    scale = dpi / 72.0
    bmp_w = int(fig_w * scale)
    bmp_h = int(fig_h * scale)
    if bmp_w < 10 or bmp_h < 10:
        return None

    try:
        bmp = fsdk.Bitmap(bmp_w, bmp_h, fsdk.Bitmap.e_DIBArgb)
        bmp.FillRect(0xFFFFFFFF, None)

        renderer = fsdk.Renderer(bmp, False)

        # Compute display matrix that maps the figure region to (0,0)
        pw = page.GetWidth()
        ph = page.GetHeight()
        full_w = int(pw * scale)
        full_h = int(ph * scale)
        x_off = int(-fig_left * scale)
        y_off = int(-(ph - fig_top) * scale)
        matrix = page.GetDisplayMatrix(x_off, y_off, full_w, full_h, 0)

        renderer.StartRender(page, matrix, None)

        img_filename = f"{pdf_name}_p{page_index + 1}_fig{fig_counter}.png"
        img_path = os.path.join(IMAGES_DIR, img_filename)

        image = fsdk.Image()
        image.AddFrame(bmp)
        image.SaveAs(img_path)

        logger.info(
            f"Rendered figure p{page_index+1} #{fig_counter}: "
            f"{bmp_w}x{bmp_h} -> {img_filename}"
        )
        return ImageBlock(
            image_path=img_filename,
            bbox=bbox,
            page_index=page_index,
            width=bmp_w,
            height=bmp_h,
        )
    except Exception as e:
        logger.warning(
            f"Failed to render figure region p{page_index+1}: {e}"
        )
        return None


def _extract_lr_row(row_elem, text_page: fsdk.TextPage, char_count: int) -> List[TableCell]:
    """Extract cells from a single TR element."""
    row_se = fsdk.LRStructureElement(row_elem)
    cells: List[TableCell] = []
    for i in range(row_se.GetChildCount()):
        cell = row_se.GetChild(i)
        cell_et = cell.GetElementType()
        if cell_et not in (_LR_TD, _LR_TH):
            continue
        cell_se = fsdk.LRStructureElement(cell)
        bbox = cell_se.GetBBox()
        txt = _text_in_rect(text_page, bbox, char_count)
        # Clean up embedded newlines / carriage returns inside cells
        txt = txt.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").strip()

        # ColSpan / RowSpan
        colspan = 1
        rowspan = 1
        try:
            for ai in range(cell_se.GetSupportedAttributeCount()):
                attr = cell_se.GetSupportedAttribute(ai)
                if attr == fsdk.LRStructureElement.e_AttributeTypeColSpan:
                    colspan = cell_se.GetAttributeValueInt32(attr)
                elif attr == fsdk.LRStructureElement.e_AttributeTypeRowSpan:
                    rowspan = cell_se.GetAttributeValueInt32(attr)
        except Exception:
            pass

        cells.append(TableCell(
            text=txt,
            colspan=colspan,
            rowspan=rowspan,
            is_header=(cell_et == _LR_TH),
            bbox=(bbox.left, bbox.bottom, bbox.right, bbox.top),
        ))
    return cells


def _extract_lr_table(
    table_elem,
    text_page: fsdk.TextPage,
    char_count: int,
) -> List[List[TableCell]]:
    """Extract all rows from a Table LR element."""
    se = fsdk.LRStructureElement(table_elem)
    rows: List[List[TableCell]] = []

    for ci in range(se.GetChildCount()):
        child = se.GetChild(ci)
        et = child.GetElementType()

        if et == _LR_TR:
            cells = _extract_lr_row(child, text_page, char_count)
            if cells:
                rows.append(cells)
        elif et in (_LR_TBODY, _LR_THEAD, _LR_TFOOT):
            group_se = fsdk.LRStructureElement(child)
            for gci in range(group_se.GetChildCount()):
                row = group_se.GetChild(gci)
                if row.GetElementType() == _LR_TR:
                    cells = _extract_lr_row(row, text_page, char_count)
                    if cells:
                        rows.append(cells)
    return rows


class _Rect:
    """Lightweight rectangle for _text_in_rect calls."""
    __slots__ = ("left", "bottom", "right", "top")
    def __init__(self, left: float, bottom: float, right: float, top: float):
        self.left = left
        self.bottom = bottom
        self.right = right
        self.top = top


def _split_merged_row_cells(
    rows: List[List[TableCell]],
    text_page: fsdk.TextPage,
    char_count: int,
) -> List[List[TableCell]]:
    """Split cells that SDK LR module incorrectly merged across columns.

    The LR module sometimes fails to detect column boundaries in some rows,
    returning a single cell spanning multiple expected columns.  This function
    detects such cases by comparing cell counts against the maximum (typically
    the header row), then re-extracts text for each sub-column using column
    positions from complete rows.
    """
    if not rows or len(rows) < 2:
        return rows

    max_cells = max(len(row) for row in rows)
    if max_cells <= 2:
        return rows

    # Only apply when the first row (header) defines the column structure.
    # If the header itself has fewer cells, it may have legitimate merged
    # cells and we have no reliable reference to split by.
    if len(rows[0]) != max_cells or not all(c.bbox for c in rows[0]):
        return rows

    ref_lefts = [c.bbox[0] for c in rows[0]]
    ref_rights = [c.bbox[2] for c in rows[0]]

    # Only proceed if there are incomplete rows
    incomplete = [i for i, row in enumerate(rows) if len(row) < max_cells]
    if not incomplete:
        return rows

    TOLERANCE = 5.0

    def _find_ref_col(x: float) -> int:
        best, best_d = 0, abs(x - ref_lefts[0])
        for i in range(1, max_cells):
            d = abs(x - ref_lefts[i])
            if d < best_d:
                best, best_d = i, d
        return best

    changed = False
    new_rows: List[List[TableCell]] = list(rows)  # shallow copy
    for ri in incomplete:
        row = rows[ri]
        new_row: List[TableCell] = []
        row_changed = False
        for cell in row:
            if not cell.bbox:
                new_row.append(cell)
                continue

            start_col = _find_ref_col(cell.bbox[0])
            end_col = start_col
            for i in range(start_col + 1, max_cells):
                if ref_lefts[i] < cell.bbox[2] - TOLERANCE:
                    end_col = i
                else:
                    break

            span = end_col - start_col + 1
            if span <= 1:
                new_row.append(cell)
            else:
                # Split: re-extract text for each sub-column
                for ci in range(start_col, end_col + 1):
                    sub_l = ref_lefts[ci]
                    sub_r = (ref_rights[ci]
                             if ci < len(ref_rights)
                             else cell.bbox[2])
                    sub_bbox = _Rect(sub_l, cell.bbox[1], sub_r, cell.bbox[3])
                    txt = _text_in_rect(text_page, sub_bbox, char_count)
                    txt = (txt.replace("\r\n", " ")
                              .replace("\r", " ")
                              .replace("\n", " ")
                              .strip())
                    new_row.append(TableCell(
                        text=txt,
                        colspan=1,
                        rowspan=1,
                        is_header=cell.is_header,
                        bbox=(sub_l, cell.bbox[1], sub_r, cell.bbox[3]),
                    ))
                row_changed = True

        if row_changed:
            new_rows[ri] = new_row
            changed = True

    return new_rows if changed else rows


# Regex for bibliography numbering like [1], [23], [100]
_RE_BIB_NUM = re.compile(r"^\s*\[\d+\]")

# Regex for section / chapter heading patterns.
# Used to detect single-row "tables" that are actually wrapped headings.
_RE_SECTION_HEADING = re.compile(
    r"^\s*("
    r"\d+(\.\d+)+\.?\s"                                    # "3.2 xxx", "1.2.3 xxx"
    r"|第[一二三四五六七八九十百千万\d]+[章节篇部编条款]"   # "第三章", "第1节"
    r"|[一二三四五六七八九十]+\s*[、.]\s*\S"                # "一、xxx"
    r"|（[一二三四五六七八九十\d]+）"                       # "（一）xxx"
    r")"
)

# Patterns for detecting programming code lines
_RE_CODE_ASSIGN = re.compile(r'\w+\s*=\s*\S')          # variable assignment
_RE_CODE_CALL = re.compile(r'\w+\s*\(')                 # function call
_RE_CODE_KEYWORD = re.compile(
    r'\b(?:import|from|def|class|return|if|else|elif|for|'
    r'while|print|with|try|except|raise|lambda|yield|async|await)\b'
)
_RE_CODE_COMMENT = re.compile(r'#\s*\S')                # inline comment
_RE_CODE_METHOD = re.compile(r'\.\s*\w+\s*\(')          # method call


def _is_false_table(rows: List[List[TableCell]]) -> str:
    """Heuristic: detect LR "tables" that are actually bibliography lists,
    wrapped section headings, or code blocks.

    Returns a reason string:
    - ""  (empty) → real table
    - "bib"     → bibliography list
    - "heading" → wrapped section heading
    - "code"    → misidentified code block
    - "empty"   → empty table

    Common false-positive patterns:
    1. Many rows are completely empty (no text in any cell).
    2. Non-empty rows contain bibliography entries starting with ``[N]``.
    3. Very few columns (1-2) with most cells empty — not a real tabular
       structure.
    4. A single-row table with 1-3 columns whose combined text matches a
       section heading pattern.
    5. A sparse multi-column table whose rows contain programming code
       patterns.
    """
    if not rows:
        return "empty"

    total_rows = len(rows)
    empty_rows = 0
    bib_rows = 0
    non_empty_rows = 0

    for row in rows:
        # Concatenate all cell text in this row
        row_text = " ".join(c.text.strip() for c in row).strip()
        if not row_text:
            empty_rows += 1
            continue
        non_empty_rows += 1
        if _RE_BIB_NUM.match(row_text):
            bib_rows += 1

    # If more than half the rows are empty AND the majority of non-empty
    # rows look like bibliography entries → it's a false table.
    if non_empty_rows > 0 and bib_rows / non_empty_rows >= 0.5 and empty_rows >= non_empty_rows:
        return "bib"

    # If ALL non-empty rows are bibliography entries → also reject
    if non_empty_rows > 0 and bib_rows == non_empty_rows:
        return "bib"

    # --- Single-row "tables" that are actually wrapped section headings ---
    max_cols = max(len(row) for row in rows) if rows else 0
    if non_empty_rows <= 1 and max_cols <= 3:
        all_text = re.sub(
            r"\s+", " ",
            " ".join(c.text.strip() for row in rows for c in row),
        ).strip()
        if _RE_SECTION_HEADING.match(all_text):
            logger.debug(
                f"Rejected false table (heading): {all_text[:80]}"
            )
            return "heading"

    # --- Code block detection ---
    _code_patterns = [
        _RE_CODE_ASSIGN, _RE_CODE_CALL, _RE_CODE_KEYWORD,
        _RE_CODE_COMMENT, _RE_CODE_METHOD,
    ]
    if max_cols >= 3 and non_empty_rows >= 2:
        sparse_rows = 0
        code_like_rows = 0
        for row in rows:
            texts = [c.text.strip() for c in row]
            non_empty_count = sum(1 for t in texts if t)
            if non_empty_count <= 2:
                sparse_rows += 1
            combined = " ".join(texts)
            if combined.strip():
                score = sum(1 for p in _code_patterns if p.search(combined))
                if score >= 2:
                    code_like_rows += 1

        if (sparse_rows >= total_rows * 0.6
                and code_like_rows >= 2
                and non_empty_rows > 0
                and code_like_rows / non_empty_rows >= 0.5):
            logger.debug(
                f"Rejected false table (code block): "
                f"{total_rows} rows, {max_cols} cols, "
                f"{code_like_rows}/{non_empty_rows} code-like rows"
            )
            return "code"

    # --- Single-row multi-column list layouts ---
    # Detected but NOT rejected here — handled by _restructure_multicolumn_list
    # downstream to convert into a proper multi-row table.

    return ""


def _restructure_multicolumn_list(rows: List[List[TableCell]]) -> Optional[List[List[TableCell]]]:
    """If a table is a single-row, multi-column list (many names per cell),
    restructure it into a multi-row table with one item per cell.

    Returns restructured rows, or None if the pattern doesn't match.
    """
    if len(rows) != 1:
        return None
    max_cols = len(rows[0])
    if not (2 <= max_cols <= 4):
        return None

    cell_texts = [c.text.strip() for c in rows[0]]
    if not any(len(t) > 80 for t in cell_texts):
        return None

    # Split each cell's text into individual names.
    # Names in the extracted text are typically separated by whitespace,
    # but CJK names followed by punctuation/company suffixes make simple
    # splitting tricky.  We try splitting by double-space or newline first,
    # then fall back to splitting by "有限公司" / "研究院" etc. boundaries.
    import re as _re

    def _split_names(text: str) -> List[str]:
        # First, try splitting by two or more spaces or newline
        parts = _re.split(r'\s{2,}|\n', text)
        parts = [p.strip() for p in parts if p.strip()]
        if len(parts) > 3:
            return parts
        # Fallback: split on CJK organization name boundaries.
        # Insert a marker after common org suffixes followed by a space
        # and the start of a new name.
        _ORG_SUFFIX = (
            r'(?:股份有限公司|有限责任公司|有限公司|股份公司'
            r'|研究中心|研究院|研究所|研究室|实验室'
            r'|大学|学院|中心|协会|集团|总台|技术局'
            r'|分公司|总公司)'
        )
        marked = _re.sub(
            _ORG_SUFFIX + r'\s+',
            lambda m: m.group().rstrip() + '\x00',
            text,
        )
        parts = [p.strip() for p in marked.split('\x00') if p.strip()]
        if len(parts) > 1:
            return parts
        return [text]

    columns = [_split_names(ct) for ct in cell_texts]
    # Pad columns to same length
    max_rows = max(len(col) for col in columns) if columns else 0
    if max_rows <= 1:
        return None

    new_rows = []
    for ri in range(max_rows):
        new_row = []
        for ci, col in enumerate(columns):
            name = col[ri] if ri < len(col) else ""
            new_row.append(TableCell(text=name, colspan=1, rowspan=1, is_header=False))
        new_rows.append(new_row)

    return new_rows


# ---------------------------------------------------------------------------
# Heuristic reconstruction of tables the LR module missed
# ---------------------------------------------------------------------------
# Regex matching table caption patterns: "表 2-1 …", "表3.2 …", etc.
_RE_TABLE_CAPTION = re.compile(
    r"^表\s*\d+[\-–—.．]\d+\s"
)


def _smart_join(a: str, b: str) -> str:
    """Concatenate two fragments; add space when the join point is
    not CJK-to-CJK (Latin text needs a space separator)."""
    if not a:
        return b
    if not b:
        return a
    last = a[-1]
    first = b[0]
    # If both sides are CJK, no space needed (wrapped line)
    if ('\u4e00' <= last <= '\u9fff' and '\u4e00' <= first <= '\u9fff'):
        return a + b
    # Otherwise add a space unless one already exists
    if a.endswith(' ') or b.startswith(' '):
        return a + b
    return a + ' ' + b


def _group_cells(entries: List[Tuple[float, str]]) -> List[Tuple[float, float, str]]:
    """Group consecutive entries into cells by adaptive y-gap detection.

    Returns list of (y_top, y_bottom, merged_text) per cell.
    """
    if not entries:
        return []
    if len(entries) == 1:
        return [(entries[0][0], entries[0][0], entries[0][1])]

    # Compute gaps between consecutive entries
    gaps = []
    for i in range(1, len(entries)):
        gaps.append(entries[i - 1][0] - entries[i][0])

    # Find adaptive threshold via the largest "jump" in sorted gaps.
    sorted_gaps = sorted(gaps)
    if len(sorted_gaps) >= 2:
        max_jump = 0
        best_idx = 0
        for i in range(1, len(sorted_gaps)):
            jump = sorted_gaps[i] - sorted_gaps[i - 1]
            if jump > max_jump:
                max_jump = jump
                best_idx = i
        # If no clear bimodal distribution (max jump < 30% of the
        # median gap), don't group — each entry is its own cell.
        median = sorted_gaps[len(sorted_gaps) // 2]
        if median > 0 and max_jump / median < 0.3:
            return [(y, y, txt) for y, txt in entries]
        cell_gap = (sorted_gaps[best_idx - 1] + sorted_gaps[best_idx]) / 2.0
        cell_gap = max(cell_gap, 10)
    else:
        cell_gap = max(sorted_gaps[0] * 1.5, 15)

    cells: List[Tuple[float, float, str]] = []
    cur_top = entries[0][0]
    cur_bottom = cur_top
    cur_texts = [entries[0][1]]
    for idx in range(1, len(entries)):
        y, txt = entries[idx]
        gap = gaps[idx - 1]
        if gap < cell_gap:
            cur_bottom = y
            cur_texts.append(txt)
        else:
            merged = cur_texts[0]
            for ct in cur_texts[1:]:
                merged = _smart_join(merged, ct)
            cells.append((cur_top, cur_bottom, merged))
            cur_top = y
            cur_bottom = y
            cur_texts = [txt]
    merged = cur_texts[0]
    for ct in cur_texts[1:]:
        merged = _smart_join(merged, ct)
    cells.append((cur_top, cur_bottom, merged))
    return cells


def _reconstruct_missed_tables(
    text_blocks: List[TextBlock],
    lr_headings: List[LRHeadingInfo],
    table_blocks: List[TableBlock],
    page_index: int,
) -> Tuple[List[TableBlock], List[LRHeadingInfo]]:
    """Detect tables that the LR module missed and reconstruct them.

    When the LR module fails to recognise a table, its cells end up as
    ordinary text blocks (and their labels often become LR "headings").
    This heuristic looks for:

    1. A *table caption* text block matching "表 N-N …".
    2. A cluster of text blocks directly below the caption that share a
       *different* font size from the body text and form a multi-column
       grid (≥2 distinct x-positions).
    3. Multiple small LR headings in the same region (grid-like layout).

    When the pattern is found we build a ``TableBlock`` from the clustered
    text, remove consumed LR headings, and exclude those text blocks from
    subsequent body-text merging.

    Returns the (possibly augmented) *table_blocks* list and the (possibly
    pruned) *lr_headings* list.
    """
    if not text_blocks:
        return table_blocks, lr_headings

    new_tables: List[TableBlock] = list(table_blocks)
    remaining_headings: List[LRHeadingInfo] = list(lr_headings)

    # Build set of existing table bboxes so we don't try to reconstruct
    # something that already has a detected table nearby.
    existing_table_bboxes = [tb.bbox for tb in table_blocks]

    for blk_idx, blk in enumerate(text_blocks):
        caption_text = re.sub(r"\s+", " ", blk.text).strip()
        if not _RE_TABLE_CAPTION.match(caption_text):
            continue

        caption_top = blk.bbox[3]  # top of caption
        caption_bottom = blk.bbox[1]

        # Check that no existing table already covers this region
        skip = False
        for eb in existing_table_bboxes:
            # If the caption vertically overlaps an existing table bbox
            if eb[1] - 10 <= caption_top <= eb[3] + 10:
                skip = True
                break
        if skip:
            continue

        # Gather candidate text blocks below the caption (lower y = lower
        # on page in PDF coords) that share the same font size.
        #  - Must be below the caption (top < caption_bottom)
        #  - Must share a similar (non-body) font size
        #  - Stop when we hit a block with a noticeably different font size
        #    or a large vertical gap.
        caption_font = blk.font_size

        # Collect ALL blocks below the caption with same font size
        candidates: List[TextBlock] = []
        for other in text_blocks:
            if other is blk:
                continue
            # Below caption: top of other < bottom of caption (with tolerance)
            if other.bbox[3] >= caption_bottom + 5:
                continue
            # Same font size (within 5% tolerance)
            if caption_font > 0 and abs(other.font_size - caption_font) / caption_font > 0.05:
                continue
            candidates.append(other)

        if len(candidates) < 3:
            continue

        # --- Detect column grid from x-positions ---
        # Cluster left-edge x values to find distinct columns.
        x_lefts = sorted(set(round(c.bbox[0], 0) for c in candidates))
        if len(x_lefts) < 2:
            continue

        # Merge nearby x positions (within 15 units) into column groups
        columns: List[float] = []
        for x in x_lefts:
            if not columns or x - columns[-1] > 15:
                columns.append(x)
            # else: same column, skip

        if len(columns) < 2:
            continue

        # --- Determine the vertical extent of the table ---
        # Use the candidates that align with the detected columns.
        table_candidates: List[TextBlock] = []
        for c in candidates:
            cl = round(c.bbox[0], 0)
            # Check if block's left edge aligns with any column
            if any(abs(cl - col) <= 15 for col in columns):
                table_candidates.append(c)

        if len(table_candidates) < 3:
            continue

        # Table vertical extent
        table_top = max(c.bbox[3] for c in table_candidates)
        table_bottom = min(c.bbox[1] for c in table_candidates)
        table_left = min(c.bbox[0] for c in table_candidates)
        table_right = max(c.bbox[2] for c in table_candidates)

        # --- Check that we have multiple LR headings in this region ---
        # This confirms it's a table the LR module misclassified.
        headings_in_region = []
        for lrh in remaining_headings:
            lh_cy = (lrh.bbox[1] + lrh.bbox[3]) / 2.0
            lh_cx = (lrh.bbox[0] + lrh.bbox[2]) / 2.0
            if (table_bottom - 5 <= lh_cy <= table_top + 5 and
                    table_left - 5 <= lh_cx <= table_right + 5):
                headings_in_region.append(lrh)

        if len(headings_in_region) < 3:
            continue

        # --- Assign blocks to columns ---
        def _col_index(block_left: float) -> int:
            best_ci = 0
            best_dist = abs(block_left - columns[0])
            for ci, col_x in enumerate(columns):
                d = abs(block_left - col_x)
                if d < best_dist:
                    best_dist = d
                    best_ci = ci
            return best_ci

        num_cols = len(columns)

        # Pre-process: split blocks that span multiple columns.
        # When a block's left edge is in col N but it extends far into
        # col N+1+, the text typically starts with a label (name) for
        # col N followed by content for col N+1.  We split at the
        # boundary between the Latin/name prefix and the CJK description.
        _RE_NAME_DESC_SPLIT = re.compile(
            r"^([A-Za-z\s()（）\d,.\-–&]+?)\s+"  # Latin name + year
            r"([\u4e00-\u9fff].*)$"                # CJK description
        )

        split_blocks: List[Tuple[int, str]] = []  # (col_index, text)
        for tc in table_candidates:
            ci = _col_index(tc.bbox[0])
            txt = re.sub(r"\s+", " ", tc.text).strip()
            # Check if block spans into the next column's territory
            if ci < num_cols - 1:
                next_col_x = columns[ci + 1]
                block_right = tc.bbox[2]
                if block_right > next_col_x + 10:
                    # This block spans columns — try to split
                    m = _RE_NAME_DESC_SPLIT.match(txt)
                    if m:
                        split_blocks.append((ci, m.group(1).strip()))
                        split_blocks.append((ci + 1, m.group(2).strip()))
                        continue
            split_blocks.append((ci, txt))

        # Group split text entries by column, preserving order
        col_entries: List[List[Tuple[float, str]]] = [[] for _ in range(num_cols)]
        # We need y-positions for ordering. Map original blocks to entries.
        # Since we iterated table_candidates in order, re-iterate.
        entry_idx = 0
        for tc in table_candidates:
            ci = _col_index(tc.bbox[0])
            txt = re.sub(r"\s+", " ", tc.text).strip()
            if ci < num_cols - 1:
                next_col_x = columns[ci + 1]
                if tc.bbox[2] > next_col_x + 10:
                    m = _RE_NAME_DESC_SPLIT.match(txt)
                    if m:
                        col_entries[ci].append((tc.bbox[3], m.group(1).strip()))
                        col_entries[ci + 1].append((tc.bbox[3], m.group(2).strip()))
                        continue
            col_entries[ci].append((tc.bbox[3], txt))

        # Sort each column by y (descending)
        for ce in col_entries:
            ce.sort(key=lambda x: -x[0])

        # --- Within each column, group consecutive entries into cells ---
        # Use adaptive gap detection: find the natural break point between
        # intra-cell line spacing and inter-cell row boundaries.

        col_cells = [_group_cells(ce) for ce in col_entries]

        # --- Determine anchor column for row boundaries ---
        # Use the rightmost column with ≥ 2 cells as anchor, since the
        # rightmost column (typically descriptions) tends to have the
        # most reliable cell-boundary detection (larger text blocks with
        # clear inter-row gaps).
        anchor_col = -1
        for ci_r in range(num_cols - 1, -1, -1):
            if len(col_cells[ci_r]) >= 2:
                anchor_col = ci_r
                break
        if anchor_col < 0:
            continue

        anchor_cells = col_cells[anchor_col]
        n_rows = len(anchor_cells)

        if n_rows < 2:
            continue

        # --- Map entries from other columns to anchor rows via y-overlap ---
        # For non-anchor columns, first group entries into cells using the
        # adaptive gap method, then map each grouped cell to the best
        # matching anchor row.  This preserves multi-line entries (e.g.
        # "从机会识别来\n源的角度" → single cell) while correctly
        # aligning them to the anchor row structure.
        def _best_row_for_cell(cell_top, cell_bottom, anchors):
            """Find anchor row with best y-overlap for a cell span."""
            best_ri = 0
            best_overlap = float("-inf")
            for ri, (a_top, a_bottom, _) in enumerate(anchors):
                overlap_top = min(cell_top, a_top)
                overlap_bottom = max(cell_bottom, a_bottom)
                overlap = overlap_top - overlap_bottom
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_ri = ri
            return best_ri

        grid: List[List[str]] = [["" for _ in range(num_cols)] for _ in range(n_rows)]

        # Fill anchor column from grouped cells
        for ri, (_, _, txt) in enumerate(anchor_cells):
            grid[ri][anchor_col] = txt

        # Fill other columns: group into cells first, then map
        for ci in range(num_cols):
            if ci == anchor_col:
                continue
            cells = col_cells[ci]
            for (c_top, c_bot, txt) in cells:
                ri = _best_row_for_cell(c_top, c_bot, anchor_cells)
                grid[ri][ci] = _smart_join(grid[ri][ci], txt)

        # --- Heading-based row refinement ---
        # When LR headings in the table region suggest more rows than
        # gap-based grouping detected AND the current grid has at least
        # one very long cell (suggesting merged rows), use heading
        # positions as row anchors with sentence-end-aware splitting.
        max_grid_cell = max(
            (len(grid[r][c]) for r in range(n_rows)
             for c in range(num_cols)),
            default=0,
        )
        heading_anchors = sorted(
            [(lrh.bbox[3], lrh.bbox[1], lrh.text.strip())
             for lrh in headings_in_region
             if hasattr(lrh, 'text') and lrh.text.strip()],
            key=lambda x: -x[0],  # y descending = top to bottom
        )

        if (len(heading_anchors) > n_rows
                and n_rows <= 3
                and max_grid_cell >= 80):
            heading_x_avg = sum(
                lrh.bbox[0] for lrh in headings_in_region
            ) / len(headings_in_region)
            heading_ci = _col_index(heading_x_avg)

            h_n = len(heading_anchors)
            h_centers = [
                (h[0] + h[1]) / 2.0 for h in heading_anchors
            ]
            h_mids = [
                (h_centers[i] + h_centers[i + 1]) / 2.0
                for i in range(h_n - 1)
            ]

            new_grid = [
                ["" for _ in range(num_cols)] for _ in range(h_n)
            ]
            for ri, (_, _, h_txt) in enumerate(heading_anchors):
                new_grid[ri][heading_ci] = h_txt

            _RE_SENT_END = re.compile(
                r'[。！？）\]】」』][。\s]*$'
            )

            for ci in range(num_cols):
                if ci == heading_ci:
                    continue
                entries = col_entries[ci]
                if not entries:
                    continue

                if len(entries) < 2 or not h_mids:
                    # Few entries — assign each to nearest heading
                    for (y, txt) in entries:
                        best_ri = min(
                            range(h_n),
                            key=lambda ri: abs(y - h_centers[ri]),
                        )
                        new_grid[best_ri][ci] = _smart_join(
                            new_grid[best_ri][ci], txt,
                        )
                    continue

                # Compute gap midpoints between consecutive entries
                gap_info = []
                for ei in range(len(entries) - 1):
                    gm = (entries[ei][0] + entries[ei + 1][0]) / 2.0
                    actual_gap = entries[ei][0] - entries[ei + 1][0]
                    gap_info.append((gm, actual_gap, ei))

                all_gaps = [g[1] for g in gap_info]
                med_gap = sorted(all_gaps)[len(all_gaps) // 2]

                # For each heading boundary, find best split point
                splits: List[int] = []
                used: set = set()
                for hm in h_mids:
                    best_ei = None
                    best_score = float("-inf")
                    for gm, ag, ei in gap_info:
                        if ei in used:
                            continue
                        dist = abs(gm - hm)
                        if dist > max(80, med_gap * 6):
                            continue
                        score = -dist
                        if _RE_SENT_END.search(entries[ei][1]):
                            score += 30
                        if ag > med_gap * 1.5:
                            score += 20
                        if score > best_score:
                            best_score = score
                            best_ei = ei
                    if best_ei is not None:
                        splits.append(best_ei)
                        used.add(best_ei)

                splits.sort()

                # Partition entries into groups and assign to rows
                groups: List[List[Tuple[float, str]]] = []
                start = 0
                for si in splits:
                    groups.append(entries[start : si + 1])
                    start = si + 1
                groups.append(entries[start:])

                for ri, group in enumerate(groups):
                    if ri >= h_n:
                        break
                    for (_, txt) in group:
                        new_grid[ri][ci] = _smart_join(
                            new_grid[ri][ci], txt,
                        )

            n_rows = h_n
            grid = new_grid

        # Convert to TableCell rows
        table_rows: List[List[TableCell]] = []
        for row_texts in grid:
            cells = [TableCell(text=t, colspan=1, rowspan=1) for t in row_texts]
            table_rows.append(cells)

        # Only accept if we got a meaningful table (>= 2 rows, >= 2 cols)
        if len(table_rows) < 2:
            continue

        # Verify it's a real table: at least 2 rows should have content in
        # multiple columns
        multi_col_rows = sum(
            1 for row in table_rows
            if sum(1 for c in row if c.text.strip()) >= 2
        )
        if multi_col_rows < 2:
            continue

        # --- Build the table block ---
        table_bbox = (table_left, table_bottom, table_right, table_top)
        new_tables.append(TableBlock(
            rows=table_rows,
            bbox=table_bbox,
            page_index=page_index,
        ))

        # --- Remove LR headings that fall within the table region ---
        remaining_headings = [
            lrh for lrh in remaining_headings
            if lrh not in headings_in_region
        ]

        logger.info(
            f"Reconstructed missed table p{page_index + 1}: "
            f"caption='{caption_text[:40]}' "
            f"{len(table_rows)} rows x {num_cols} cols, "
            f"removed {len(headings_in_region)} LR headings"
        )

    return new_tables, remaining_headings


def _fix_misstructured_lr_tables(
    page: "fsdk.PDFPage",
    table_blocks: List[TableBlock],
    text_blocks: List[TextBlock],
    page_index: int,
) -> List[TableBlock]:
    """Fix LR tables that have too few rows due to incorrect cell merging.

    Some tables are detected by the LR module but with cells merged across
    rows, resulting in (e.g.) a 4-row table appearing as only 2 rows.
    This function detects such tables and reconstructs the correct row
    structure by re-extracting characters from the table region.

    Detection criteria:
    - A "表 N-N" caption exists in *text_blocks* just above an LR table
    - The LR table has ≤ 3 data rows
    - At least one cell contains ≥ 80 characters (indicating merged text)

    Returns the (possibly updated) table_blocks list.
    """
    if not table_blocks or not text_blocks:
        return table_blocks

    # Find table captions in text blocks
    caption_blks = []
    for blk in text_blocks:
        if _RE_TABLE_CAPTION.match(re.sub(r"\s+", " ", blk.text).strip()):
            caption_blks.append(blk)

    if not caption_blks:
        return table_blocks

    result = list(table_blocks)

    for cap_blk in caption_blks:
        cap_bottom = cap_blk.bbox[1]  # bottom of caption in PDF coords

        # Find the LR table just below this caption
        best_ti = None
        best_dist = float("inf")
        for ti, tb in enumerate(result):
            table_top = tb.bbox[3]  # top of table
            # Caption bottom should be near table top (within ~30 units)
            dist = cap_bottom - table_top
            if 0 <= dist < 30 and dist < best_dist:
                best_dist = dist
                best_ti = ti

        if best_ti is None:
            continue

        lr_table = result[best_ti]
        num_lr_rows = len(lr_table.rows)

        # Only fix tables with suspiciously few rows
        if num_lr_rows > 3:
            continue

        # Check if any cell has substantial text (indicating merged rows)
        max_cell_len = max(
            len(c.text) for r in lr_table.rows for c in r
        )
        if max_cell_len < 80:
            continue

        # --- Re-extract text blocks from the table region ---
        region_blocks, _ = _extract_text_blocks(
            page, page_index, include_bbox=lr_table.bbox,
        )

        if len(region_blocks) < 4:
            continue

        # --- Detect column grid from x-positions ---
        x_lefts = sorted(set(round(b.bbox[0], 0) for b in region_blocks))
        if len(x_lefts) < 2:
            continue

        columns: List[float] = []
        for x in x_lefts:
            if not columns or x - columns[-1] > 15:
                columns.append(x)
        if len(columns) < 2:
            continue

        num_cols = len(columns)

        def _col_idx(block_left: float) -> int:
            best_ci, best_d = 0, abs(block_left - columns[0])
            for ci, cx in enumerate(columns):
                d = abs(block_left - cx)
                if d < best_d:
                    best_d = d
                    best_ci = ci
            return best_ci

        # --- Assign blocks to columns ---
        col_entries: List[List[Tuple[float, str]]] = [[] for _ in range(num_cols)]
        for rb in region_blocks:
            ci = _col_idx(rb.bbox[0])
            txt = re.sub(r"\s+", " ", rb.text).strip()
            if txt:
                col_entries[ci].append((rb.bbox[3], txt))

        # Sort each column by y (descending = top to bottom)
        for ce in col_entries:
            ce.sort(key=lambda x: -x[0])

        # --- Group into cells per column ---
        col_cells = [_group_cells(ce) for ce in col_entries]

        # --- Anchor column: rightmost with ≥ 2 cells ---
        anchor_col = -1
        for ci_r in range(num_cols - 1, -1, -1):
            if len(col_cells[ci_r]) >= 2:
                anchor_col = ci_r
                break
        if anchor_col < 0:
            continue

        anchor_cells = col_cells[anchor_col]
        n_rows = len(anchor_cells)

        # Only replace if we found strictly more rows than LR
        if n_rows <= num_lr_rows:
            continue

        # --- Map other columns to anchor rows via y-overlap ---
        def _best_row(c_top, c_bot, anchors):
            best_ri, best_ov = 0, float("-inf")
            for ri, (a_top, a_bot, _) in enumerate(anchors):
                ov = min(c_top, a_top) - max(c_bot, a_bot)
                if ov > best_ov:
                    best_ov = ov
                    best_ri = ri
            return best_ri

        grid = [["" for _ in range(num_cols)] for _ in range(n_rows)]
        for ri, (_, _, txt) in enumerate(anchor_cells):
            grid[ri][anchor_col] = txt

        for ci in range(num_cols):
            if ci == anchor_col:
                continue
            for (c_top, c_bot, txt) in col_cells[ci]:
                ri = _best_row(c_top, c_bot, anchor_cells)
                grid[ri][ci] = _smart_join(grid[ri][ci], txt)

        # --- Build corrected TableBlock ---
        table_rows = [
            [TableCell(text=t, colspan=1, rowspan=1) for t in row_texts]
            for row_texts in grid
        ]

        # Validate: ≥ 2 rows with content in multiple columns
        multi_col_rows = sum(
            1 for row in table_rows
            if sum(1 for c in row if c.text.strip()) >= 2
        )
        if multi_col_rows < 2:
            continue

        result[best_ti] = TableBlock(
            rows=table_rows,
            bbox=lr_table.bbox,
            page_index=page_index,
        )

        cap_text = re.sub(r"\s+", " ", cap_blk.text).strip()
        logger.info(
            f"Fixed misstructured LR table p{page_index + 1}: "
            f"caption='{cap_text[:40]}' "
            f"{num_lr_rows} rows -> {n_rows} rows x {num_cols} cols"
        )

    return result


def _extract_tables_and_lr_headings(
    page: fsdk.PDFPage,
    page_index: int,
) -> Tuple[List[TableBlock], List[LRHeadingInfo], List[Tuple[float, float, float, float]], List[CodeBlock], List[Tuple[float, float, float, float]]]:
    """Use the Foxit LR module to detect tables, headings, paragraphs, and figures.

    Returns (tables, lr_headings, lr_paragraph_bboxes, code_blocks, figure_bboxes).
    """
    tables: List[TableBlock] = []
    lr_headings: List[LRHeadingInfo] = []
    lr_para_bboxes: List[Tuple[float, float, float, float]] = []
    code_blocks: List[CodeBlock] = []
    lr_figure_bboxes: List[Tuple[float, float, float, float]] = []
    try:
        ctx = fsdk.LRContext(page)
        ctx.StartParse()
        root = ctx.GetRootElement()
        if root.IsEmpty():
            return tables, lr_headings, lr_para_bboxes, code_blocks, lr_figure_bboxes

        lr_tables: list = []
        lr_heading_elems: list = []
        _collect_lr_elements(root, lr_tables, lr_heading_elems, lr_para_bboxes, lr_figure_bboxes)

        # Filter out oversized individual figure elements (likely false
        # positives from watermarks or decorative backgrounds).
        page_area = page.GetWidth() * page.GetHeight()
        if page_area > 0:
            lr_figure_bboxes[:] = [
                fb for fb in lr_figure_bboxes
                if (fb[2] - fb[0]) * (fb[3] - fb[1]) / page_area <= 0.12
            ]

        # Also find figure *container* Divs whose bboxes encompass text
        # labels / annotations that are part of the figure but not
        # individual Figure elements in the LR tree.
        _find_figure_container_bboxes(root, page_area, lr_figure_bboxes)

        text_page = fsdk.TextPage(page, fsdk.TextPage.e_ParseTextNormal)
        char_count = text_page.GetCharCount()

        # Collect FormXObject bboxes for chart/diagram detection.
        # If an LR-detected "table" overlaps significantly with a
        # FormXObject, it is actually an embedded vector drawing
        # (org chart, flowchart, etc.) and should be rendered as an
        # image rather than converted to a Markdown table.
        fxobj_bboxes = _collect_formxobject_bboxes(page)

        # --- Tables ---
        for t_elem in lr_tables:
            t_se = fsdk.LRStructureElement(t_elem)
            bbox_rect = t_se.GetBBox()
            bbox = (bbox_rect.left, bbox_rect.bottom,
                    bbox_rect.right, bbox_rect.top)

            # Check if this "table" overlaps with a FormXObject (embedded
            # vector drawing).  If ≥ 50% of the table area is covered by
            # a FormXObject, treat it as a chart/diagram figure instead.
            is_chart = False
            if fxobj_bboxes:
                for fx_bbox in fxobj_bboxes:
                    if _bbox_overlap_ratio(bbox, fx_bbox) >= 0.50:
                        lr_figure_bboxes.append(fx_bbox)
                        logger.info(
                            f"Reclassified table as chart/figure "
                            f"p{page_index+1}: bbox={bbox}"
                        )
                        is_chart = True
                        break
            if is_chart:
                continue  # skip this table — it's now a figure

            rows = _extract_lr_table(t_elem, text_page, char_count)
            if not rows:
                continue

            # --- Fix rows where the LR module merged columns ---
            rows = _split_merged_row_cells(rows, text_page, char_count)

            # --- Filter out false-positive tables ---
            false_reason = _is_false_table(rows)
            if false_reason:
                if false_reason == "code":
                    # Reconstruct code lines from table rows and store
                    # as a CodeBlock.  Each row = one line of code.
                    code_lines = []
                    for row in rows:
                        line_text = " ".join(
                            c.text.strip() for c in row if c.text.strip()
                        )
                        if line_text:
                            code_lines.append(line_text)
                    if code_lines:
                        code_blocks.append(CodeBlock(
                            lines=code_lines,
                            bbox=bbox,
                            page_index=page_index,
                        ))
                        logger.info(
                            f"Extracted code block p{page_index+1}: "
                            f"{len(code_lines)} lines, bbox={bbox}"
                        )
                    continue

                # If the combined text looks like a section heading,
                # promote it to an LR heading so that the converter
                # renders it with the correct Markdown heading marker
                # instead of emitting it as disjointed body text.
                #
                # Use _text_in_rect on the table bbox to get text in
                # correct reading order (top-to-bottom, left-to-right).
                # Cell-based concatenation can mis-order characters when
                # a long heading wraps across two lines and the LR
                # module splits it into columns (e.g. the last char of
                # a wrapped heading lands in the first column of the
                # next visual row).
                all_text = re.sub(
                    r"\s+", " ",
                    _text_in_rect(text_page, bbox_rect, char_count),
                ).strip()
                if not all_text:
                    # Fallback: concatenate cell texts
                    all_text = re.sub(
                        r"\s+", " ",
                        " ".join(c.text.strip() for row in rows for c in row),
                    ).strip()
                # Fix wrapped CJK heading artifacts, e.g. "推进作 用" -> "推进作用".
                # Keep this normalization local to promoted false-table headings
                # to avoid changing normal body spacing behavior.
                all_text = re.sub(
                    r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])",
                    "",
                    all_text,
                )
                m = _RE_SECTION_HEADING.match(all_text)
                if m and all_text:
                    # Determine heading level from numbering depth
                    num_m = re.match(r"^\s*(\d+(?:\.\d+)*)", all_text)
                    if num_m:
                        depth = len(num_m.group(1).split("."))
                        level = min(depth, 6)  # "3" → H1, "3.2" → H2, "3.2.1" → H3
                    else:
                        level = 1  # default for "第X章" etc.
                    lr_headings.append(LRHeadingInfo(
                        bbox=bbox, level=level, text=all_text,
                    ))
                    logger.info(
                        f"Promoted false table to heading p{page_index+1} "
                        f"H{level}: {all_text[:80]}"
                    )
                else:
                    logger.debug(
                        f"Skipped false table p{page_index+1}: "
                        f"{len(rows)} rows, bbox={bbox}"
                    )
                continue

            tables.append(TableBlock(
                rows=rows,
                bbox=bbox,
                page_index=page_index,
            ))

            # Check if this is a multi-column list that should be
            # restructured into a proper multi-row table.
            restructured = _restructure_multicolumn_list(tables[-1].rows)
            if restructured is not None:
                tables[-1] = TableBlock(
                    rows=restructured,
                    bbox=bbox,
                    page_index=page_index,
                )
                logger.info(
                    f"Restructured multi-column list p{page_index+1}: "
                    f"1 row → {len(restructured)} rows"
                )

            logger.info(
                f"Extracted table p{page_index+1}: "
                f"{len(rows)} rows, bbox={bbox}"
            )

        # --- Headings ---
        for h_elem in lr_heading_elems:
            et = h_elem.GetElementType()
            level = _LR_HEADING_MAP.get(et, 1)
            try:
                h_se = fsdk.LRStructureElement(h_elem)
                bbox_rect = h_se.GetBBox()
                bbox = (bbox_rect.left, bbox_rect.bottom,
                        bbox_rect.right, bbox_rect.top)
                txt = _text_in_rect(text_page, bbox_rect, char_count)
                if txt:
                    lr_headings.append(LRHeadingInfo(
                        bbox=bbox, level=level, text=txt,
                    ))
                    logger.debug(
                        f"LR heading p{page_index+1} H{level}: {txt[:60]}"
                    )
            except Exception:
                pass

        # Explicitly release SDK C++ resources to prevent memory buildup
        # when processing many pages sequentially.
        del text_page
        del ctx
        gc.collect()

    except Exception as e:
        logger.warning(f"LR extraction failed on page {page_index}: {e}")

    # Cluster raw figure bboxes into composite figure regions
    try:
        pw = page.GetWidth()
        ph = page.GetHeight()
        figure_clusters = _cluster_figure_bboxes(lr_figure_bboxes, pw, ph)
    except Exception:
        figure_clusters = []

    return tables, lr_headings, lr_para_bboxes, code_blocks, figure_clusters


# ---------------------------------------------------------------------------
# Text-density-based figure region filter
# ---------------------------------------------------------------------------

# If a figure cluster contains more extractable text per unit area than
# this threshold, it is very likely a text region (e.g. a code block with
# a gray background fill) misidentified as a figure.  Threshold is
# expressed as *characters per 1000 square points* of figure area.
_FIGURE_TEXT_DENSITY_THRESHOLD = 1.5   # chars / 1000 pt²

# Minimum length of the longest "continuous text run" (non-whitespace
# characters) for a figure region to be classified as text-heavy.
# Chart / diagram labels consist of short scattered fragments,
# while code or prose has long continuous tokens or sentences.
_FIGURE_MIN_LONGEST_TOKEN = 15

# Thin horizontal strips below this height (pt) are almost always
# single-line code-block background fills, not real figures.
_FIGURE_MIN_HEIGHT = 60.0


def _filter_text_heavy_figures(
    figure_regions: List[Tuple[float, float, float, float]],
    page: fsdk.PDFPage,
    page_index: int,
) -> List[Tuple[float, float, float, float]]:
    """Remove figure regions that actually contain mostly extractable text.

    The LR module can misclassify filled-background code/text areas as
    Figure elements.  When several of those are clustered together they
    form a large "figure region" that suppresses normal text extraction.

    For each figure region we extract text via TextPage and check two
    criteria — text *density* (chars per 1000 pt²) and the longest
    contiguous non-space token.  Only regions that exceed **both**
    thresholds are dropped; this avoids false-dropping chart/diagram
    labels that are dense but consist of short scattered fragments.
    """
    if not figure_regions:
        return figure_regions

    try:
        tp = fsdk.TextPage(page, fsdk.TextPage.e_ParseTextNormal)
        cc = tp.GetCharCount()
    except Exception:
        return figure_regions

    kept: List[Tuple[float, float, float, float]] = []
    for fb in figure_regions:
        height = fb[3] - fb[1]
        # Skip thin horizontal strips (code-line background fills).
        if height < _FIGURE_MIN_HEIGHT:
            logger.info(
                f"Dropped thin-strip figure region p{page_index+1}: "
                f"bbox={fb}, height={height:.1f}pt"
            )
            continue

        area = max(1.0, (fb[2] - fb[0]) * height)
        rect = _Rect(fb[0], fb[1], fb[2], fb[3])
        txt = _text_in_rect(tp, rect, cc)
        stripped = txt.strip()
        text_len = len(stripped)
        density = text_len / (area / 1000.0)

        # Find the longest token (non-space run)
        tokens = stripped.split()
        longest_token = max((len(t) for t in tokens), default=0) if tokens else 0

        if (density > _FIGURE_TEXT_DENSITY_THRESHOLD
                and longest_token >= _FIGURE_MIN_LONGEST_TOKEN):
            logger.info(
                f"Dropped text-heavy figure region p{page_index+1}: "
                f"bbox={fb}, text_len={text_len}, density={density:.2f}, "
                f"longest_token={longest_token}"
            )
        else:
            kept.append(fb)

    del tp
    return kept


def _point_in_any_table(
    x: float, y: float,
    table_bboxes: List[Tuple[float, float, float, float]],
) -> bool:
    """Return True if the point (x, y) falls inside any table bounding box."""
    for (left, bottom, right, top) in table_bboxes:
        if left - 2 <= x <= right + 2 and bottom - 2 <= y <= top + 2:
            return True
    return False


# ---------------------------------------------------------------------------
# Link extraction
# ---------------------------------------------------------------------------

def _extract_links(page: fsdk.PDFPage, text_page: fsdk.TextPage, page_index: int) -> List[LinkBlock]:
    """Extract hyperlinks from page text."""
    links = []
    try:
        page_links = fsdk.PageTextLinks(text_page)
        link_count = page_links.GetTextLinkCount()
        for i in range(link_count):
            try:
                text_link = page_links.GetTextLink(i)
                if text_link.IsEmpty():
                    continue
                url = text_link.GetURI()
                # Get link text range and text
                start_index = text_link.GetStartCharIndex()
                end_index = text_link.GetEndCharIndex()
                text = ""
                if start_index >= 0 and end_index >= start_index:
                    count = end_index - start_index + 1
                    text = text_page.GetChars(start_index, count)
                links.append(LinkBlock(
                    url=url,
                    text=text,
                    page_index=page_index,
                ))
            except Exception as e:
                logger.debug(f"Failed to extract link {i} on page {page_index}: {e}")
    except Exception as e:
        logger.debug(f"Link extraction failed on page {page_index}: {e}")
    return links


# ---------------------------------------------------------------------------
# Main parsing function
# ---------------------------------------------------------------------------

def parse_pdf(pdf_path: str) -> PDFContent:
    """
    Parse a PDF file and extract all structured content.
    
    Args:
        pdf_path: Path to the PDF file.
        
    Returns:
        PDFContent with all extracted information.
    """
    _ensure_sdk()

    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    # Replace spaces with underscores so image filenames are Markdown-safe
    pdf_name = pdf_name.replace(" ", "_")
    result = PDFContent()

    # Open document
    doc = fsdk.PDFDoc(pdf_path)
    error_code = doc.Load("")
    if error_code != fsdk.e_ErrSuccess:
        # Try with empty password first, may be a non-encrypted doc
        if error_code == fsdk.e_ErrPassword:
            raise ValueError("PDF is password-protected. Please provide the password.")
        raise RuntimeError(f"Failed to load PDF. Error code: {error_code}")

    # Metadata
    try:
        metadata = fsdk.Metadata(doc)
        result.title = metadata.GetValue("Title") or ""
        result.author = metadata.GetValue("Author") or ""
    except Exception:
        pass

    result.page_count = doc.GetPageCount()

    # Bookmarks
    try:
        root_bookmark = doc.GetRootBookmark()
        if not root_bookmark.IsEmpty():
            first_child = root_bookmark.GetFirstChild()
            result.bookmarks = _extract_bookmarks(first_child, doc, level=0)
    except Exception as e:
        logger.debug(f"Bookmark extraction failed: {e}")

    # Process each page
    for page_idx in range(result.page_count):
        try:
            page = doc.GetPage(page_idx)
            # Parse page content
            page.StartParse(fsdk.PDFPage.e_ParsePageNormal, None, False)
        except Exception as e:
            logger.warning(f"Failed to parse page {page_idx}: {e}")
            continue

        page_content = PageContent(page_index=page_idx)

        try:
            page_content.width = page.GetWidth()
            page_content.height = page.GetHeight()
        except Exception:
            pass

        # Tables, LR headings, LR paragraph bboxes, and figure regions —
        # extract first so we can exclude table/code/figure regions from
        # general text extraction.
        page_content.table_blocks, page_content.lr_headings, \
            page_content.lr_paragraphs, page_content.code_blocks, \
            figure_regions = \
            _extract_tables_and_lr_headings(page, page_idx)

        # Filter out figure regions that are actually text-heavy
        # (e.g. code blocks with gray background fills).
        figure_regions = _filter_text_heavy_figures(
            figure_regions, page, page_idx,
        )

        table_bboxes = [tb.bbox for tb in page_content.table_blocks]
        # Also exclude code block regions from text extraction
        code_bboxes = [cb.bbox for cb in page_content.code_blocks]
        # Render figure regions and exclude them from text extraction
        fig_counter = 0
        figure_bboxes = list(figure_regions)
        exclude_bboxes = table_bboxes + code_bboxes + figure_bboxes

        # Text blocks (skip characters that fall inside a detected table or code block)
        text_blocks, raw_text = _extract_text_blocks(
            page, page_idx, exclude_bboxes=exclude_bboxes,
        )
        page_content.text_blocks = text_blocks
        page_content.raw_text = raw_text

        # --- Fix LR tables with wrong row structure ---
        # Must run before _reconstruct_missed_tables and needs the page
        # object for re-extracting characters inside table regions.
        if page_content.table_blocks and page_content.text_blocks:
            page_content.table_blocks = _fix_misstructured_lr_tables(
                page, page_content.table_blocks,
                page_content.text_blocks, page_idx,
            )

        # --- Heuristic: reconstruct tables the LR module missed ----------
        # Run after text blocks are available so we can detect grid-like
        # patterns among the extracted text blocks.
        if page_content.text_blocks:
            new_tables, new_headings = _reconstruct_missed_tables(
                page_content.text_blocks,
                page_content.lr_headings,
                page_content.table_blocks,
                page_idx,
            )
            if len(new_tables) > len(page_content.table_blocks):
                # New table(s) reconstructed — remove text blocks that
                # fall inside the new table region(s) so they don't
                # appear as duplicate body text.
                added_bboxes = [
                    tb.bbox for tb in new_tables
                    if tb not in page_content.table_blocks
                ]
                filtered_blocks = []
                for blk in page_content.text_blocks:
                    bx = (blk.bbox[0] + blk.bbox[2]) / 2.0
                    by = (blk.bbox[1] + blk.bbox[3]) / 2.0
                    if _point_in_any_table(bx, by, added_bboxes):
                        continue
                    filtered_blocks.append(blk)
                page_content.text_blocks = filtered_blocks
                page_content.table_blocks = new_tables
                page_content.lr_headings = new_headings

        # Images (embedded bitmaps)
        page_content.image_blocks = _extract_images(page, page_idx, pdf_name)

        # Render LR figure regions (vector diagrams) as raster images.
        # Done after _extract_images so we can skip figure regions that
        # overlap with an already-extracted embedded bitmap.
        existing_img_bboxes = [ib.bbox for ib in page_content.image_blocks]
        fig_counter = 0
        for fig_bbox in figure_regions:
            fig_cx = (fig_bbox[0] + fig_bbox[2]) / 2.0
            fig_cy = (fig_bbox[1] + fig_bbox[3]) / 2.0
            if _point_in_any_table(fig_cx, fig_cy, existing_img_bboxes):
                continue
            fig_counter += 1
            img_block = _render_page_region(
                page, fig_bbox, page_idx, pdf_name, fig_counter,
            )
            if img_block:
                page_content.image_blocks.append(img_block)

        # Links
        try:
            tp = fsdk.TextPage(page, fsdk.TextPage.e_ParseTextNormal)
            page_content.link_blocks = _extract_links(page, tp, page_idx)
        except Exception:
            pass

        result.pages.append(page_content)

    return result
