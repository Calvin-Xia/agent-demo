# 最小单步图像检查 Agent / Tool 完整拆解

本文按 Context、Constraint、Acting、Verify、Correct 五个部分，拆解仓库中最小图像检查 Demo 的完整执行链。范围包括：

- 项目与依赖配置；
- inspect_image 工具；
- scripted single-step Agent；
- python -m rs_agent 命令行入口；
- 示例图片；
- 工具和 Agent 测试；
- 实际验证与受控失败方式。

这不是 LLM Agent。它没有模型调用、提示词、自然语言路由、循环规划或自动重试。这里的 “Agent” 是一个透明的固定策略执行器，用来演示：

~~~text
task → decision → action → observation → final
~~~

完整调用关系如下：

~~~text
python -m rs_agent inspect IMAGE_PATH
    └─ __main__.main()
        └─ run_image_inspection_agent(task, image_path)
            └─ inspect_image(image_path)
                └─ Pillow 打开并读取图片
~~~

## 1. Context：这条链路为什么存在

### 1.1 项目当前要解决的问题

仓库的长期方向是研究多时相遥感场景中的 Agent 工具调用和任务分解，但本次只建立第一条最小纵向链路。入口命令是：

~~~bash
uv run python -m rs_agent inspect examples/sample.ppm
~~~

这条命令的目标不是完成遥感分析，而是证明一项任务可以被固定策略转换为一次工具调用，并留下完整、可测试、可序列化的执行轨迹。

### 1.2 运行环境和依赖边界

~~~toml
[project]
name = "multitemporal-rs-agent"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pillow>=12.1.0,<13",
]

[dependency-groups]
dev = [
    "pytest>=9.0.0,<10",
]
~~~

运行时只有 Pillow，用于识别图片、读取像素和计算通道统计；测试环境只有 pytest。argparse、json、pathlib 等能力全部来自 Python 标准库，因此没有引入 Typer、Click、Pydantic 或 Agent 框架。

~~~toml
[build-system]
requires = ["uv_build>=0.12.2,<0.13.0"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "rs_agent"
~~~

发行项目名是 multitemporal-rs-agent，而实际 Python 包位于 src/rs_agent。module-name 明确告诉 uv_build 应打包 rs_agent，否则构建后端会按发行名寻找 src/multitemporal_rs_agent。

~~~toml
[tool.pytest.ini_options]
testpaths = ["tests"]
~~~

pytest 只从 tests 目录收集测试，避免把示例文件或包代码误识别成测试模块。

### 1.3 示例输入

~~~text
P3
# A 2x2 RGB image: red, green, blue, and white.
2 2
255
255 0 0   0 255 0
0 0 255   255 255 255
~~~

examples/sample.ppm 是一个可直接阅读的 ASCII PPM 文件。四个像素依次为红、绿、蓝、白，因此每个 RGB 通道的最小值为 0、最大值为 255、平均值为 127.5。它足够小，也不需要额外生成脚本。

### 1.4 文件职责

- src/rs_agent/tools.py：定义工具契约、输入检查、图片读取和统计计算。
- src/rs_agent/agent.py：定义固定策略、五阶段轨迹和成功/失败收束。
- src/rs_agent/__main__.py：解析命令行、调用 Agent、输出 JSON 和设置退出码。
- tests/test_tools.py：验证工具元数据、统计值、各类非法输入和异常收束。
- tests/test_agent.py：验证阶段顺序、真实工具调用和受控失败。
- tests/test_main.py：验证 CLI 退出码契约和 JSON 输出结构。
- examples/sample.ppm：提供 fresh clone 后即可使用的确定性输入。
- pyproject.toml 与 uv.lock：定义并锁定可复现环境。

## 2. Constraint：代码必须遵守什么边界

### 2.1 Agent 的决策是固定的

~~~python
{
    "stage": "decision",
    "policy": "scripted",
    "tool": "inspect_image",
    "reason": "The scripted policy always selects inspect_image.",
}
~~~

decision 阶段明确写出 policy 为 scripted，而且工具名固定为 inspect_image。它不会根据 task 内容选择工具，也不会把 scripted policy 包装或描述成 LLM。

~~~python
def run_image_inspection_agent(
    task: str, image_path: str | Path
) -> dict[str, object]:
~~~

task 和 image_path 分开传入。Agent 不从自然语言中猜测路径，也不做意图识别；调用者必须明确提供图片路径。

### 2.2 工具只有一个公开入口

~~~python
class ImageInspectionError(Exception):
    """Raised when an image cannot be inspected safely."""


def inspect_image(path: str | Path) -> dict[str, object]:
    """Return JSON-serializable metadata and per-band statistics for an image."""
~~~

工具层只有 inspect_image 和一个自有异常，没有 BaseTool、注册中心、插件发现器或抽象工厂。返回类型限制为字典，所有字段都可以被 json.dumps 序列化。

### 2.3 输入检查只覆盖工具正常职责

~~~python
display_path = str(path)
image_path = Path(path)

try:
    path_exists = image_path.exists()
    path_is_file = image_path.is_file()
except OSError as exc:
    raise ImageInspectionError(f"Cannot access image path: {display_path}") from exc

if not path_exists:
    raise ImageInspectionError(f"Image path does not exist: {display_path}")
if not path_is_file:
    raise ImageInspectionError(f"Image path is not a regular file: {display_path}")
~~~

这段代码只检查路径可访问、路径存在且是普通文件。它没有加入路径白名单、沙箱、权限框架或完整性哈希。display_path 保留调用者传入的可读表达，例如 examples/sample.ppm，不会强制转换为机器相关的绝对路径。

### 2.4 只捕获预期的图片读取错误

~~~python
except UnidentifiedImageError as exc:
    raise ImageInspectionError(f"File is not a readable image: {display_path}") from exc
except DecompressionBombError as exc:
    raise ImageInspectionError(f"Image exceeds safety limits: {display_path}") from exc
except OSError as exc:
    raise ImageInspectionError(f"Could not read image data: {display_path}") from exc
~~~

UnidentifiedImageError 对应无法识别的文件，OSError 对应读取失败或解码损坏，DecompressionBombError 对应图片像素超过 Pillow 内置的安全上限。这三种都是工具可以合理预期的失败，因此都被收束为 ImageInspectionError。代码没有捕获宽泛的 Exception，因此编程错误不会被误装成普通业务失败。

### 2.5 返回结构必须稳定、透明

~~~python
return {
    "ok": True,
    "path": display_path,
    "format": image_format,
    "width": width,
    "height": height,
    "mode": mode,
    "bands": bands,
    "statistics": statistics,
}
~~~

工具返回值明确包含成功标志、原始路径表达、格式、尺寸、模式、通道和统计信息。没有返回 Pillow 对象、Path 对象或异常对象，因而可以直接进入 Agent observation 和最终 JSON。

### 2.6 明确不在本次范围内的能力

代码和依赖共同约束了以下边界：

- 不调用 OpenAI、DeepSeek 或其他模型服务；
- 不实现提示词、自然语言路由或 MockModel；
- 不依赖 Rasterio、GDAL，也不读取 CRS、GeoTIFF 地理范围；
- 不实现 NDVI、变化检测或多时相分析；
- 不实现 MCP、向量库、工具检索或多 Agent；
- 不循环规划、不重试、不自我反思；
- 不增加 baseline、文件哈希、冻结机制或备份副本。

## 3. Acting：一条命令如何实际执行

### 3.1 CLI 只暴露 inspect 子命令

~~~python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m rs_agent")
    subparsers = parser.add_subparsers(dest="command", required=True)
    inspect_parser = subparsers.add_parser(
        "inspect", help="inspect one image with the scripted agent"
    )
    inspect_parser.add_argument("image_path", help="path to an image file")
    return parser
~~~

argparse 要求用户选择子命令并提供一个 image_path。没有隐藏配置、环境变量或交互式输入，因此命令行为容易复现。

### 3.2 CLI 构造任务，但不绕过 Agent

~~~python
args = build_parser().parse_args(argv)

if args.command == "inspect":
    task = f"Inspect image metadata and per-band statistics for {args.image_path}."
    result = run_image_inspection_agent(task, args.image_path)
~~~

CLI 根据明确路径构造一条简短任务说明，然后调用 run_image_inspection_agent。它没有直接调用 inspect_image，因此五阶段轨迹始终由 Agent 统一生成。

### 3.3 Agent 建立前三个阶段

~~~python
path = str(image_path)
trace: list[dict[str, object]] = [
    {"stage": "task", "task": task, "image_path": path},
    {
        "stage": "decision",
        "policy": "scripted",
        "tool": "inspect_image",
        "reason": "The scripted policy always selects inspect_image.",
    },
    {
        "stage": "action",
        "tool": "inspect_image",
        "arguments": {"path": path},
    },
]
~~~

task 保存输入；decision 公开固定策略和工具选择；action 记录实际调用参数。三者在工具执行前建立，因此即使工具失败，也可以看到失败发生前 Agent 已经做了什么。

### 3.4 Agent 执行唯一工具

~~~python
try:
    result = inspect_image(image_path)
~~~

这里是 action 真正发生的位置。没有工具注册表、动态查找或异步调度，Python 函数调用就是全部执行机制。

### 3.5 工具强制读取像素

~~~python
with Image.open(image_path) as image:
    image.load()
    image_format = image.format
    width, height = image.size
    mode = image.mode
    bands = list(image.getbands())
    statistics = _calculate_statistics(image, bands)
~~~

Image.open 可能延迟读取像素，因此显式调用 image.load，确保损坏数据在工具执行期间暴露。上下文管理器保证图片文件被关闭。随后读取格式、尺寸、模式和通道，并把统计计算交给单独的小函数。

### 3.6 每个通道独立计算统计值

~~~python
_HIGH_BIT_DEPTH_MODES = {"I", "I;16", "I;16L", "I;16B", "F"}


def _calculate_statistics(
    image: Image.Image, bands: list[str]
) -> dict[str, dict[str, int | float]]:
    statistics: dict[str, dict[str, int | float]] = {}

    for band_name, band_image in zip(bands, image.split(), strict=True):
        minimum, maximum = band_image.getextrema()
        statistics[band_name] = {
            "min": minimum,
            "max": maximum,
            "mean": round(_band_mean(band_image), 4),
        }

    return statistics


def _band_mean(band_image: Image.Image) -> int | float:
    if band_image.mode in _HIGH_BIT_DEPTH_MODES:
        width, height = band_image.size
        return sum(band_image.getdata()) / (width * height)
    return ImageStat.Stat(band_image).mean[0]
~~~

image.split 将图片拆成单通道图片，getextrema 读取最小值和最大值，均值由 `_band_mean` 计算。zip 的 strict=True 保证通道名数量与拆分后的通道数量一致。平均值最多保留四位小数，使 CLI 输出和测试结果稳定。

均值走两条路径：8-bit 波段（`L` 以及 RGB 拆分后的各通道）继续用 ImageStat.Stat，因为它对 256 bin 直方图是精确的；`I`、`I;16`（含 `I;16L`/`I;16B`）和 `F` 等高位深模式改用 `sum(getdata()) / 像素数`，因为 ImageStat 在这些模式下只用 256 bin 直方图反推均值，会把 16-bit 数据静默压错（例如 `[0, 65535]` 会被算成接近 0）。这两种遥感常用位深必须从原始像素求和。

### 3.7 成功结果进入 observation

~~~python
trace.append(
    {
        "stage": "observation",
        "tool": "inspect_image",
        "ok": True,
        "result": result,
    }
)
final_answer = {
    "ok": True,
    "message": "Image inspection completed.",
    "image": result,
}
status = "completed"
~~~

工具结果完整进入 observation，没有被转换成不透明文本。final_answer 同时提供简短消息和结构化图片结果，顶层状态设置为 completed。

### 3.8 final 阶段和顶层返回值

~~~python
trace.append(
    {
        "stage": "final",
        "status": status,
        "answer": final_answer,
    }
)
return {"status": status, "trace": trace, "final_answer": final_answer}
~~~

无论成功还是失败，final 都是轨迹中的第五个阶段。顶层 status 方便调用者判断结果，trace 用于完整观察执行过程，final_answer 方便只关心最终输出的消费者使用。

### 3.9 CLI 输出 JSON 并映射退出码

~~~python
print(json.dumps(result, ensure_ascii=False, indent=2))
return 0 if result["status"] == "completed" else 1
~~~

ensure_ascii=False 保留可读的非 ASCII 文本，indent=2 便于人工检查。completed 映射为退出码 0，failed 映射为退出码 1，因此脚本和 CI 可以使用标准进程语义判断结果。

~~~python
if __name__ == "__main__":
    raise SystemExit(main())
~~~

python -m rs_agent 最终通过 SystemExit 把 main 的返回值传递给操作系统。

## 4. Verify：如何证明行为正确

### 4.1 工具成功测试使用已知像素

~~~python
image_path = tmp_path / "known.png"
with Image.new("RGB", (2, 2)) as image:
    image.putdata(
        [
            (0, 10, 20),
            (255, 20, 0),
            (0, 30, 10),
            (255, 40, 30),
        ]
    )
    image.save(image_path)

result = inspect_image(image_path)
~~~

测试在 pytest 提供的 tmp_path 中即时创建 2×2 PNG，不访问网络，也不依赖开发者机器上的图片。

~~~python
assert result == {
    "ok": True,
    "path": str(image_path),
    "format": "PNG",
    "width": 2,
    "height": 2,
    "mode": "RGB",
    "bands": ["R", "G", "B"],
    "statistics": {
        "R": {"min": 0, "max": 255, "mean": 127.5},
        "G": {"min": 10, "max": 40, "mean": 25.0},
        "B": {"min": 0, "max": 30, "mean": 15.0},
    },
}
~~~

这个断言同时验证公开契约中的所有必要字段和真实统计值。测试路径来自本次调用本身，没有绑定固定的机器目录。

### 4.2 工具失败测试覆盖三类输入

~~~python
with pytest.raises(ImageInspectionError, match="does not exist"):
    inspect_image(missing_path)

with pytest.raises(ImageInspectionError, match="not a regular file"):
    inspect_image(tmp_path)

with pytest.raises(ImageInspectionError, match="not a readable image"):
    inspect_image(text_path)
~~~

三个断言分别验证路径不存在、目录不是普通文件、普通文本不是图片。它们还证明底层 Pillow 错误已经被转换为统一的 ImageInspectionError。

### 4.3 Agent 测试锁定五阶段顺序

~~~python
assert [item["stage"] for item in result["trace"]] == [
    "task",
    "decision",
    "action",
    "observation",
    "final",
]
~~~

这是 Agent 的核心公开行为。测试只检查阶段顺序，不把每个说明文字都锁死，因此仍允许以后在不破坏契约的前提下改善文案。

### 4.4 Agent 成功测试证明调用了 inspect_image

~~~python
assert result["status"] == "completed"
assert result["trace"][2] == {
    "stage": "action",
    "tool": "inspect_image",
    "arguments": {"path": str(image_path)},
}
observation = result["trace"][3]
assert observation["tool"] == "inspect_image"
assert observation["ok"] is True
assert observation["result"]["format"] == "PNG"
~~~

测试使用真实临时 PNG，并检查 action 中的工具名和参数，以及 observation 中由 Pillow 得到的 PNG 格式。它验证的不是伪造轨迹，而是完整工具链的公开结果。

### 4.5 Agent 失败测试证明受控退出

~~~python
result = run_image_inspection_agent("Inspect the supplied image.", missing_path)

assert result["status"] == "failed"
observation = result["trace"][3]
assert observation["stage"] == "observation"
assert observation["ok"] is False
assert "does not exist" in observation["error"]
assert result["trace"][4]["status"] == "failed"
~~~

函数没有向测试抛出 ImageInspectionError，而是把错误保存到 observation，并正常生成 failed final。这正是 “错误被观察并受控退出” 的契约。

### 4.6 实际执行的验收

~~~bash
uv lock
uv sync --locked
uv run python -m rs_agent inspect examples/sample.ppm
uv run pytest -q
git diff --check
git status --short
uv run python -m rs_agent inspect examples/does-not-exist.png
~~~

本次实际结果为（首版 MVP 合并时记录的验收快照）：

- uv lock 成功解析 8 个包；
- uv sync --locked 成功构建并安装项目；
- 成功 CLI 返回退出码 0，轨迹严格包含五个阶段；
- sample.ppm 被识别为 2×2 RGB PPM，三个通道统计均为 0、255、127.5；
- pytest 全部通过；
- git diff --check 返回 0；
- 失败 CLI 返回退出码 1，输出结构化 failed 结果且没有 traceback。

后续若新增测试或工具，pytest 用例总数会随之变化；以当前提交运行 `uv run pytest -q` 拿到的数字为准。

## 5. Correct：失败如何被收束，问题如何被修正

### 5.1 Tool 层先统一错误语言

~~~python
if not path_exists:
    raise ImageInspectionError(f"Image path does not exist: {display_path}")
if not path_is_file:
    raise ImageInspectionError(f"Image path is not a regular file: {display_path}")
~~~

路径错误在调用 Pillow 前被识别，用户看到的是简短、带路径上下文的工具错误。

~~~python
except UnidentifiedImageError as exc:
    raise ImageInspectionError(f"File is not a readable image: {display_path}") from exc
except DecompressionBombError as exc:
    raise ImageInspectionError(f"Image exceeds safety limits: {display_path}") from exc
except OSError as exc:
    raise ImageInspectionError(f"Could not read image data: {display_path}") from exc
~~~

无法识别、解压炸弹和读取失败都被转换成相同的 ImageInspectionError，因此 Agent 层只用一个 except 即可收束。from exc 保留了开发调试时的异常链，但 Agent 面向终端输出时只使用自定义错误消息。

### 5.2 Agent 层把异常转换为 observation

~~~python
except ImageInspectionError as exc:
    trace.append(
        {
            "stage": "observation",
            "tool": "inspect_image",
            "ok": False,
            "error": str(exc),
        }
    )
    final_answer: dict[str, object] = {
        "ok": False,
        "message": f"Image inspection failed: {exc}",
    }
    status = "failed"
~~~

Agent 只捕获约定的 ImageInspectionError，并将其转换为 ok: false observation。然后生成 failed final_answer，不重试，也不把 traceback 输出给 CLI 用户。

这里的 Correct 不是 “Agent 自动修复输入”。当前 Demo 只做错误归一化和受控退出。用户修正路径或文件后，需要重新执行命令。

### 5.3 CLI 用非零退出码结束失败链路

~~~python
return 0 if result["status"] == "completed" else 1
~~~

结构化 JSON 负责解释错误，退出码 1 负责通知 shell 调用失败。两者同时存在，使失败既适合人阅读，也适合脚本判断。

## 总结

这个 MVP 的价值不在能力复杂度，而在边界清楚：

1. Context 给出明确任务和图片路径；
2. Constraint 把策略固定为一次 inspect_image 调用；
3. Acting 产生可观察的五阶段轨迹；
4. Verify 用确定性图片、单元测试和真实 CLI 验证契约；
5. Correct 把已知工具错误转换为结构化失败，并要求调用者修正输入后重新运行。

因此，它是一条最小、透明、可测试的 Agent / Tool 纵向链路，而不是一个被过度包装的模型 Agent。
