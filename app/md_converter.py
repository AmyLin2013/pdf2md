"""Copyright (c) 2026 Zhi Lin. All rights reserved.
Author: zhi_lin@qq.com
"""

"""
Markdown Converter – transforms PDFContent into well-formatted Markdown.

Strategy for heading detection (priority cascade):
1. PDF bookmarks (most reliable for hierarchy)
2. Font-size rank heuristics (larger / bold text → headings)
3. LR confirmation + numbering patterns (for body-size headings)
4. Bold + numbering pattern fallback
"""

import re
import statistics
from collections import Counter
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from .pdf_parser import (
    BookmarkItem,
    CodeBlock,
    ImageBlock,
    LRHeadingInfo,
    LinkBlock,
    PageContent,
    PDFContent,
    TableBlock,
    TableCell,
    TextBlock,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape_md(text: str) -> str:
    """Escape special Markdown characters in body text (not headings)."""
    # Only escape characters that would break rendering
    text = text.replace("\\", "\\\\")
    for ch in ["<", ">"]:
        text = text.replace(ch, f"\\{ch}")
    return text


def _slugify(text: str) -> str:
    """Create anchor-friendly slug from text."""
    s = text.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s]+", "-", s)
    return s


def _collapse_spurious_blank_lines(md_text: str) -> str:
    """Remove accidental blank lines caused by wrapped body-text extraction."""
    lines = md_text.split("\n")
    if len(lines) < 3:
        return md_text

    structural_prefixes = ("#", "- ", "* ", "+ ", ">", "|", "![", "```")

    def _is_structural(line: str) -> bool:
        s = line.lstrip()
        return (not s) or s.startswith(structural_prefixes)

    def _looks_continuation(line: str) -> bool:
        s = line.lstrip()
        return bool(re.match(r"^[\u4e00-\u9fffA-Za-z0-9（(\"“‘']", s))

    out: List[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if (
            line == ""
            and i > 0
            and out
        ):
            prev = out[-1].rstrip()
            j = i
            while j < len(lines) and lines[j] == "":
                j += 1
            nxt = lines[j] if j < len(lines) else ""
            if (
                prev
                and nxt.strip()
                and not _is_structural(prev)
                and not _is_structural(nxt)
                and _looks_continuation(nxt)
                and not prev.endswith(("。", "！", "？", ".", "!", "?", ";", "；", ":", "："))
            ):
                i = j
                continue
        out.append(line)
        i += 1

    return "\n".join(out)


# ---------------------------------------------------------------------------
# Header / Footer detection
# ---------------------------------------------------------------------------

def _detect_header_footer_texts(
    pages: List[PageContent],
    margin_ratio: float = 0.10,
    min_repeat: int = 3,
) -> FrozenSet[str]:
    """
    Detect recurring page headers and footers.

    Strategy: for each page, collect text blocks whose vertical centre
    falls inside the top or bottom *margin_ratio* of the page height.
    Text that appears (after normalisation) on at least *min_repeat*
    pages is considered a header or footer.

    Page numbers are also detected: if a text block is purely numeric
    (or Roman numeral / "Page X" style) and sits in the margin zone,
    it counts as a header/footer even if it changes from page to page.

    Returns a frozen set of normalised texts to skip.
    """
    _PAGE_NUM_RE = re.compile(
        r"^\s*"
        r"(?:"
        r"\d+"                      # plain number
        r"|[ivxlcdm]+"              # Roman lowercase
        r"|[IVXLCDM]+"              # Roman uppercase
        r"|(?:page|第)\s*\d+\s*(?:页)?"  # Page N / 第N页
        r"|[-–—]\s*\d+\s*[-–—]"    # -3-, —3—
        r")\s*$",
        re.IGNORECASE,
    )

    # Count normalised text → number of pages it appears in
    text_page_count: Counter = Counter()
    # Track per-page margin-zone texts to avoid double-counting
    per_page_texts: Dict[int, Set[str]] = {}
    # Track page-number-like blocks by position bucket
    page_num_position_counts: Counter = Counter()  # ("top"/"bottom", rounded_x) → count

    effective_min = max(min_repeat, min(3, len(pages)))

    for page in pages:
        if page.height <= 0:
            continue
        top_threshold = page.height * (1.0 - margin_ratio)  # PDF y: 0=bottom
        bottom_threshold = page.height * margin_ratio

        seen: Set[str] = set()
        for block in page.text_blocks:
            # Vertical centre of block
            y_centre = (block.bbox[1] + block.bbox[3]) / 2.0
            in_top = y_centre >= top_threshold
            in_bottom = y_centre <= bottom_threshold
            if not (in_top or in_bottom):
                continue

            norm = _normalize(block.text)
            if not norm:
                continue

            # Page-number detection (changes each page but is still HF)
            if _PAGE_NUM_RE.match(norm):
                zone = "top" if in_top else "bottom"
                x_bucket = round(block.bbox[0] / 10.0) * 10  # 10-pt buckets
                page_num_position_counts[(zone, x_bucket)] += 1
                seen.add(norm)  # still add so it's counted
                continue

            if norm not in seen:
                seen.add(norm)
                text_page_count[norm] += 1

        per_page_texts[page.page_index] = seen

    # Texts appearing on >= effective_min pages
    hf_texts: Set[str] = set()
    for text, count in text_page_count.items():
        if count >= effective_min:
            hf_texts.add(text)

    # If a page-number position bucket appears on most pages, flag all
    # pure-numeric margin text as HF regardless of changing content.
    page_count = len(pages)
    for (zone, x_bucket), cnt in page_num_position_counts.items():
        if cnt >= max(effective_min, page_count * 0.5):
            # mark a sentinel so _is_header_footer knows to check regex
            hf_texts.add(f"__PAGE_NUM__{zone}_{x_bucket}")

    return frozenset(hf_texts)


def _is_header_footer(
    block: TextBlock,
    page_height: float,
    hf_texts: FrozenSet[str],
    margin_ratio: float = 0.10,
) -> bool:
    """Return True if *block* should be treated as a header/footer."""
    if not hf_texts or page_height <= 0:
        return False

    y_centre = (block.bbox[1] + block.bbox[3]) / 2.0
    top_threshold = page_height * (1.0 - margin_ratio)
    bottom_threshold = page_height * margin_ratio
    in_top = y_centre >= top_threshold
    in_bottom = y_centre <= bottom_threshold

    if not (in_top or in_bottom):
        return False

    norm = _normalize(block.text)
    if norm in hf_texts:
        return True

    # Check page-number sentinels
    _PAGE_NUM_RE = re.compile(
        r"^\s*"
        r"(?:"
        r"\d+"
        r"|[ivxlcdm]+"
        r"|[IVXLCDM]+"
        r"|(?:page|第)\s*\d+\s*(?:页)?"
        r"|[-–—]\s*\d+\s*[-–—]"
        r")\s*$",
        re.IGNORECASE,
    )
    if _PAGE_NUM_RE.match(norm):
        zone = "top" if in_top else "bottom"
        x_bucket = round(block.bbox[0] / 10.0) * 10
        sentinel = f"__PAGE_NUM__{zone}_{x_bucket}"
        if sentinel in hf_texts:
            return True

    return False


# ---------------------------------------------------------------------------
# Heading detection via font size (rank-based)
# ---------------------------------------------------------------------------

# Regex patterns for common Chinese academic heading numbering
_RE_CHAPTER = re.compile(r"^第\s*[一二三四五六七八九十\d]+\s*章")
_RE_SEC_DOT = re.compile(r"^(\d+\.)+\d*\s")           # 1.1  2.3.4
# (一)、（1）style – but NOT year-like "(2000)" or "(2024)"
_RE_SEC_PAREN = re.compile(
    r"^[（(]\s*(?:[一二三四五六七八九十]+|\d{1,2})\s*[)）]"
)
# Detect TOC-like lines (contain sequences of dots / leaders)
_RE_TOC_LINE = re.compile(r"[.·…]{4,}")


def _compute_font_stats(
    pages: List[PageContent],
) -> Dict:
    """
    Compute font-size statistics across the document.

    Returns dict with:
        body_size       – the dominant font size (most chars)
        heading_sizes   – sorted list of font sizes *larger* than body (desc)
        size_rank_map   – {font_size: heading_level} based on ranking
    """
    size_char_count: Dict[float, int] = {}

    for page in pages:
        for block in page.text_blocks:
            fs = round(block.font_size, 1)
            if fs > 0:
                char_len = len(block.text)
                size_char_count[fs] = size_char_count.get(fs, 0) + char_len

    if not size_char_count:
        return {"body_size": 12.0, "heading_sizes": [], "size_rank_map": {}}

    body_size = max(size_char_count, key=size_char_count.get)

    # Collect font sizes strictly larger than body AND at least 15% bigger.
    # A size only slightly larger (e.g. 10pt vs 9pt body) is typically a
    # secondary body style (TOC entries, captions), not a heading.
    # Chinese documents commonly use 14pt headings with 12pt body
    # (ratio ~1.167), so 1.15 captures this common pattern.
    min_heading_size = body_size * 1.15
    heading_sizes = sorted(
        [fs for fs in size_char_count if fs >= min_heading_size],
        reverse=True,
    )

    # Map each distinct heading size to a level: biggest → H1, next → H2, …
    size_rank_map: Dict[float, int] = {}
    for rank, fs in enumerate(heading_sizes):
        level = min(rank + 1, 6)
        size_rank_map[fs] = level

    return {
        "body_size": body_size,
        "heading_sizes": heading_sizes,
        "size_rank_map": size_rank_map,
    }


def _heading_level_from_font(
    font_size: float,
    is_bold: bool,
    font_stats: Dict,
    text: str = "",
) -> int:
    """
    Map font size to heading level (1-6) using **rank-based** approach.

    Sizes larger than body are ranked: largest → H1, next → H2, etc.
    Bold text at body size may still be a low-level heading, but only
    if the text is short (≤ 50 chars) and doesn't look like a TOC line.
    Returns 0 for body text.
    """
    body_size = font_stats.get("body_size", 12.0)
    size_rank_map = font_stats.get("size_rank_map", {})

    if font_size <= 0:
        return 0

    # Reject TOC-like lines early (dot leaders) regardless of font size
    if text and _RE_TOC_LINE.search(text):
        return 0

    fs_r = round(font_size, 1)

    # Direct match in the rank map
    if fs_r in size_rank_map:
        return size_rank_map[fs_r]

    # Close-enough match (within 0.5pt)
    for map_fs, level in size_rank_map.items():
        if abs(fs_r - map_fs) <= 0.5:
            return level

    # Bold text at ~body size → might be a sub-heading, but only if short
    ratio = font_size / body_size if body_size > 0 else 1.0
    if is_bold and 0.95 <= ratio <= 1.05:
        # Skip long text (likely bold paragraph, not heading)
        if len(text) > 50:
            return 0
        # Skip TOC-like lines with dot leaders
        if _RE_TOC_LINE.search(text):
            return 0
        # Assign level = max_heading_rank + 1, capped at 6
        max_rank = max(size_rank_map.values()) if size_rank_map else 0
        return min(max_rank + 1, 6)

    return 0


def _heading_level_from_numbering(text: str) -> int:
    """
    Detect heading level from Chinese academic numbering patterns.

    Returns a suggested heading level (1-4) or 0 if not detected.
    This is used as an additional signal, not the sole decider.
    Skips TOC lines (containing dot leaders) and long text.
    """
    text = text.strip()

    # Reject TOC lines
    if _RE_TOC_LINE.search(text):
        return 0

    if _RE_CHAPTER.match(text):
        return 1  # 第X章
    if _RE_SEC_DOT.match(text):
        m = _RE_SEC_DOT.match(text)
        parts = m.group(0).strip().rstrip(".").split(".")
        return min(len(parts), 4)
    if _RE_SEC_PAREN.match(text):
        return 4  # (一) style
    return 0


# ---------------------------------------------------------------------------
# Bookmark-based heading map
# ---------------------------------------------------------------------------

def _flatten_bookmarks(
    bookmarks: List[BookmarkItem],
) -> Dict[Tuple[int, str], int]:
    """
    Flatten bookmark tree into a dict: (page_index, title) → heading_level.
    """
    result: Dict[Tuple[int, str], int] = {}

    def _walk(items: List[BookmarkItem], level: int):
        for bm in items:
            key = (bm.page_index, bm.title.strip())
            result[key] = min(level + 1, 6)  # heading levels 1-6
            _walk(bm.children, level + 1)

    _walk(bookmarks, 0)
    return result


# ---------------------------------------------------------------------------
# Link map
# ---------------------------------------------------------------------------

def _build_link_map(page: PageContent) -> Dict[str, str]:
    """Build a text → url map from page links for inline replacement."""
    link_map: Dict[str, str] = {}
    for link in page.link_blocks:
        if link.text and link.url:
            link_map[link.text.strip()] = link.url
    return link_map


# ---------------------------------------------------------------------------
# Main conversion
# ---------------------------------------------------------------------------

def convert_to_markdown(
    content: PDFContent,
    include_images: bool = True,
    include_toc: bool = False,
    skip_header_footer: bool = True,
    image_base_path: str = "images",
    html_merged_table: bool = False,
) -> str:
    """
    Convert parsed PDF content to Markdown string.
    
    Args:
        content: Parsed PDF content from pdf_parser.
        include_images: Whether to include image references.
        include_toc: Whether to generate TOC from bookmarks.
        skip_header_footer: Whether to detect and skip repeated
            page headers/footers (default True).
        image_base_path: Relative path prefix for image references.
        html_merged_table: Whether to render tables with merged cells
            as HTML <table> instead of Markdown pipe-tables.
        
    Returns:
        Markdown text.
    """
    lines: List[str] = []

    # Detect recurring headers / footers
    hf_texts: FrozenSet[str] = frozenset()
    if skip_header_footer and len(content.pages) >= 2:
        hf_texts = _detect_header_footer_texts(content.pages)

    # Document title
    doc_title = content.title.strip() if content.title else ""
    if doc_title:
        lines.append(f"# {doc_title}")
        lines.append("")

    # Table of contents from bookmarks
    if include_toc and content.bookmarks:
        lines.append("## 目录")
        lines.append("")
        _render_toc(content.bookmarks, lines, indent=0)
        lines.append("")
        lines.append("---")
        lines.append("")

    # Build bookmark heading map
    bm_heading_map = _flatten_bookmarks(content.bookmarks)

    # Compute font statistics for heading detection
    font_stats = _compute_font_stats(content.pages)

    # Process each page
    for page in content.pages:
        page_lines = _convert_page(
            page=page,
            bm_heading_map=bm_heading_map,
            font_stats=font_stats,
            include_images=include_images,
            image_base_path=image_base_path,
            hf_texts=hf_texts,
            html_merged_table=html_merged_table,
        )
        lines.extend(page_lines)

    # Clean up excessive blank lines
    md_text = "\n".join(lines)
    md_text = re.sub(r"\n{4,}", "\n\n\n", md_text)
    md_text = _collapse_spurious_blank_lines(md_text)

    return md_text.strip() + "\n"


def _render_toc(bookmarks: List[BookmarkItem], lines: List[str], indent: int):
    """Render bookmarks as a nested Markdown list (TOC)."""
    for bm in bookmarks:
        prefix = "  " * indent + "- "
        anchor = _slugify(bm.title)
        lines.append(f"{prefix}[{bm.title}](#{anchor})")
        if bm.children:
            _render_toc(bm.children, lines, indent + 1)


# Item tuple: (y_top, item_type, md_text, bbox_or_None)
_MergeItem = Tuple[float, str, str, Optional[Tuple[float, float, float, float]]]


def _merge_body_text(
    items: List[_MergeItem],
    body_size: float,
    page_width: float = 0.0,
    lr_paragraphs: Optional[List[Tuple[float, float, float, float]]] = None,
) -> List[str]:
    """
    Merge consecutive body-text blocks into paragraphs.

    Uses **LR paragraph bounding boxes** as the primary signal: if two
    consecutive body blocks both fall inside the same LR paragraph bbox,
    they belong to the same paragraph and are joined with a space.

    Falls back to geometric heuristics (vertical gap + line width) when
    LR data is unavailable or when blocks don't match any LR paragraph.

    Items are sorted top-to-bottom (descending y).
    Each item is ``(y_top, item_type, md_text, bbox_or_None)``.
    """
    if not items:
        return []

    # --- Sanity-check body_size against actual block heights ---------------
    # Some PDF SDKs report font_size in non-standard units (e.g. 240 for a
    # 12pt font).  When body_size is much larger than the average text-block
    # height it would make the geometric thresholds wildly wrong, so we
    # fall back to the average block height.
    _block_heights = [
        b[3] - b[1]
        for _, t, _, b in items
        if t == "body" and b is not None and b[3] - b[1] > 0
    ]
    if _block_heights:
        avg_block_h = sum(_block_heights) / len(_block_heights)
        if body_size > avg_block_h * 3:
            body_size = avg_block_h

    line_height = max(body_size * 1.4, 14.0)
    para_gap_threshold = line_height * 1.8   # gap above this → new paragraph
    same_line_threshold = max(body_size * 0.5, 5.0)

    # --- Effective content width -------------------------------------------
    # Use the widest body block as an estimate of the actual text-column
    # width.  This is more reliable than page_width for PDFs with wide
    # margins (where body blocks are much narrower than the page).
    effective_page_width = page_width if page_width > 0 else 0.0
    _block_widths = [
        b[2] - b[0]
        for _, t, _, b in items
        if t == "body" and b is not None and b[2] - b[0] > 0
    ]
    if _block_widths:
        max_block_w = max(_block_widths)
        # Use the larger of max-block-width and page_width so that
        # _is_full_width_line compares against the text-column, not the
        # whole page.
        if max_block_w < effective_page_width:
            effective_page_width = max_block_w

    lr_paras = lr_paragraphs or []

    def _find_lr_paragraph(
        bbox: Optional[Tuple[float, float, float, float]],
    ) -> int:
        """Return the index of the LR paragraph that contains *bbox*, or -1."""
        if bbox is None or not lr_paras:
            return -1
        # Use the centre of the text block
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        for idx, (pl, pb, pr, pt) in enumerate(lr_paras):
            if pl - 3 <= cx <= pr + 3 and pb - 3 <= cy <= pt + 3:
                return idx
        return -1

    result: List[str] = []
    para_parts: List[str] = []
    prev_y: Optional[float] = None
    prev_bbox: Optional[Tuple[float, float, float, float]] = None
    prev_text: Optional[str] = None
    prev_lr_idx: int = -1   # LR paragraph index of previous block
    list_start_re = re.compile(
        r"^\s*(?:"
        r"[▪•●◆■-]"
        r"|\d+[\.、)]"
        r"|[（(]?[一二三四五六七八九十]+[)）、.]"
        r"|第\s*[一二三四五六七八九十\d]+\s*[章节条款阶段]"
        r")"
    )
    # Dot-leader pattern for TOC entries (e.g. "前言 ........... III")
    _dot_leader_re = re.compile(r'\.{4,}|…{2,}')
    toc_parts: List[str] = []

    def _flush_toc():
        """Flush accumulated TOC / dot-leader lines with soft line breaks."""
        if toc_parts:
            result.append("  \n".join(toc_parts) + "\n")
            toc_parts.clear()

    def _flush_para():
        if para_parts:
            result.append(" ".join(para_parts) + "\n")
            para_parts.clear()

    def _is_full_width_line(bbox: Optional[Tuple[float, float, float, float]]) -> bool:
        """Return True when the block spans close to the full page width."""
        if bbox is None or effective_page_width <= 0:
            return True  # conservative: assume full width → allow merge
        block_width = bbox[2] - bbox[0]  # right - left
        return block_width >= effective_page_width * 0.78

    def _looks_wrapped_continuation(
        prev_bbox_local: Optional[Tuple[float, float, float, float]],
        cur_bbox_local: Optional[Tuple[float, float, float, float]],
        prev_text_local: Optional[str],
        cur_text_local: str,
        gap_local: float,
    ) -> bool:
        """Conservative fallback for wrapped lines inside one paragraph."""
        if prev_bbox_local is None or cur_bbox_local is None:
            return False
        if gap_local <= 0:
            return False

        # New bullet/list/numbered item should usually start a new paragraph.
        if list_start_re.match(cur_text_local):
            return False

        prev_left = prev_bbox_local[0]
        cur_left = cur_bbox_local[0]
        left_delta = abs(cur_left - prev_left)
        if left_delta > max(body_size * 1.2, 10.0):
            return False

        if effective_page_width > 0:
            prev_center = (prev_bbox_local[0] + prev_bbox_local[2]) / 2.0
            cur_center = (cur_bbox_local[0] + cur_bbox_local[2]) / 2.0
            if abs(cur_center - prev_center) > effective_page_width * 0.35:
                return False

        # If previous line already ends with strong sentence punctuation,
        # keep conservative unless current line is clearly a continuation.
        if prev_text_local and prev_text_local.rstrip().endswith(("。", "！", "？", ".", "!", "?", ";", "；")):
            if list_start_re.match(cur_text_local):
                return False
            # If the previous line is noticeably short (not full-width),
            # it is very likely the last line of a paragraph.
            if not _is_full_width_line(prev_bbox_local):
                return False

        return True

    for y_top, item_type, md_text, bbox in items:
        # Non-body items (headings, images, tables) flush the paragraph
        if item_type != "body":
            _flush_para()
            _flush_toc()
            result.append(md_text + "\n")
            prev_y = None
            prev_bbox = None
            prev_text = None
            prev_lr_idx = -1
            continue

        text = md_text.strip()
        if not text:
            continue

        # --- Dot-leader lines (TOC entries) are never merged ---
        if _dot_leader_re.search(text):
            _flush_para()          # flush any preceding normal paragraph
            toc_parts.append(text)
            prev_y = y_top
            prev_bbox = bbox
            prev_text = text
            prev_lr_idx = -1
            continue
        # If previous was a TOC line but this one isn't, flush TOC group
        if toc_parts:
            _flush_toc()

        cur_lr_idx = _find_lr_paragraph(bbox)

        if prev_y is not None and para_parts:
            gap = prev_y - y_top   # positive = moving downward on page

            # --- PRIMARY: LR paragraph grouping ---
            # If both the current and previous block fall inside the SAME
            # LR paragraph, they are part of the same paragraph.
            if (cur_lr_idx >= 0 and cur_lr_idx == prev_lr_idx
                    and abs(gap) < para_gap_threshold * 3):
                # Same LR paragraph → merge
                para_parts.append(text)
            elif gap > para_gap_threshold or gap < -para_gap_threshold:
                # Large gap (or column jump) — allow conservative continuation
                # for slightly loose line spacing if geometry strongly matches.
                if (
                    gap > 0
                    and gap <= para_gap_threshold * 1.8
                    and _looks_wrapped_continuation(
                        prev_bbox, bbox, prev_text, text, gap
                    )
                ):
                    para_parts.append(text)
                else:
                    _flush_para()
                    para_parts.append(text)
            elif abs(gap) <= same_line_threshold:
                # Same visual line (e.g. after superscript split)
                if para_parts:
                    last = para_parts[-1]
                    if (last and text
                            and last[-1].isalnum() and text[0].isalnum()):
                        para_parts[-1] = last + " " + text
                    else:
                        para_parts[-1] = last + text
                else:
                    para_parts.append(text)
            else:
                # Normal line gap — fallback to width heuristic
                if _is_full_width_line(prev_bbox) or _looks_wrapped_continuation(
                    prev_bbox, bbox, prev_text, text, gap
                ):
                    para_parts.append(text)
                else:
                    _flush_para()
                    para_parts.append(text)
        else:
            para_parts.append(text)

        prev_y = y_top
        prev_bbox = bbox
        prev_text = text
        prev_lr_idx = cur_lr_idx

    _flush_para()
    _flush_toc()
    return result


def _convert_page(
    page: PageContent,
    bm_heading_map: Dict[Tuple[int, str], int],
    font_stats: Dict,
    include_images: bool,
    image_base_path: str,
    hf_texts: FrozenSet[str] = frozenset(),
    html_merged_table: bool = False,
) -> List[str]:
    """Convert a single page's content to Markdown lines."""
    lines: List[str] = []
    link_map = _build_link_map(page)
    body_size = font_stats.get("body_size", 12.0)

    # Build a quick lookup for LR headings:
    # For each text block we check if its centre falls inside an LR heading bbox.
    lr_headings: List[LRHeadingInfo] = page.lr_headings

    def _lr_match_heading(block_bbox):
        """Return the LRHeadingInfo whose bbox contains the block centre, or None."""
        if not lr_headings or not block_bbox:
            return None
        bx = (block_bbox[0] + block_bbox[2]) / 2.0
        by = (block_bbox[1] + block_bbox[3]) / 2.0
        for lrh in lr_headings:
            lb, bb, rb, tb = lrh.bbox
            if lb - 5 <= bx <= rb + 5 and bb - 5 <= by <= tb + 5:
                return lrh
        return None

    # Track LR headings that have already been emitted (by id) so that
    # multi-line headings (e.g. a wrapped heading whose text is split into
    # two text blocks) are emitted only once with the full combined text.
    _emitted_lr_headings: set = set()

    # Track bookmarks whose full title has been emitted for a prefix-matched
    # multi-line heading, so continuation blocks can be skipped.
    _consumed_bm_prefixes: set = set()

    # (y_top, item_type, md_text, bbox_or_None)
    items: List[_MergeItem] = []

    # ---- text blocks ----
    for block in page.text_blocks:
        text = re.sub(r"\s+", " ", block.text).strip()
        if not text:
            continue

        # Skip header / footer blocks
        if hf_texts and _is_header_footer(block, page.height, hf_texts):
            continue

        skip_block = False

        # --- LR heading de-duplication ---
        # When an LR heading spans multiple text blocks (e.g. a wrapped
        # title split across two lines), use the LR heading's combined
        # text for the first block and skip subsequent blocks within the
        # same heading bbox.
        #
        # However, the LR module sometimes misclassifies large body-text
        # regions as a single "heading".  When the combined text is long
        # (>= 120 chars) it is almost certainly body text, so we fall
        # through and let each block be processed individually.
        matched_lrh = _lr_match_heading(block.bbox)
        if matched_lrh is not None:
            lrh_id = id(matched_lrh)
            lrh_text_clean = (
                re.sub(r"\s+", " ", matched_lrh.text).strip()
                if matched_lrh.text else ""
            )
            if _RE_TOC_LINE.search(lrh_text_clean):
                # TOC entry misclassified as heading — use combined
                # text for deduplication but do not treat as heading
                if lrh_id in _emitted_lr_headings:
                    continue
                _emitted_lr_headings.add(lrh_id)
                if lrh_text_clean:
                    text = lrh_text_clean
                matched_lrh = None
            elif len(lrh_text_clean) < 120:
                # Genuine short heading — use combined text & skip dupes
                if lrh_id in _emitted_lr_headings:
                    continue
                _emitted_lr_headings.add(lrh_id)
                if lrh_text_clean:
                    text = lrh_text_clean
            else:
                # Misclassified body text — ignore the LR heading
                matched_lrh = None

        is_lr_heading = matched_lrh is not None

        # --- Heading detection (priority cascade) ---
        heading_level = 0

        # 1) Bookmark match (most reliable for hierarchy)
        #    Use space-stripped normalization so that extra whitespace
        #    in extracted text (e.g. '（ 四）' vs bookmark '（四）')
        #    does not break matching.  Also supports multi-line headings:
        #    when a heading wraps across two text blocks, the first block
        #    (a prefix of the bookmark title) emits the full bookmark
        #    title and the second block (a suffix) is skipped.
        if not _RE_TOC_LINE.search(text):
            text_stripped = _strip_spaces(text)
            if len(text_stripped) >= 4:
                # Check if this block is a continuation of a consumed
                # multi-line heading → skip entirely.
                for bm_key_c in _consumed_bm_prefixes:
                    if bm_key_c[0] == page.page_index:
                        bm_stripped_c = _strip_spaces(bm_key_c[1])
                        if text_stripped in bm_stripped_c:
                            skip_block = True
                            break

                if not skip_block:
                    for (bm_page, bm_title), bm_level in bm_heading_map.items():
                        if bm_page != page.page_index or not bm_title:
                            continue
                        bm_stripped = _strip_spaces(bm_title)
                        if not bm_stripped:
                            continue
                        if (text_stripped == bm_stripped or
                                bm_stripped in text_stripped or
                                text_stripped in bm_stripped):
                            heading_level = bm_level
                            # If text is a proper substring of the bookmark
                            # title, this is a multi-line heading.  Emit the
                            # full bookmark title and mark as consumed so
                            # subsequent continuation blocks are skipped.
                            if (text_stripped != bm_stripped and
                                    text_stripped in bm_stripped):
                                text = bm_title
                                _consumed_bm_prefixes.add(
                                    (bm_page, bm_title))
                            break

        if skip_block:
            continue

        # 2) Font-size rank heuristic
        if heading_level == 0:
            heading_level = _heading_level_from_font(
                block.font_size, block.is_bold, font_stats, text
            )

        # 3) LR confirmation: if LR says this is a heading but font-size
        #    didn't detect it (body-size text), promote it using numbering
        #    pattern or assign a default sub-heading level.
        if heading_level == 0 and is_lr_heading and len(text) < 80:
            num_level = _heading_level_from_numbering(text)
            if num_level > 0:
                heading_level = min(num_level, 6)
            else:
                # LR says heading, no numbering → generic sub-heading
                max_rank = max(
                    font_stats.get("size_rank_map", {}).values(), default=0
                )
                heading_level = min(max_rank + 1, 6)

        # 4) Numbering-pattern hint: promote short body-text that looks
        #    like a heading (e.g. "第三章 …" or "3.2.1 …") but was at
        #    body font size and missed by font heuristic.
        #    Only if text is short and not a TOC line.
        if heading_level == 0 and len(text) < 60:
            num_level = _heading_level_from_numbering(text)
            if num_level > 0 and block.is_bold:
                # Bold + numbering → confident sub-heading
                heading_level = min(num_level, 6)

        md_text = _apply_links(text, link_map)
        y_pos = block.bbox[3] if block.bbox[3] != float('-inf') else 0
        blk_bbox = block.bbox if block.bbox[3] != float('-inf') else None

        if heading_level > 0:
            prefix = "#" * heading_level
            items.append((y_pos, "heading", f"{prefix} {md_text}", blk_bbox))
        else:
            if block.is_italic:
                md_text = f"*{md_text}*"
            items.append((y_pos, "body", md_text, blk_bbox))

    # ---- images ----
    if include_images:
        for img in page.image_blocks:
            img_md = f"![image]({image_base_path}/{img.image_path})"
            y_pos = img.bbox[3] if img.bbox[3] != float('-inf') else 0
            items.append((y_pos, "image", img_md, None))

    # ---- tables ----
    for tbl in page.table_blocks:
        table_md = _render_table_md(tbl, html_merged=html_merged_table)
        if table_md:
            y_pos = tbl.bbox[3] if tbl.bbox[3] != float('-inf') else 0
            items.append((y_pos, "table", table_md, None))

    # --- Code blocks from false-table reconstruction ---
    for cb in page.code_blocks:
        y_pos = cb.bbox[3]  # top of code region
        code_md = "```\n" + "\n".join(cb.lines) + "\n```"
        items.append((y_pos, "code", code_md, None))

    # Sort by vertical position (top of page first = largest y)
    items.sort(key=lambda x: -x[0])

    # Merge consecutive body-text blocks into paragraphs
    merged = _merge_body_text(
        items, body_size,
        page_width=page.width,
        lr_paragraphs=page.lr_paragraphs,
    )
    lines.extend(merged)

    # Page separator
    if lines:
        lines.append("")

    return lines


def _normalize(text: str) -> str:
    """Normalize text for fuzzy matching."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _strip_spaces(text: str) -> str:
    """Strip ALL whitespace for bookmark matching.

    PDF text extraction often introduces extra spaces inside CJK text
    (e.g. '（ 四）' vs bookmark '（四）').  Removing all whitespace
    gives a canonical form suitable for substring / prefix matching.
    """
    return re.sub(r"\s+", "", text.strip())


def _apply_links(text: str, link_map: Dict[str, str]) -> str:
    """Replace known link texts with Markdown links."""
    for link_text, url in link_map.items():
        if link_text in text:
            md_link = f"[{link_text}]({url})"
            text = text.replace(link_text, md_link, 1)
    return text


# ---------------------------------------------------------------------------
# Table rendering helpers
# ---------------------------------------------------------------------------


def _has_merged_cells(table: TableBlock) -> bool:
    """Return True if the table appears to have merged cells.

    Detection heuristics (checked in order):
    1. SDK-reported colspan/rowspan > 1 (rare — LR module usually reports 1).
    2. Rows have differing cell counts — a strong bbox-level indicator that
       some cells span multiple rows or columns.
    """
    # 1) SDK attributes
    if any(c.colspan > 1 or c.rowspan > 1
           for row in table.rows for c in row):
        return True

    # 2) Uneven cell counts across rows
    cell_counts = [len(row) for row in table.rows]
    if len(set(cell_counts)) > 1:
        return True

    return False


def _render_table_html(table: TableBlock) -> str:
    """Render a TableBlock as an HTML <table> with bbox-inferred merging.

    Analyses cell bounding boxes to determine:
    - **Column positions** — clustered left edges of all cells.
    - **Row positions** — clustered bottom/top edges of all cells (each
      unique y-band = one logical row).
    - **colspan** — how many column boundaries a cell spans horizontally.
    - **rowspan** — how many row boundaries a cell spans vertically.

    This handles the common case where the LR module does not report
    colspan/rowspan but the bboxes clearly show merged regions.
    """
    if not table.rows:
        return ""

    all_have_bbox = all(
        c.bbox is not None for row in table.rows for c in row
    )
    if not all_have_bbox:
        # Cannot infer merging without bboxes — fall back to simple HTML
        return _render_table_html_simple(table)

    TOLERANCE = 5.0

    # --- Collect all cells with their bboxes ---
    all_cells: List[TableCell] = []
    for row in table.rows:
        for cell in row:
            all_cells.append(cell)

    if not all_cells:
        return ""

    # --- Build column positions from left edges ---
    left_edges = sorted(set(c.bbox[0] for c in all_cells))
    col_positions: List[float] = [left_edges[0]]
    for e in left_edges[1:]:
        if e - col_positions[-1] > TOLERANCE:
            col_positions.append(e)
    n_cols = len(col_positions)

    # --- Build row positions from y-bands ---
    # Each LR-provided row is one visual row; use the top (bbox[3]) of each
    # row's cells as the row-top boundary and the bottom (bbox[1]) as the
    # row-bottom boundary.  We cluster distinct y-bands.
    y_boundaries: List[float] = []
    for row in table.rows:
        tops = [c.bbox[3] for c in row if c.bbox]
        bottoms = [c.bbox[1] for c in row if c.bbox]
        if tops:
            y_boundaries.append(max(tops))
        if bottoms:
            y_boundaries.append(min(bottoms))

    # Cluster y-boundaries into unique row edges (sorted descending = top-first)
    y_sorted = sorted(set(y_boundaries), reverse=True)
    row_edges: List[float] = [y_sorted[0]] if y_sorted else []
    for y in y_sorted[1:]:
        if row_edges[-1] - y > TOLERANCE:
            row_edges.append(y)
    # row_edges is sorted top→bottom (descending y).
    # Logical row i occupies the band from row_edges[i] down to row_edges[i+1].
    n_rows = max(len(row_edges) - 1, 1)

    def _find_col(x: float) -> int:
        best = 0
        best_dist = abs(x - col_positions[0])
        for i in range(1, n_cols):
            d = abs(x - col_positions[i])
            if d < best_dist:
                best = i
                best_dist = d
        return best

    def _find_row(y: float) -> int:
        """Map a y coordinate to the row index (0 = topmost)."""
        best = 0
        best_dist = abs(y - row_edges[0])
        for i in range(1, len(row_edges)):
            d = abs(y - row_edges[i])
            if d < best_dist:
                best = i
                best_dist = d
        return best

    # --- Place cells into a grid and compute spans ---
    occupied: List[List[bool]] = [
        [False] * n_cols for _ in range(n_rows)
    ]

    # Each entry: (row, col, rowspan, colspan, text, is_header)
    placements: List[Tuple[int, int, int, int, str, bool]] = []

    for lr_ri, row in enumerate(table.rows):
        is_header = (lr_ri == 0) or all(c.is_header for c in row)
        for cell in row:
            if not cell.bbox:
                continue
            # Column span
            start_col = _find_col(cell.bbox[0])
            cs = 1
            for i in range(start_col + 1, n_cols):
                if col_positions[i] < cell.bbox[2] - TOLERANCE:
                    cs += 1
                else:
                    break

            # Row span: find which row-edges the cell's top and bottom map to
            top_row = _find_row(cell.bbox[3])   # top of cell → first row
            bot_row = _find_row(cell.bbox[1])   # bottom of cell → last edge
            rs = max(bot_row - top_row, 1)

            # Skip already occupied slots
            while start_col < n_cols and occupied[top_row][start_col]:
                start_col += 1
            if start_col >= n_cols:
                continue

            # Mark occupied
            for r in range(rs):
                for c in range(cs):
                    if top_row + r < n_rows and start_col + c < n_cols:
                        occupied[top_row + r][start_col + c] = True

            placements.append((top_row, start_col, rs, cs, cell.text.strip(), is_header))

    # --- Render HTML ---
    lines: List[str] = ['<table>']
    # Sort placements by row then column
    placements.sort(key=lambda p: (p[0], p[1]))

    current_row = -1
    for (r, c, rs, cs, text, is_hdr) in placements:
        if r != current_row:
            if current_row >= 0:
                lines.append('  </tr>')
            lines.append('  <tr>')
            current_row = r
        tag = 'th' if is_hdr else 'td'
        attrs = ''
        if cs > 1:
            attrs += f' colspan="{cs}"'
        if rs > 1:
            attrs += f' rowspan="{rs}"'
        lines.append(f'    <{tag}{attrs}>{text}</{tag}>')
    if current_row >= 0:
        lines.append('  </tr>')
    lines.append('</table>')
    return '\n'.join(lines)


def _render_table_html_simple(table: TableBlock) -> str:
    """Fallback HTML rendering without bbox-based merge detection."""
    if not table.rows:
        return ""
    n_rows = len(table.rows)
    max_cols = max((sum(c.colspan for c in row) for row in table.rows), default=0)
    if max_cols == 0:
        return ""

    occupied: List[List[bool]] = [
        [False] * max_cols for _ in range(n_rows)
    ]
    lines: List[str] = ['<table>']
    for ri, row in enumerate(table.rows):
        tag = 'th' if (ri == 0 or all(c.is_header for c in row)) else 'td'
        lines.append('  <tr>')
        col_cursor = 0
        for cell in row:
            while col_cursor < max_cols and occupied[ri][col_cursor]:
                col_cursor += 1
            if col_cursor >= max_cols:
                break
            cs = min(cell.colspan, max_cols - col_cursor)
            rs = min(cell.rowspan, n_rows - ri)
            for r in range(rs):
                for c in range(cs):
                    if ri + r < n_rows and col_cursor + c < max_cols:
                        occupied[ri + r][col_cursor + c] = True
            text = cell.text.strip()
            attrs = ''
            if cs > 1:
                attrs += f' colspan="{cs}"'
            if rs > 1:
                attrs += f' rowspan="{rs}"'
            lines.append(f'    <{tag}{attrs}>{text}</{tag}>')
            col_cursor += cs
        lines.append('  </tr>')
    lines.append('</table>')
    return '\n'.join(lines)


def _render_table_md(table: TableBlock, html_merged: bool = False) -> str:
    """
    Render a TableBlock as a Markdown pipe-table.

    If *html_merged* is True and the table contains merged cells
    (colspan > 1 or rowspan > 1), render as an HTML <table> instead.

    Uses cell bounding boxes (when available) to determine correct column
    alignment — this handles cases where the LR module fails to report
    rowspan and cells would otherwise be placed in the wrong column.

    Falls back to colspan/rowspan-based grid building when bbox data is
    not available.
    """
    if not table.rows:
        return ""

    # If requested, use HTML for tables with merged cells
    if html_merged and _has_merged_cells(table):
        return _render_table_html(table)

    # --- Decide grid-building strategy ---
    all_have_bbox = all(
        c.bbox is not None for row in table.rows for c in row
    )

    if all_have_bbox:
        grid, max_cols = _build_grid_bbox(table)
    else:
        grid, max_cols = _build_grid_span(table)

    if max_cols == 0 or not grid:
        return ""

    # --- Render Markdown table lines ---
    lines: List[str] = []
    for ri, grid_row in enumerate(grid):
        line = "| " + " | ".join(grid_row) + " |"
        lines.append(line)
        if ri == 0:
            sep = "| " + " | ".join(["---"] * max_cols) + " |"
            lines.append(sep)

    return "\n".join(lines)


def _build_grid_span(table: TableBlock) -> Tuple[List[List[str]], int]:
    """Build table grid using colspan/rowspan attributes (original logic)."""
    max_cols = 0
    for row in table.rows:
        cols_in_row = sum(c.colspan for c in row)
        if cols_in_row > max_cols:
            max_cols = cols_in_row
    if max_cols == 0:
        return [], 0

    n_rows = len(table.rows)
    grid: List[List[str]] = [["" for _ in range(max_cols)] for _ in range(n_rows)]
    occupied: List[List[bool]] = [
        [False for _ in range(max_cols)] for _ in range(n_rows)
    ]

    for ri, row in enumerate(table.rows):
        col_cursor = 0
        for cell in row:
            while col_cursor < max_cols and occupied[ri][col_cursor]:
                col_cursor += 1
            if col_cursor >= max_cols:
                break

            text = cell.text.replace("|", "\\|")
            cs = min(cell.colspan, max_cols - col_cursor)
            rs = min(cell.rowspan, n_rows - ri)

            for c in range(cs):
                if col_cursor + c < max_cols:
                    grid[ri][col_cursor + c] = text if c == 0 else ""
                    for r in range(1, rs):
                        if ri + r < n_rows:
                            occupied[ri + r][col_cursor + c] = True

            col_cursor += cs

    return grid, max_cols


def _build_grid_bbox(table: TableBlock) -> Tuple[List[List[str]], int]:
    """Build table grid using cell bounding boxes for column alignment.

    Clusters the left edges of all cells to determine column positions,
    then places each cell in its correct column based on its bbox.
    This correctly handles missing rowspan information from the LR module.
    """
    TOLERANCE = 5.0  # points

    # Collect all left edges
    left_edges: List[float] = []
    for row in table.rows:
        for cell in row:
            if cell.bbox:
                left_edges.append(cell.bbox[0])

    if not left_edges:
        return _build_grid_span(table)

    # Cluster left edges into column positions
    sorted_edges = sorted(set(left_edges))
    col_positions = [sorted_edges[0]]
    for edge in sorted_edges[1:]:
        if edge - col_positions[-1] > TOLERANCE:
            col_positions.append(edge)

    max_cols = len(col_positions)
    if max_cols == 0:
        return [], 0

    n_rows = len(table.rows)
    grid: List[List[str]] = [["" for _ in range(max_cols)] for _ in range(n_rows)]

    def _find_col(x: float) -> int:
        """Find the column index for a given x position."""
        best = 0
        best_dist = abs(x - col_positions[0])
        for i in range(1, len(col_positions)):
            d = abs(x - col_positions[i])
            if d < best_dist:
                best = i
                best_dist = d
        return best

    for ri, row in enumerate(table.rows):
        for cell in row:
            if not cell.bbox:
                continue
            start_col = _find_col(cell.bbox[0])

            # Determine colspan: count how many column boundaries
            # fall within the cell's horizontal range
            span = 1
            for i in range(start_col + 1, max_cols):
                if col_positions[i] < cell.bbox[2] - TOLERANCE:
                    span += 1
                else:
                    break

            text = cell.text.replace("|", "\\|")
            for c in range(span):
                if start_col + c < max_cols:
                    grid[ri][start_col + c] = text if c == 0 else ""

    return grid, max_cols
