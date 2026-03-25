"""Copyright (c) 2026 Zhi Lin. All rights reserved.
Author: zhi_lin@qq.com
"""

"""命令行 PDF → Markdown 转换脚本。

用法:
    python convert.py input.pdf                     # 输出到 input.md
    python convert.py input.pdf -o output.md        # 指定输出路径
    python convert.py input.pdf --no-images         # 不提取图片
    python convert.py input.pdf --toc               # 生成目录
    python convert.py input.pdf --keep-header-footer # 保留页眉页脚
"""

import sys
import os
import argparse

sys.path.insert(0, os.path.dirname(__file__))

from app.pdf_parser import parse_pdf
from app.md_converter import convert_to_markdown


def main():
    parser = argparse.ArgumentParser(description="PDF → Markdown 转换工具")
    parser.add_argument("pdf", help="输入 PDF 文件路径")
    parser.add_argument("-o", "--output", help="输出 Markdown 文件路径（默认与 PDF 同名 .md）")
    parser.add_argument("--no-images", action="store_true", help="不提取图片（默认提取）")
    parser.add_argument("--toc", action="store_true", help="生成目录（默认不生成）")
    parser.add_argument("--keep-header-footer", action="store_true", help="保留页眉页脚（默认过滤）")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"错误: 文件不存在 - {args.pdf}", file=sys.stderr)
        sys.exit(1)

    out_path = args.output or os.path.splitext(args.pdf)[0] + ".md"

    print(f"正在转换: {args.pdf}")
    content = parse_pdf(args.pdf)
    md = convert_to_markdown(
        content,
        include_images=not args.no_images,
        include_toc=args.toc,
        skip_header_footer=not args.keep_header_footer,
    )

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(md)

    print(f"完成: {out_path} ({len(md)} 字符, {len(content.pages)} 页)")


if __name__ == "__main__":
    main()
