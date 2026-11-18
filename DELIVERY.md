# uf-t109 交付说明：损坏帧导致整库导入中断的定位与修复

## 1. 问题机理：帧级损坏为何升级为文件级灾难

`FrameSet.parse`（`eyed3/id3/frames.py:1844`）在逐帧循环里只捕获 `FrameException`：

```python
try:
    frame = createFrame(tag_header, frame_header, data)
except FrameException as frame_ex:
    log.warning(f"Frame error:  {frame_ex}")   # 跳过该帧，继续下一帧
```

但各帧类的 `parse()` 在面对损坏数据时抛出的并不是 `FrameException`，而是
`UnicodeDecodeError`、`TypeError`、`zlib.error`、`IndexError` 等原生异常。
这些异常穿透 `FrameSet.parse` → `Tag._loadV2Tag`（`tag.py:130`）→ `Tag.parse`
→ `Mp3AudioFile._read` → `eyed3.load`，于是**单个损坏帧让整个文件加载失败**；
在音乐管理软件的批量导入场景下，一个文件异常又中断整库导入。健康文件不受影响，
因为它们的帧不会触发这些路径。

## 2. 崩点清单（四类损坏形态 × 解析环节 × 异常类型）

| # | 损坏形态 | 解析环节 | 位置（修复前） | 逃逸异常 |
|---|----------|----------|----------------|----------|
| 1 | 文本类帧 payload 非法编码（如 TXXX 含孤立 UTF-16 代理对） | `UserTextFrame.parse` → `decodeUnicode` | `frames.py:358/360` | `UnicodeDecodeError` |
| 2 | APIC 数据截断（缺少 picture-type 字节） | `ImageFrame.parse` → `ord(input.read(1))` | `frames.py:629` | `TypeError` |
| 3 | v2.3 压缩帧（compression 标志位置位）payload 非 zlib 数据 | `Frame._disassembleFrame` → `Frame.decompress` → `zlib.decompress` | `frames.py:182` → `frames.py:118` | `zlib.error` |
| 4 | RVAD 数据截断（不足 2 字节） | `RelVolAdjFrameV23.parse` → `data[0]`/`data[1]` | `frames.py:1577/1579` | `IndexError` |

同形态的其他未保护点（同一修复一并覆盖，不再逐一列举行号）：
`UserUrlFrame.parse`、`ImageFrame` 描述解码、`ObjectFrame`、`LyricsFrame` 等所有
未捕获的 `decodeUnicode` 调用点，以及 `splitUnicode` 对未知编码字节抛出的
`NotImplementedError`。

**影响范围**：任何含上述形态损坏帧的 MP3，`eyed3.load` 直接抛异常，文件级元数据
（包括同标签内的健康帧、MP3 音频头信息）全部丢失；批量导入任务随之中断。

## 3. 修复方案

在帧解析的唯一收口点 `createFrame`（`frames.py:1951` 附近）把**任何**帧解析失败
统一归位到库自己的帧异常族 `FrameException`（保留原异常链 `__cause__` 便于排查），
由 `FrameSet.parse` 既有的 `except FrameException` 分支按库内宽容语义**跳过该帧、
记录警告、继续解析后续帧**。改动仅 `frames.py` 一处（+14/-1 行）。

### 取舍：跳过（skip），而非降级解码（degraded decode）

- 库内对帧级错误的既有处置语义就是跳过：`FrameSet.parse` 捕获 `FrameException` 后
  告警并继续；压缩帧缺 data-length-indicator、非法帧 ID、非法日期等先例均如此。
  选择跳过与库的自愈契约一致。
- 降级解码（如 `errors="replace"`）只对文本类帧有意义，对 zlib 解压失败、结构截断
  等形态无从"降级"；且保留一个半解析的帧对象会把风险推迟到 render/save 阶段爆发。
- 损坏帧的原始字节不可信，丢弃单帧的信息损失小于保留一个状态未知的帧对象。

### 既有宽容先例：全部保留，未回退

`TextFrame.parse` 的 `UnicodeDecodeError → text=""` 降级（`frames.py:305-309`）、
`splitUnicode` 的切分失败回退、`decodeUnicode` 的 UTF-16 尾部零字节修复、
`UrlFrame`/`UserUrlFrame` 的非 ASCII URL 清空、`DateFrame` 的非法日期 `parseError`
——本次 diff 只触碰 `createFrame`，上述先例一行未动，行为不变。

## 4. 回归测试

新增 `tests/id3/test_corrupt_frames.py`，四类损坏形态 × 两个层级共 8 个用例：

- 单元层：`createFrame` 对每种损坏形态抛 `FrameException`（崩点归位帧异常族）；
- 集成层：构造"健康 TIT2 + 一个损坏帧"的完整 MP3，`eyed3.load` 成功、
  损坏帧被跳过、健康帧 `title == "Healthy"` 存活。

## 5. 测试套件状态

`pytest tests`：**189 passed, 4 failed** —— 4 个失败均为修复前就存在的既有红灯，
与帧解析无关，未因本次改动新增或变化：

- `test_classic_plugin.py::test_lyrics`、`test_classic_plugin.py::test_all`
  —— argparse 退出码 2：Windows 路径盘符冒号与 `--add-lyrics FILE:desc` 参数
  分隔符冲突（命令行参数解析问题，按任务口径不在验收范围）；
- `test_jsonyaml_plugin.py::testJsonPlugin` —— Windows 路径反斜杠的 JSON 转义
  差异（同为命令行/路径参数形态差异）；
- `test_console.py::AnsiCodesTC::test_init_color_enabled` —— Windows 下
  `OS=Windows_NT` 使 `_term_supports_color()` 恒为 False（环境相关）。
