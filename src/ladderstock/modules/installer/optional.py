"""可选依赖清单：便携包不自带的大件，由首页弹窗引导用户一键安装（QUOT-F10/QUOT-setup）。

为什么要独立一份清单，而不是塞进 `requirements.lock`：这些包只在某个功能被用到时才
需要（PDF 解析、截图 OCR），装进主依赖会让每个用户的下载体积和安装时间都涨上去，
而且 OCR 模型（约 200MB）对不用截图查证的人是纯负担。便携包的目标是"先能跑起来，
缺什么再点一下装"。

三条边界：

- **清单只描述，不安装**。安装走 `runner` 已有的 pip 链路（同一套镜像顺序、
  同样的进度解析），不在这里另写一份 subprocess。
- **检测用 import，不用 pip freeze**。`find_spec` 能区分"装了但版本不对"之外的所有
  "装了没有"，而 `pip freeze` 在便携包里可能因为 pip 不在 PATH 而失败。
- **镜像按国内优先**：清华 → 阿里 → 腾讯 → 官方。不看 IP、不猜地区——一个国内源
  在海外通常也能连，反过来官方源在国内经常几百 KB/s。
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

#: 复用 `manifest.PYPI_MIRRORS` 的顺序（国内优先，官方兜底）
from ladderstock.modules.installer.manifest import PYPI_MIRRORS


@dataclass(frozen=True)
class OptionalDep:
    """一个可选依赖。`import_name` 用来检测，`package` 用来 pip 安装。"""

    key: str
    label: str
    package: str
    import_name: str
    version: str
    size_mb: int
    feature: str
    note: str


#: 清单本体。顺序即弹窗里的展示顺序（先装影响功能最多的）。
OPTIONAL_DEPS: tuple[OptionalDep, ...] = (
    OptionalDep(
        key="rapidocr",
        label="截图文字识别（RapidOCR）",
        package="rapidocr-onnxruntime",
        import_name="rapidocr_onnxruntime",
        version=">=1.3,<2",
        size_mb=210,
        feature="F10 截图/PDF 查证",
        note="不装则无法识别研报截图里的数字；识别在本机完成，不外发",
    ),
    OptionalDep(
        key="pymupdf",
        label="PDF 解析（PyMuPDF）",
        package="pymupdf",
        import_name="fitz",
        version=">=1.24,<2",
        size_mb=45,
        feature="F10 PDF 查证",
        note="不装则只能识别纯文本层 PDF 的图片版会被跳过",
    ),
    OptionalDep(
        key="markdown",
        label="Markdown 渲染（导出用）",
        package="markdown",
        import_name="markdown",
        version=">=3.5,<4",
        size_mb=2,
        feature="F8 研究档案导出",
        note="不装时导出仍然可用，只是预览不带 HTML 渲染",
    ),
)

BY_KEY = {item.key: item for item in OPTIONAL_DEPS}


def is_installed(item: OptionalDep) -> bool:
    """按 import 名判断。`find_spec` 抛 ValueError 的情况（模块在 sys.modules 但无 spec）按已装处理。"""
    try:
        return importlib.util.find_spec(item.import_name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


def rows() -> list[dict[str, object]]:
    """给前端的一行一条：装没装、多大、影响哪个功能。"""
    out: list[dict[str, object]] = []
    for item in OPTIONAL_DEPS:
        out.append(
            {
                "key": item.key,
                "label": item.label,
                "package": item.package,
                "version": item.version,
                "size_mb": item.size_mb,
                "feature": item.feature,
                "note": item.note,
                "installed": is_installed(item),
            }
        )
    return out


def missing_keys() -> list[str]:
    return [item.key for item in OPTIONAL_DEPS if not is_installed(item)]


def total_mb(keys: list[str] | None = None) -> int:
    picked = keys if keys is not None else [item.key for item in OPTIONAL_DEPS]
    return sum(BY_KEY[key].size_mb for key in picked if key in BY_KEY)


def install_spec(keys: list[str]) -> list[str]:
    """把这几个 key 翻成 pip 参数（`包名+版本约束`）。未知 key 直接抛，别静默跳过。"""
    out: list[str] = []
    for key in keys:
        item = BY_KEY.get(key)
        if item is None:
            raise KeyError(f"未知可选依赖：{key}")
        out.append(f"{item.package}{item.version}")
    return out


__all__ = [
    "BY_KEY",
    "OPTIONAL_DEPS",
    "PYPI_MIRRORS",
    "OptionalDep",
    "install_spec",
    "is_installed",
    "missing_keys",
    "rows",
    "total_mb",
]
