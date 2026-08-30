# 最小遥感栅格元数据检查 Tool 完整拆解

本文按 Context、Constraint、Acting、Verify、Correct 五个部分，拆解仓库中新增加的 `inspect_raster(path)` 工具。范围包括：

- 为什么需要独立于 `inspect_image` 的栅格元数据入口；
- `gdalinfo -json` 与 Python 工具之间的边界；
- 路径检查、外部进程、JSON 解析和字段规范化；
- CRS、地理变换、旋转像元大小和四角坐标的处理；
- 必要字段、可选字段、warning 与受控错误；
- `inspect-raster` CLI、pytest 模拟和真实 GDAL 验证；
- 当前明确没有实现的统计计算与多时相能力。

这里的五个部分是理解实现的工程视角，不是新的 Agent 运行轨迹。`inspect-raster` 当前直接调用 Tool，没有经过现有 scripted Agent，也不会产生 `task → decision → action → observation → final` 五阶段轨迹。

完整调用关系如下：

~~~text
python -m rs_agent inspect-raster RASTER_PATH
    └─ __main__.main()
        └─ inspect_raster(raster_path)
            ├─ 检查本地普通文件
            ├─ subprocess.run(["gdalinfo", "-json", 抑制选项..., absolute_path])
            ├─ 检查 returncode；非零时读取首条 stderr
            ├─ json.loads(stdout)
            └─ _normalize_raster_info(raw_info, display_path)
                ├─ 校验 driver、size、bands
                ├─ 规范化波段、CRS 和 geotransform
                ├─ 计算 pixel_size
                ├─ 规范化四角坐标
                └─ 汇总 warnings
~~~

路径、进程、JSON 或必要字段出现受控失败时，`inspect_raster` 抛出 `RasterInspectionError`；`main()` 捕获后向 stdout 输出 `{"ok": false, "error": "..."}` 并返回退出码 1。

## 1. Context：为什么增加这条栅格元数据链路

### 1.1 通用图片检查和遥感栅格检查解决不同问题

现有工具的公开入口是：

~~~python
def inspect_image(path: str | Path) -> dict[str, object]:
    """Return JSON-serializable metadata and per-band statistics for an image."""
~~~

`inspect_image` 面向教学用通用图片。它通过 Pillow 打开图片，调用 `image.load()` 强制读取全部像元，再计算每个通道的最小值、最大值和均值。这个行为适合展示一次真实 Tool 调用，但它没有 CRS、仿射地理变换或地理范围等遥感栅格概念。

新工具的公开入口是：

~~~python
def inspect_raster(path: str | Path) -> dict[str, object]:
    """Return a compact, JSON-serializable view of GDAL raster metadata."""
~~~

`inspect_raster` 面向低成本栅格观察。它不复用 Pillow 统计链，也不改变 `inspect_image` 的现有行为；它把元数据读取交给系统中的 GDAL 命令行工具，并只返回本项目需要的紧凑字段。

### 1.2 数据流是“读取、裁剪、规范化”

~~~text
gdalinfo -json <path>
        ↓
检查进程退出码并解析 stdout JSON
        ↓
选择 driver、size、bands、CRS、geotransform、corners
        ↓
统一字段名称、空值、warning 和错误
        ↓
输出紧凑 JSON
~~~

GDAL 原始 JSON 是上游格式，`inspect_raster` 的返回字典是本项目自己的稳定契约。调用者不需要知道 `driverShortName`、`geoTransform` 或 `upperLeft` 这些 GDAL 命名，也不会收到无关的 metadata、STAC、文件列表或统计对象。

### 1.3 运行前提是系统能够执行 gdalinfo

~~~bash
uv run python -m rs_agent inspect-raster path/to/image.tif
~~~

Python 项目没有新增 GDAL bindings、Rasterio 或其他运行时依赖。命令能否成功取决于当前操作系统的 `PATH` 中是否存在可执行的 `gdalinfo`。这使 Python 环境保持最小，同时把格式识别和栅格元数据读取交给已经成熟的 GDAL 驱动体系。

项目调用的是 [GDAL 官方 `gdalinfo`](https://gdal.org/en/stable/programs/gdalinfo.html) 的 JSON 输出模式。普通 `-json` 调用不附加 `-stats`；即使原始 JSON 中带有驱动可直接提供的附加内容，本工具也不会把统计、直方图或全量 metadata 放进公开结果。

### 1.4 成功结果是固定结构

~~~json
{
  "ok": true,
  "path": "examples/sample.tif",
  "driver": {
    "short_name": "GTiff",
    "long_name": "GeoTIFF"
  },
  "width": 4,
  "height": 3,
  "band_count": 2,
  "bands": [
    {
      "index": 1,
      "data_type": "UInt16",
      "color_interpretation": "Gray",
      "nodata": 0
    },
    {
      "index": 2,
      "data_type": "UInt16",
      "color_interpretation": "Undefined",
      "nodata": 0
    }
  ],
  "crs_wkt": "GEOGCRS[...]",
  "geotransform": [0.0, 1.0, 0.0, 0.0, 0.0, -1.0],
  "pixel_size": {
    "x": 1.0,
    "y": 1.0
  },
  "corners": {
    "upper_left": [0.0, 0.0],
    "lower_left": [0.0, -3.0],
    "lower_right": [4.0, -3.0],
    "upper_right": [4.0, 0.0]
  },
  "warnings": []
}
~~~

所有值都可以直接交给 `json.dumps`。工具不会返回 `Path`、`CompletedProcess`、GDAL 对象或原始异常。可选信息缺失时，相应字段仍然存在并设为 `null`，同时在 `warnings` 中解释信息缺口。

### 1.5 文件职责

- `src/rs_agent/tools.py`：定义 `RasterInspectionError`、执行 `gdalinfo`、解析并规范化元数据；
- `src/rs_agent/__main__.py`：注册 `inspect-raster` 子命令，输出成功或受控失败 JSON；
- `tests/test_raster_tools.py`：模拟外部进程，验证字段、warning、超时和错误边界；
- `tests/test_main.py`：验证新 CLI 的 stdout JSON 和退出码；
- `README.md`：提供安装前提、最短用法和能力边界；
- 本文：解释完整运行机制，不承担路线图状态管理。

## 2. Constraint：工具必须遵守什么边界

### 2.1 公开契约只有函数、字典和受控异常

~~~python
class RasterInspectionError(Exception):
    """Raised when raster metadata cannot be inspected safely."""


def inspect_raster(path: str | Path) -> dict[str, object]:
    """Return a compact, JSON-serializable view of GDAL raster metadata."""
~~~

工具没有 BaseTool、注册表、插件系统、配置对象或 GDAL Python 对象。成功返回普通字典；可预期失败统一抛出 `RasterInspectionError`。CLI 只需要理解这一种受控异常。

### 2.2 当前只接受本地普通文件

~~~python
display_path = str(path)
raster_path = Path(path)

try:
    path_exists = raster_path.exists()
    path_is_file = raster_path.is_file()
except OSError as exc:
    raise RasterInspectionError(
        f"Cannot access raster path: {display_path}"
    ) from exc
~~~

`display_path` 保留调用者传入的路径表达，用于返回结果和错误消息。`Path` 用于本地文件检查；实际命令参数会转为绝对路径，避免以 `-` 开头的相对文件名被 GDAL 当成选项。由于要求必须是普通文件，当前接口不会把目录、GDAL `/vsicurl/` 虚拟路径、URL 或云端数据集当作合法输入。

### 2.3 外部进程调用参数是固定的

~~~python
_GDALINFO_TIMEOUT_SECONDS = 15

execution_path = str(raster_path.absolute())
command = [
    "gdalinfo",
    "-json",
    "-nogcp",
    "-nomd",
    "-norat",
    "-noct",
    "-nofl",
    execution_path,
]
completed = subprocess.run(
    command,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    timeout=_GDALINFO_TIMEOUT_SECONDS,
    check=False,
    shell=False,
)
~~~

命令使用参数列表，路径即使包含空格也仍是一个独立参数。`shell=False` 表示不经过 PowerShell、cmd 或其他 shell 解释；代码不会拼接命令字符串，也不会执行输入路径中的 shell 语法。

`capture_output=True` 同时捕获 stdout 和 stderr；`text=True` 把结果作为文本处理；超时固定为 15 秒。`check=False` 让代码显式检查退出码，从而能把 stderr 裁剪为本项目自己的错误信息。

### 2.4 不请求统计，也不在 Python 中读取像元

~~~python
command = [
    "gdalinfo", "-json", "-nogcp", "-nomd", "-norat", "-noct", "-nofl",
    execution_path,
]
~~~

参数中没有 `-stats`、`-hist`、`-checksum` 或其他会主动计算栅格值摘要的选项；`-nogcp`、`-nomd`、`-norat`、`-noct`、`-nofl` 还会抑制本工具不需要的 GCP、全量 metadata、属性表、颜色表和文件列表。Python 代码也没有调用 Pillow、Rasterio 或 GDAL bindings 打开这个栅格，更没有波段循环和像元读取。

“低成本”指的是只请求元数据，不代表零磁盘访问：GDAL 仍需打开数据集、识别驱动并读取必要的头部或驱动信息。但本工具不会主动触发全量统计，也不会把上游可能携带的统计字段传给调用者。

### 2.5 必要字段和可选字段采用不同策略

GDAL 字段到公开字段的映射如下：

| GDAL JSON | 公开结果 | 处理规则 |
| --- | --- | --- |
| `driverShortName` | `driver.short_name` | 必要，必须是非空字符串 |
| `driverLongName` | `driver.long_name` | 必要，必须是非空字符串 |
| `size[0]` | `width` | 必要，必须是正整数 |
| `size[1]` | `height` | 必要，必须是正整数 |
| `bands` | `bands`、`band_count` | 必要，必须是列表 |
| `bands[].band` | `bands[].index` | 必要，必须是正整数 |
| `bands[].type` | `bands[].data_type` | 必要，必须是非空字符串 |
| `bands[].colorInterpretation` | `bands[].color_interpretation` | 可选，缺失或无效时为 `null` 并 warning |
| `bands[].noDataValue` | `bands[].nodata` | 可选，缺失或不是有限数值时为 `null` 并 warning |
| `coordinateSystem.wkt` | `crs_wkt` | 可选，缺失或无效时为 `null` 并 warning |
| `geoTransform` | `geotransform` | 可选，必须是六个有限数值，否则为 `null` 并 warning |
| `geoTransform` | `pixel_size` | 从六参数计算；结果不是有限数值时与 geotransform 一起为 `null` 并 warning |
| `cornerCoordinates` | `corners` | 可选，四角完整有效才返回对象，否则为 `null` 并 warning |

必要字段决定结果是否还能被称为栅格元数据。driver、尺寸或波段结构不可信时，函数直接失败。CRS、地理变换、四角坐标、颜色解释和 NoData 并不是所有栅格都具备，因此缺失时采用显式降级，不把整个检查判为失败。

当前结构校验刻意保持最小：`bands` 必须是列表，但空列表仍然合法；代码不额外要求 band index 从 1 连续排列，也不检查 index 是否唯一。WKT 只验证为非空字符串，不在这个工具中解析或判断其坐标参考系语义是否正确。

### 2.6 输出使用白名单而不是透传原始 JSON

~~~python
return {
    "ok": True,
    "path": display_path,
    "driver": {
        "short_name": driver_short_name,
        "long_name": driver_long_name,
    },
    "width": width,
    "height": height,
    "band_count": len(bands),
    "bands": bands,
    "crs_wkt": crs_wkt,
    "geotransform": geotransform,
    "pixel_size": pixel_size,
    "corners": corners,
    "warnings": warnings,
}
~~~

结果逐项构造，因此 GDAL 原始 JSON 中的 `metadata`、`files`、`stac`、`gcps`、统计、直方图、overview 或其他扩展字段不会意外泄漏到公开接口。上游版本以后新增字段，也不会无意扩大本工具输出。

### 2.7 新命令不建立新的 Agent policy

~~~python
if args.command == "inspect-raster":
    try:
        result = inspect_raster(args.raster_path)
~~~

CLI 直接调用 `inspect_raster`。它没有构造自然语言 task，也没有调用 `run_image_inspection_agent`。因此 `inspect-raster` 的 stdout 是工具结果本身，而不是现有 `inspect` 命令的五阶段 Agent trace。

### 2.8 明确不在当前范围内的能力

- 不计算最小值、最大值、均值、标准差或直方图；
- 不读取全部像元，不实现 NDVI 或其他指数；
- 不引入 Rasterio 或 GDAL Python bindings；
- 不实现 `validate_pair`、多时相配准、变化检测或完整任务链；
- 不新增 LLM、Agent policy、MCP、工具发现或异步调度；
- 不新增 Python 依赖，不修改 `uv.lock`；
- 不把真实临时 GeoTIFF、baseline、哈希或审计文件加入仓库。

## 3. Acting：一条 inspect-raster 命令如何执行

### 3.1 argparse 注册独立子命令

~~~python
inspect_raster_parser = subparsers.add_parser(
    "inspect-raster", help="inspect low-cost raster metadata with gdalinfo"
)
inspect_raster_parser.add_argument("raster_path", help="path to a raster file")
~~~

`inspect-raster` 和原有 `inspect` 并列。它只接受一个位置参数 `raster_path`，没有统计开关、配置文件或隐藏环境变量。命令入口清楚表达“检查一个栅格文件”。

### 3.2 main 直接调用工具

~~~python
if args.command == "inspect-raster":
    try:
        result = inspect_raster(args.raster_path)
    except RasterInspectionError as exc:
        result = {"ok": False, "error": str(exc)}
        exit_code = 1
    else:
        exit_code = 0
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return exit_code
~~~

成功时，工具字典被格式化为 JSON 并返回退出码 0。受控错误只输出 `ok: false` 和简短 `error`，返回退出码 1。`main` 不捕获宽泛的 `Exception`，所以真正的编程错误不会被伪装成普通输入失败。

### 3.3 工具先验证路径

~~~python
if not path_exists:
    raise RasterInspectionError(f"Raster path does not exist: {display_path}")
if not path_is_file:
    raise RasterInspectionError(
        f"Raster path is not a regular file: {display_path}"
    )
~~~

不存在的路径和目录在启动子进程前就被拒绝。这避免把明显的输入错误交给 GDAL，也使错误语言不依赖不同 GDAL 版本和驱动的 stderr 文案。

### 3.4 启动 gdalinfo 并等待结果

~~~python
execution_path = str(raster_path.absolute())
command = [
    "gdalinfo",
    "-json",
    "-nogcp",
    "-nomd",
    "-norat",
    "-noct",
    "-nofl",
    execution_path,
]
try:
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=_GDALINFO_TIMEOUT_SECONDS,
        check=False,
        shell=False,
    )
~~~

`subprocess.run` 同步等待命令结束，并返回 `CompletedProcess`。stdout 承载待解析的 JSON；stderr 只在外部命令失败时用于形成简短错误。固定超时防止损坏数据集、异常驱动或外部进程无限占住 CLI。

### 3.5 启动阶段错误被分成三类

~~~python
except FileNotFoundError as exc:
    raise RasterInspectionError(
        "gdalinfo is not installed or not available on PATH."
    ) from exc
except subprocess.TimeoutExpired as exc:
    raise RasterInspectionError(
        f"gdalinfo timed out after {_GDALINFO_TIMEOUT_SECONDS} seconds."
    ) from exc
except OSError as exc:
    raise RasterInspectionError("Could not run gdalinfo.") from exc
~~~

`FileNotFoundError` 明确说明系统无法找到 `gdalinfo`；`TimeoutExpired` 说明超过 15 秒；其他启动层 `OSError` 收束为无法运行命令。`from exc` 保留内部异常链供开发调试，但 CLI 只打印自定义错误文本。

### 3.6 非零退出码只保留第一条有效 stderr

~~~python
if completed.returncode != 0:
    detail = _first_meaningful_line(completed.stderr)
    if detail is None:
        detail = f"exit code {completed.returncode}"
    raise RasterInspectionError(f"gdalinfo failed: {detail}")
~~~

外部命令失败后不会继续解析 stdout。工具优先从 stderr 中取第一条非空信息；如果 stderr 没有有效内容，则退回退出码。这样既保留最有用的 GDAL 错误，又不会把多页驱动日志塞进 CLI JSON。

~~~python
def _first_meaningful_line(message: str) -> str | None:
    for line in message.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None
~~~

空行被跳过，第一条有效信息最多保留 200 个字符。这是错误输出的长度边界，不会修改成功结果。

### 3.7 stdout 必须是合法 JSON

~~~python
try:
    raw_info = json.loads(completed.stdout)
except (json.JSONDecodeError, TypeError) as exc:
    raise RasterInspectionError("gdalinfo returned invalid JSON.") from exc

return _normalize_raster_info(raw_info, display_path)
~~~

退出码 0 并不自动意味着输出可信。只有 `json.loads` 成功后，数据才进入规范化函数。JSON 语法错误或不可解析的 stdout 都会被转换为 `RasterInspectionError`。

### 3.8 根对象和 driver 首先被验证

~~~python
if not isinstance(raw_info, dict):
    raise RasterInspectionError("gdalinfo JSON root must be an object.")

driver_short_name = raw_info.get("driverShortName")
driver_long_name = raw_info.get("driverLongName")
if not _is_non_empty_string(driver_short_name) or not _is_non_empty_string(
    driver_long_name
):
    raise _invalid_required_field("driver")
~~~

合法 JSON 也可能是数组、字符串或缺字段对象，因此还要做结构验证。两个 driver 名称共同组成公开 `driver`；缺任意一个或出现空字符串都无法形成完整结果。

### 3.9 size 必须是两个正整数

~~~python
size = raw_info.get("size")
if (
    not isinstance(size, list)
    or len(size) != 2
    or not all(_is_positive_integer(value) for value in size)
):
    raise _invalid_required_field("size")
width, height = size
~~~

GDAL 的 `size` 顺序是宽、高。代码要求数组长度恰好为 2，并拒绝字符串、布尔值、零和负数。通过后才解包为 `width` 和 `height`。

### 3.10 bands 保留顺序并改名

~~~python
raw_bands = raw_info.get("bands")
if not isinstance(raw_bands, list):
    raise _invalid_required_field("bands")

warnings: list[str] = []
bands: list[dict[str, object]] = []
for position, raw_band in enumerate(raw_bands, start=1):
    if not isinstance(raw_band, dict):
        raise _invalid_required_field(f"bands[{position}]")

    band_index = raw_band.get("band")
    data_type = raw_band.get("type")
    if not _is_positive_integer(band_index) or not _is_non_empty_string(data_type):
        raise _invalid_required_field(f"bands[{position}]")
~~~

代码按 GDAL 数组原顺序遍历，不根据 band index 重新排序。每个波段必须是对象，并提供正整数 `band` 和非空字符串 `type`；错误信息中的 `bands[position]` 指向原始列表位置，便于定位结构问题。

~~~python
color_interpretation = raw_band.get("colorInterpretation")
if not _is_non_empty_string(color_interpretation):
    color_interpretation = None
    warnings.append(
        f"Band {band_index} color interpretation is unavailable."
    )

nodata = raw_band.get("noDataValue")
if not _is_number(nodata):
    nodata = None
    warnings.append(f"Band {band_index} NoData value is unavailable.")
~~~

颜色解释和 NoData 是可选信息。缺失、空字符串或无效类型不会让整个工具失败，而是把对应字段设为 `None`，再记录具体波段 warning。NoData 为 0 时仍是有效值，因为代码检查数值类型，而不是用真假值判断。

~~~python
bands.append(
    {
        "index": band_index,
        "data_type": data_type,
        "color_interpretation": color_interpretation,
        "nodata": nodata,
    }
)
~~~

公开波段对象只有四个字段。原始波段里的 block size、description、metadata、statistics、overviews 和其他驱动扩展不会被复制。

### 3.11 CRS 只保留 WKT

~~~python
coordinate_system = raw_info.get("coordinateSystem")
crs_wkt: str | None = None
if isinstance(coordinate_system, dict):
    raw_wkt = coordinate_system.get("wkt")
    if _is_non_empty_string(raw_wkt):
        crs_wkt = raw_wkt
if crs_wkt is None:
    warnings.append("CRS is unavailable.")
~~~

工具只保留 `coordinateSystem.wkt`，不复制 PROJJSON、PROJ 字符串或 STAC 投影字段。没有 CRS 的普通栅格仍可返回尺寸和波段，因此这里采用 `null + warning`。

### 3.12 pixel_size 从六参数 geotransform 计算

~~~python
raw_geotransform = raw_info.get("geoTransform")
geotransform: list[int | float] | None = None
pixel_size: dict[str, float] | None = None
if (
    isinstance(raw_geotransform, list)
    and len(raw_geotransform) == 6
    and all(_is_number(value) for value in raw_geotransform)
):
    pixel_size_x = math.hypot(raw_geotransform[1], raw_geotransform[4])
    pixel_size_y = math.hypot(raw_geotransform[2], raw_geotransform[5])
    if math.isfinite(pixel_size_x) and math.isfinite(pixel_size_y):
        geotransform = raw_geotransform
        pixel_size = {"x": pixel_size_x, "y": pixel_size_y}
    else:
        warnings.append("Geotransform and pixel size are unavailable.")
else:
    warnings.append("Geotransform and pixel size are unavailable.")
~~~

GDAL 六参数变换满足：

~~~text
Xgeo = GT(0) + Xpixel * GT(1) + Yline * GT(2)
Ygeo = GT(3) + Xpixel * GT(4) + Yline * GT(5)
~~~

一个像素沿列方向的地理向量是 `(GT(1), GT(4))`，沿行方向的地理向量是 `(GT(2), GT(5))`。因此像元大小使用欧氏长度：

~~~text
pixel_size.x = hypot(GT(1), GT(4))
pixel_size.y = hypot(GT(2), GT(5))
~~~

如果没有旋转项，`[0, 1, 0, 0, 0, -1]` 得到 `x=1.0, y=1.0`。如果旋转项非零，例如 `[0, 3, 4, 0, 4, -3]`，两个方向的向量长度都为 5。使用 `math.hypot` 而不是简单取 `abs(GT(1))` 和 `abs(GT(5))`，才能兼容旋转栅格；计算后的模长还要再次检查有限性，防止极端有限输入溢出为无穷值。

`pixel_size.x` 和 `pixel_size.y` 分别是列、行步进向量的非负模长，单位由 CRS 决定。它们不保留轴方向、正负号、旋转角或剪切关系；需要完整空间变换时仍应使用原始六参数 `geotransform`。

### 3.13 四角坐标整体规范化

~~~python
key_map = {
    "upper_left": "upperLeft",
    "lower_left": "lowerLeft",
    "lower_right": "lowerRight",
    "upper_right": "upperRight",
}
~~~

公开结果使用 snake_case，GDAL JSON 使用 camelCase。只保留四个角，不返回 `center` 或 WGS84 extent。

~~~python
corners: dict[str, list[float]] = {}
for output_key, gdal_key in key_map.items():
    coordinate = raw_corners.get(gdal_key)
    if (
        not isinstance(coordinate, list)
        or len(coordinate) != 2
        or not all(_is_number(value) for value in coordinate)
    ):
        return None
    corners[output_key] = [float(coordinate[0]), float(coordinate[1])]
return corners
~~~

每个角必须是两个有限数值。只要四角中任意一个缺失或无效，`_normalize_corners` 就返回 `None`，上层把整个 `corners` 设为 `null` 并添加 warning，避免向调用者返回看似完整但实际残缺的范围。

### 3.14 小型类型函数统一结构判断

~~~python
def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False
~~~

这些函数避免在 driver、size、bands、geotransform 和 corners 中重复略有差异的判断。布尔值在 Python 中是整数子类，因此必须显式排除；数值输入还要求有限，从而拒绝无穷值和 NaN。超大整数无法安全转换为浮点数时也按无效可选值处理，不会泄漏 `OverflowError`。

### 3.15 最终结果一次性组装

~~~python
return {
    "ok": True,
    "path": display_path,
    "driver": {
        "short_name": driver_short_name,
        "long_name": driver_long_name,
    },
    "width": width,
    "height": height,
    "band_count": len(bands),
    "bands": bands,
    "crs_wkt": crs_wkt,
    "geotransform": geotransform,
    "pixel_size": pixel_size,
    "corners": corners,
    "warnings": warnings,
}
~~~

`band_count` 由已经验证并规范化的 `bands` 长度计算，不重复信任另一个上游字段。所有可选键始终存在，调用者无需通过键是否存在来区分情况；是否缺失由 `null` 和 `warnings` 明确表达。

## 4. Verify：如何验证主要行为和边界

### 4.1 测试构造接近 GDAL 的原始 JSON

~~~python
def _gdal_info() -> dict[str, object]:
    return {
        "driverShortName": "GTiff",
        "driverLongName": "GeoTIFF",
        "files": ["sample.tif"],
        "size": [4, 3],
        "coordinateSystem": {"wkt": 'PROJCRS["Example"]'},
        "geoTransform": [0, 1, 0, 0, 0, -1],
        "cornerCoordinates": {
            "upperLeft": [0, 0],
            "lowerLeft": [0, -3],
            "lowerRight": [4, -3],
            "upperRight": [4, 0],
            "center": [2, -1.5],
        },
        "bands": [...],
        "metadata": {"ignored": "value"},
        "stac": {"proj:shape": [3, 4]},
        "gcps": {"gcpList": []},
    }
~~~

fixture 故意包含 `files`、`metadata`、`stac`、`gcps`、中心点和波段统计等多余内容。如果成功断言中的结果没有这些键，就证明实现使用白名单裁剪，而不是删除几个已知字段后透传其余内容。

### 4.2 monkeypatch 替代真实外部进程

~~~python
def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    calls.append((command, kwargs))
    return subprocess.CompletedProcess(
        command,
        returncode,
        stdout=json.dumps(payload),
        stderr=stderr,
    )

monkeypatch.setattr("rs_agent.tools.subprocess.run", fake_run)
~~~

单元测试不要求 CI 安装 GDAL，也不访问网络。它用 `CompletedProcess` 模拟真实 stdout、stderr 和退出码，同时记录命令参数，因而既能测试规范化，也能测试外部调用边界。

### 4.3 成功测试锁定完整公开结构

~~~python
result = inspect_raster(raster_path)

assert result == {
    "ok": True,
    "path": str(raster_path),
    "driver": {"short_name": "GTiff", "long_name": "GeoTIFF"},
    "width": 4,
    "height": 3,
    "band_count": 2,
    "bands": [...],
    "crs_wkt": 'PROJCRS["Example"]',
    "geotransform": [0, 1, 0, 0, 0, -1],
    "pixel_size": {"x": 1.0, "y": 1.0},
    "corners": {...},
    "warnings": [],
}
~~~

整字典断言同时验证键名、嵌套层级、字段裁剪、尺寸、波段数量、坐标规范化和空 warning。任何无意增加原始 GDAL 字段的改动都会使测试失败。

### 4.4 命令断言证明没有 shell 和 stats

~~~python
command, options = calls[0]
assert command == [
    "gdalinfo", "-json", "-nogcp", "-nomd", "-norat", "-noct", "-nofl",
    str(raster_path),
]
assert options["capture_output"] is True
assert options["timeout"] == 15
assert options["shell"] is False
~~~

命令数组的精确断言同时证明没有 `-stats`，并锁定无关输出抑制选项。另一个测试用 `-stats.tif` 作为相对文件名，验证传给 GDAL 的最后一个参数已转为绝对路径。超时、输出捕获和 shell 边界也被测试锁定。

### 4.5 波段测试锁定原顺序、类型和 NoData

~~~python
payload["bands"] = list(reversed(payload["bands"]))
_mock_gdalinfo(monkeypatch, payload)

result = inspect_raster(raster_path)

assert [band["index"] for band in result["bands"]] == [2, 1]
assert [band["data_type"] for band in result["bands"]] == [
    "Float32",
    "UInt16",
]
assert [band["nodata"] for band in result["bands"]] == [-9999.0, 0]
~~~

测试主动反转上游波段顺序，证明工具不会自行排序或让 index、类型、NoData 错位。

### 4.6 可选字段测试锁定 null 和 warning

~~~python
payload.pop("coordinateSystem")
payload.pop("geoTransform")
payload.pop("cornerCoordinates")
del payload["bands"][0]["noDataValue"]
del payload["bands"][0]["colorInterpretation"]

result = inspect_raster(raster_path)

assert result["crs_wkt"] is None
assert result["geotransform"] is None
assert result["pixel_size"] is None
assert result["corners"] is None
assert result["bands"][0]["nodata"] is None
assert result["bands"][0]["color_interpretation"] is None
~~~

这个测试证明信息不完整的栅格仍能成功返回必要元数据，而且缺失值不会静默消失。

~~~python
assert result["warnings"] == [
    "Band 1 color interpretation is unavailable.",
    "Band 1 NoData value is unavailable.",
    "CRS is unavailable.",
    "Geotransform and pixel size are unavailable.",
    "Corner coordinates are unavailable.",
]
~~~

warning 列表同时提供机器可访问的结果状态和人可读的缺口说明。它不是 stderr，也不会改变成功退出码。

### 4.7 旋转测试证明 pixel_size 不是简单取绝对值

~~~python
payload["geoTransform"] = [0, 3, 4, 0, 4, -3]
_mock_gdalinfo(monkeypatch, payload)

result = inspect_raster(raster_path)

assert math.isclose(result["pixel_size"]["x"], 5.0)
assert math.isclose(result["pixel_size"]["y"], 5.0)
~~~

如果实现只使用 `abs(GT(1))` 和 `abs(GT(5))`，这里会得到 3 而失败。当前断言直接保护旋转项兼容性；参数化测试还覆盖模长溢出和超大整数，二者都应降级为 `null + warning`。

### 4.8 外部进程错误不需要真实 GDAL

~~~python
def missing_command(*args: object, **kwargs: object) -> None:
    raise FileNotFoundError

monkeypatch.setattr("rs_agent.tools.subprocess.run", missing_command)

with pytest.raises(RasterInspectionError, match="not available on PATH"):
    inspect_raster(raster_path)
~~~

找不到可执行文件通过抛出 `FileNotFoundError` 模拟，证明安装边界会转换为受控错误。

~~~python
def timeout(*args: object, **kwargs: object) -> None:
    raise subprocess.TimeoutExpired(["gdalinfo"], timeout=15)

monkeypatch.setattr("rs_agent.tools.subprocess.run", timeout)

with pytest.raises(RasterInspectionError, match="timed out after 15 seconds"):
    inspect_raster(raster_path)
~~~

超时测试不需要真实等待 15 秒。monkeypatch 直接抛出相同异常，快速验证转换后的错误文本。

~~~python
_mock_gdalinfo(
    monkeypatch,
    {},
    returncode=1,
    stderr="\nERROR 4: raster cannot be opened\nverbose follow-up details\n",
)

with pytest.raises(RasterInspectionError) as error:
    inspect_raster(raster_path)

assert str(error.value) == "gdalinfo failed: ERROR 4: raster cannot be opened"
~~~

非零退出测试证明第二行冗长日志不会进入公开错误。

### 4.9 JSON 和必要字段测试分开覆盖

~~~python
return subprocess.CompletedProcess(
    command, returncode=0, stdout="not-json", stderr=""
)

with pytest.raises(RasterInspectionError, match="invalid JSON"):
    inspect_raster(raster_path)
~~~

非法 JSON 测试覆盖语法边界。

~~~python
@pytest.mark.parametrize("field", ["driverShortName", "size", "bands"])
def test_inspect_raster_rejects_missing_required_fields(...):
    payload = _gdal_info()
    payload.pop(field)
    _mock_gdalinfo(monkeypatch, payload)

    with pytest.raises(RasterInspectionError, match="invalid required field"):
        inspect_raster(raster_path)
~~~

参数化测试覆盖三类顶层必要字段。另一个测试把 `size` 改为 `["4", 3]`，验证字段存在但结构错误时同样失败。

### 4.10 CLI 测试锁定 stdout 和退出码

~~~python
monkeypatch.setattr("rs_agent.__main__.inspect_raster", lambda path: expected)

exit_code = main(["inspect-raster", "sample.tif"])

assert exit_code == 0
assert json.loads(capsys.readouterr().out) == expected
~~~

成功 CLI 测试把工具替换为确定性结果，只验证命令层是否原样输出 JSON 并返回 0。

~~~python
def fail_inspection(path: str) -> dict[str, object]:
    raise RasterInspectionError(f"Could not inspect {path}.")

monkeypatch.setattr("rs_agent.__main__.inspect_raster", fail_inspection)

exit_code = main(["inspect-raster", "bad.tif"])

assert exit_code == 1
assert json.loads(capsys.readouterr().out) == {
    "ok": False,
    "error": "Could not inspect bad.tif.",
}
~~~

失败 CLI 测试证明受控异常不会变成 traceback，并且 shell 可以通过退出码 1 判断失败。

### 4.11 完整验收命令

~~~bash
uv run pytest -q
git diff --check
uv run python -m rs_agent inspect examples/sample.ppm
uv run python -m rs_agent inspect-raster examples/does-not-exist.tif
~~~

当前实现的完整 pytest 结果为 27 passed，其中同时包含原有图片 Tool、scripted Agent、旧 CLI 和新栅格 Tool/CLI 测试。`git diff --check` 返回 0；原有 `inspect` 成功链保持退出码 0，新命令缺失路径返回紧凑错误 JSON 和退出码 1。

### 4.12 真实 GDAL 验证是可选补充

~~~bash
gdal_create -ot UInt16 -of GTiff -outsize 4 3 -bands 2 -burn 10 -burn 20 -a_srs EPSG:4326 -a_ullr 0 0 4 -3 -a_nodata 0 path/to/temp/sample.tif
uv run python -m rs_agent inspect-raster path/to/temp/sample.tif
~~~

当本机同时存在 `gdal_create` 和 `gdalinfo` 时，可以在仓库外临时目录生成极小 GeoTIFF 做端到端检查。当前实现已经用 4×3、2 波段 UInt16 栅格验证：driver 为 GTiff/GeoTIFF，尺寸、波段、CRS、六参数、像元大小和四角坐标均正确，warnings 为空。临时影像不属于测试 fixture，也不应提交到仓库。

## 5. Correct：错误和信息缺口如何被收束

### 5.1 Correct 不是自动修复栅格

~~~text
发现输入或环境问题
        ↓
转换为 RasterInspectionError
        ↓
CLI 输出 {"ok": false, "error": "..."}
        ↓
退出码 1
        ↓
调用者修正路径、安装环境或数据后重新运行
~~~

当前工具没有重试、修复文件、改写 CRS、猜测 NoData 或自动安装 GDAL。这里的 Correct 是把失败压缩成稳定、可行动的错误，并让调用者在外部修正后重新执行。

### 5.2 路径问题在最靠前的位置收束

~~~python
if not path_exists:
    raise RasterInspectionError(f"Raster path does not exist: {display_path}")
if not path_is_file:
    raise RasterInspectionError(
        f"Raster path is not a regular file: {display_path}"
    )
~~~

调用者看到路径本身和具体原因，不需要从 GDAL 的驱动错误中反推是拼写错误还是传入了目录。修正方式是提供存在的本地普通文件。

### 5.3 环境问题有独立错误语言

~~~python
raise RasterInspectionError(
    "gdalinfo is not installed or not available on PATH."
)
~~~

这条错误只说明运行前提没有满足，不会尝试下载或安装系统包。调用者需要自行配置能够执行的 `gdalinfo`，再重新运行命令。

~~~python
raise RasterInspectionError(
    f"gdalinfo timed out after {_GDALINFO_TIMEOUT_SECONDS} seconds."
)
~~~

超时与找不到命令分开表达。调用者可以检查数据集、驱动或外部存储，而不是把超时误判为路径不存在。

### 5.4 外部失败保留一条最有用的信息

~~~python
detail = _first_meaningful_line(completed.stderr)
if detail is None:
    detail = f"exit code {completed.returncode}"
raise RasterInspectionError(f"gdalinfo failed: {detail}")
~~~

例如 GDAL 返回多行日志时，CLI 只会得到：

~~~json
{
  "ok": false,
  "error": "gdalinfo failed: ERROR 4: raster cannot be opened"
}
~~~

错误仍保留驱动提供的首要原因，但不会暴露完整诊断日志或 Python traceback。

### 5.5 JSON 和必要结构错误属于硬失败

~~~python
def _invalid_required_field(field: str) -> RasterInspectionError:
    return RasterInspectionError(
        f"gdalinfo JSON has an invalid required field: {field}."
    )
~~~

退出码为 0 但 stdout 不是 JSON、根节点不是对象，或者 driver、size、bands 结构不可信时，继续返回部分结果会误导下游。因此这些情况统一终止，并指出无效的必要字段。

### 5.6 可选信息通过 warning 降级

~~~python
if crs_wkt is None:
    warnings.append("CRS is unavailable.")

if corners is None:
    warnings.append("Corner coordinates are unavailable.")
~~~

缺 CRS 或四角坐标并不否定 driver、尺寸和波段元数据。工具保留成功状态，把不可用字段设为 `null`，并用 warning 阻止调用者把 `null` 误当成已经验证的空间信息。

### 5.7 CLI 统一最后一层失败形状

~~~python
except RasterInspectionError as exc:
    result = {"ok": False, "error": str(exc)}
    exit_code = 1
~~~

无论错误来自本地路径、外部命令、JSON 解析还是必要字段，CLI 都输出同一个两字段结构。stdout 适合程序解析，退出码适合 shell 和 CI 判断。

### 5.8 错误类型与调用者动作

| 错误类别 | CLI 信息特征 | 调用者动作 |
| --- | --- | --- |
| 路径访问 OSError | `Cannot access raster path` | 检查文件系统权限或路径状态 |
| 路径不存在 | `does not exist` | 修正路径 |
| 不是普通文件 | `not a regular file` | 传入本地栅格文件 |
| 找不到 gdalinfo | `not available on PATH` | 配置系统 GDAL |
| 其他启动 OSError | `Could not run gdalinfo` | 检查可执行文件权限和系统环境 |
| 超时 | `timed out after 15 seconds` | 检查数据集、驱动或存储 |
| GDAL 非零退出 | `gdalinfo failed: ...` | 根据首条 GDAL 信息检查文件 |
| 非法 stdout | `invalid JSON` | 检查 gdalinfo 环境或版本行为 |
| JSON 根不是对象 | `JSON root must be an object` | 检查上游输出结构 |
| 必要字段错误 | `invalid required field` | 检查数据集是否提供有效栅格结构 |
| 可选字段缺失 | 成功结果中的 `warnings` | 只使用实际存在的元数据 |

这张表描述当前接口能提供的纠错信息，不意味着工具会代替调用者执行修复。

## 总结

`inspect_raster` 在五个方面的体现是：

1. Context：为遥感栅格提供独立于 Pillow 全像元统计的低成本元数据入口；
2. Constraint：固定使用 `gdalinfo -json`，不经过 shell、不请求 stats、不增加 Python 依赖，并用字段白名单控制输出；
3. Acting：依次完成本地文件检查、外部进程执行、JSON 解析、必要字段验证、可选字段降级和紧凑结果组装；
4. Verify：用 monkeypatch 覆盖外部命令主要边界，用精确断言验证映射、旋转像元大小、warning、错误和 CLI 退出码，并可用临时 GeoTIFF 做端到端补充；
5. Correct：把可预期失败统一成 `RasterInspectionError` 和退出码 1，把非致命信息缺口表示为 `null + warnings`，由调用者修正环境或输入后重新运行。

因此，新工具是一条最小、透明、可测试的栅格元数据观察链路。它让仓库开始具备真正的遥感栅格字段，但仍没有越界宣布统计、NDVI、配对验证或多时相分析已经完成。
