[中文](README.md) | [English](README_EN.md)

# PDF2MD — PDF 转 Markdown 服务

基于 **Foxit PDF SDK (Python)** + **FastAPI** 构建的 PDF 转 Markdown Web 服务。

> 🌐 **在线体验：** [https://pdf2md.doc-tool.qihangsoftware.cn/](https://pdf2md.doc-tool.qihangsoftware.cn/)  —  无需部署，直接使用

---

## 功能特性

- **智能文本提取** — 逐字符获取字体名称、字号、粗体/斜体属性及坐标位置
- **自动标题识别** — 优先使用 PDF 书签(Bookmark)映射标题层级；其次利用 SDK 版面分析(LR)模块识别标题；最终回退到字号比例启发式算法
- **表格提取** — 通过 SDK Layout Recognition 模块自动检测并提取表格，输出为 Markdown 表格语法
- **伪表格修正** — 自动识别被误判为表格的章节标题（如跨行排列的编号标题），将其还原为正确的 Markdown 标题
- **段落智能合并** — 自动识别换行续行、段内折行等情况，将同一段落的多行文本合并为连续段落，消除多余空行
- **图片提取** — 自动识别并导出页面中的嵌入图片为 PNG 格式
- **超链接保留** — 识别文本中的 URL 并转换为 Markdown 链接语法
- **页眉页脚过滤** — 基于频率统计自动识别并跳过重复出现的页眉、页脚文本
- **目录生成** — 从 PDF 书签树自动生成嵌套 Markdown 目录
- **Web UI** — 支持拖拽上传、实时转换进度、源码/预览双标签切换、一键下载和复制
- **REST API** — 可供其他系统集成调用
- **命令行工具** — 支持命令行直接转换，便于脚本集成和批量处理

---

## 项目结构

```
pdf2md/
├── app/
│   ├── __init__.py          # 包初始化
│   ├── config.py            # SDK License 及目录配置
│   ├── pdf_parser.py        # Foxit SDK PDF 解析核心模块
│   ├── md_converter.py      # 结构化内容 → Markdown 转换引擎
│   └── main.py              # FastAPI 路由与 Web 服务
├── templates/
│   └── index.html           # Web UI 页面
├── static/                  # 静态资源
├── uploads/                 # 上传的 PDF 文件暂存目录（自动创建）
├── output/                  # Markdown 输出及提取的图片
│   └── images/              # 提取的图片存放目录
├── convert.py               # 命令行转换脚本
├── requirements.txt         # Python 依赖清单
├── run.py                   # Web 服务启动入口
└── README.md
```

---

## 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10/11 或 Linux |
| Python | 3.8 — 3.12 |
| Foxit PDF SDK | 通过 pip 安装 `FoxitPDFSDKPython3` |
| 内存 | 建议 ≥ 4 GB |

---

## 部署步骤

### 1. 克隆项目

```bash
git clone git@github.com:AmyLin2013/pdf2md.git
cd pdf2md
```

### 2. 创建虚拟环境（推荐）

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# Linux / macOS
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

依赖列表：
- `FoxitPDFSDKPython3` — Foxit PDF SDK Python 绑定
- `fastapi` — Web 框架
- `uvicorn[standard]` — ASGI 服务器
- `python-multipart` — 文件上传支持
- `Jinja2` — HTML 模板引擎
- `aiofiles` — 异步文件操作

### 4. 配置 Foxit PDF SDK License（必须）

> **⚠️ 重要提示：** 本项目内置的试用 License 已过期，运行前必须替换为您自己的有效 License。
>
> 前往 [Foxit PDF SDK 官网](https://developers.foxit.com/products/pdf-sdk/) 申请试用或购买商用 License，获取 `SN` 和 `Key` 两个值。

License 配置位于 `app/config.py` 中的 `FOXIT_SN` 和 `FOXIT_KEY` 两个变量：

```python
# app/config.py
FOXIT_SN = os.environ.get("FOXIT_SN", "your_sn_here")
FOXIT_KEY = os.environ.get("FOXIT_KEY", "your_key_here")
```

有两种方式设置您的 License：

**方式 A：直接编辑配置文件**

修改 `app/config.py`，将 `FOXIT_SN` 和 `FOXIT_KEY` 的默认值替换为您的 License 值。

**方式 B：通过环境变量设置（推荐，避免将 License 提交到代码仓库）**

```bash
# Windows PowerShell
$env:FOXIT_SN = "your_sn_here"
$env:FOXIT_KEY = "your_key_here"

# Linux / macOS
export FOXIT_SN="your_sn_here"
export FOXIT_KEY="your_key_here"
```

如果 License 无效或已过期，启动时会报 `e_ErrInvalidLicense` 错误。

### 5. 启动服务

```bash
python run.py
```

服务将在 **http://0.0.0.0:8000** 启动。

启动参数可在 `run.py` 中调整：

```python
uvicorn.run(
    "app.main:app",
    host="0.0.0.0",   # 监听地址，0.0.0.0 允许外部访问
    port=8000,          # 端口号
    reload=True,        # 开发模式热重载（生产环境建议关闭）
)
```

> **生产环境部署建议：** 将 `reload=True` 改为 `reload=False`，并考虑使用 `workers=4`（多进程）提升并发性能。

---

## 使用方式

### 方式一：Web UI

1. 打开浏览器访问 **http://localhost:8000**
2. 将 PDF 文件拖拽到上传区域，或点击选择文件
3. 勾选选项：
   - ✅ **提取图片** — 是否导出 PDF 中的嵌入图片
   - ✅ **生成目录** — 是否从书签生成 Markdown 目录
  - ✅ **过滤页眉页脚** — 是否自动跳过重复页眉页脚
  - ✅ **合并单元格用HTML** — 对含合并单元格的表格输出 HTML `<table>`
4. 点击 **「开始转换」** 按钮
5. 转换完成后可：
   - 在 **「源码」** 标签查看原始 Markdown
   - 在 **「预览」** 标签查看渲染效果
   - 点击 **「下载 .md 文件」** 保存到本地
   - 点击 **「复制 Markdown」** 复制到剪贴板

### 方式二：命令行

```bash
python convert.py input.pdf                      # 输出到 input.md
python convert.py input.pdf -o output.md         # 指定输出路径
python convert.py input.pdf --no-images          # 不提取图片
python convert.py input.pdf --toc                # 生成目录
python convert.py input.pdf --keep-header-footer # 保留页眉页脚
python convert.py input.pdf --html-table         # 合并单元格表格用 HTML 输出
```

### 方式三：REST API

#### 转换 PDF

```bash
curl -X POST http://localhost:8000/api/convert \
  -F "file=@/path/to/your/document.pdf" \
  -F "include_images=true" \
  -F "include_toc=false" \
  -F "skip_header_footer=true" \
  -F "html_merged_table=false"
```

**响应示例：**

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

#### 下载 Markdown 文件

```bash
curl -O http://localhost:8000/api/download/document_1709012345678.md
```

#### 健康检查

```bash
curl http://localhost:8000/api/health
# {"status": "ok", "service": "pdf2md"}
```

#### Swagger 文档

FastAPI 自动生成的交互式 API 文档：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | Web UI 页面 |
| `POST` | `/api/convert` | 上传 PDF 并转换为 Markdown |
| `GET` | `/api/download/{filename}` | 下载已转换的 .md 文件 |
| `GET` | `/api/health` | 服务健康检查 |

### `POST /api/convert` 参数

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `file` | File | ✅ | — | PDF 文件 |
| `include_images` | bool | — | `true` | 是否提取图片 |
| `include_toc` | bool | — | `false` | 是否生成目录 |
| `skip_header_footer` | bool | — | `true` | 是否过滤重复页眉页脚 |
| `html_merged_table` | bool | — | `false` | 是否将含合并单元格的表格输出为 HTML |

---

## 核心技术原理

### PDF 解析流程

```
PDF 文件
  │
  ▼
Library.Initialize(sn, key)   ← SDK 初始化
  │
  ▼
PDFDoc(path) → doc.Load()     ← 加载文档
  │
  ├── Metadata → 标题、作者
  ├── Bookmark → 目录树
  │
  ▼
遍历每一页 PDFPage:
  │
  ├── TextPage(page) → GetCharInfo() 逐字符提取
  │   ├── font_name, font_size  → 标题检测
  │   ├── is_bold, is_italic    → 样式识别
  │   └── char_box (x,y,w,h)   → 布局排序
  │
  ├── LR (Layout Recognition) 版面分析
  │   ├── LRHeading → 章节标题（含层级）
  │   ├── LRTable → 表格行列数据
  │   └── 伪表格检测 → 单行标题被误判为表格时
  │       自动还原为 LRHeading
  │
  ├── GraphicsObjects → ImageObject → Bitmap → PNG
  │
  └── PageTextLinks → URL + 文本范围
```

### 标题识别策略

采用三级标题识别策略，按优先级依次命中：

1. **书签优先**：如果 PDF 包含书签(Bookmark)，将书签标题与页面文本块进行模糊匹配，匹配到的文本块标记为对应层级的标题
2. **LR 版面分析**：利用 Foxit SDK 的 Layout Recognition 模块自动识别标题区域及层级。当 SDK 将跨行排列的编号标题（如 `3.2 企业自身发展……`）误判为单行表格时，解析器会通过正则匹配章节编号模式（`X.Y`、`第X章`、`一、`、`（一）` 等）检测并修正，将其提升为正确层级的标题
3. **字号启发式**：统计全文档字号分布，找到"正文字号"（占字符总数最多的字号），然后按比例映射标题层级：

   | 字号/正文字号 比值 | 标题层级 |
   |-------------------|----------|
   | ≥ 2.0 | H1 |
   | ≥ 1.7 | H2 |
   | ≥ 1.4 | H3 |
   | ≥ 1.2（粗体） | H4 |
   | ≥ 1.05（粗体） | H5 |
   | = 1.0（粗体） | H6 |

### 段落合并策略

将 PDF 中因排版换行产生的多行文本智能合并为连续段落：

1. **续行检测**：基于行的几何位置（左缩进、行间距）和文本内容（是否以标点结尾、是否为列表项）判断相邻行是否属于同一段落
2. **内部空白折叠**：PDF 文本块中可能包含多余的空格和换行，提取后统一折叠为单个空格
3. **空行清理**：后处理阶段检测并移除段落之间被意外插入的空行（前行未以句末标点结尾、后行以中文或字母开头）

### 表格处理策略

1. **LR 表格提取**：通过 SDK 版面分析自动识别表格区域，按行列提取单元格文本，输出为 Markdown 管道表格格式
2. **伪表格过滤**：对单行少列的"表格"进行启发式检测——如果合并后的文本匹配章节编号模式，则判定为被误识别的标题而非真实表格，自动纠正

---

## 常见问题

### Q: SDK 初始化失败怎么办？

最常见的原因是 License 无效。本项目内置的试用 License 已过期，请参照上方 [配置 Foxit PDF SDK License](#4-配置-foxit-pdf-sdk-license必须) 章节获取并设置您自己的 License。

检查 `app/config.py` 中的 `FOXIT_SN` 和 `FOXIT_KEY` 是否正确，或环境变量是否已设置。错误码含义：
- `e_ErrInvalidLicense` — License 无效或已过期
- `e_ErrParam` — 参数格式错误

### Q: 中文 PDF 乱码？

Foxit SDK 内置了中文字体支持，通常不会出现乱码。如遇到问题，确保系统安装了中文字体。

### Q: 图片没有被提取？

- 确认勾选了「提取图片」选项
- 部分 PDF 中的图片可能以矢量路径(PathObject)形式存在，而非位图(ImageObject)，这类图片暂不支持提取
- 检查 `output/images/` 目录是否有写入权限

### Q: 转换速度慢？

- PDF 页数多时解析耗时较长，主要瓶颈在逐字符信息提取
- 如不需要精确的文本样式信息，可修改 `pdf_parser.py` 使用 `TextPage.GetText()` 替代逐字符遍历
- 生产环境建议关闭 `reload` 模式

### Q: 如何处理加密 PDF？

当前版本不支持带密码的 PDF 文件，上传加密文件会返回 400 错误提示。

---

## License

本项目使用 Foxit PDF SDK，需遵守 Foxit 的商用许可协议。
