"""`ldst verify`：F10 截图 / PDF 查证的命令行入口。

三个命令：

- `capabilities`：本机能做什么（PDF 文本层 / 图片 vision / 本地 OCR），先看这个再决定要不要上传；
- `file PATH`：一份 PDF 或截图 → 抽取 → 数字 → 与本地数据对账，结论落 `data/verify/<day>/`；
- `history`：最近查过哪些文件、抽出多少数字、核对正确率多少。

`--no-save` 只打印不落盘，用于试跑。OCR 不可用时命令以 2 退出（不是 0），
免得脚本把"没识别"当成功。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import typer

from ladderstock.research import verify

_MIME_BY_SUFFIX = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def _echo_json(payload: Any) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, indent=1, default=str))


def _print_result(payload: dict[str, Any]) -> None:
    extract = payload["extract"]
    typer.echo(f"{payload['filename']}（{payload['bytes']} 字节，{payload['mime']}）")
    typer.echo(f"  抽取：{extract['status']}" + (f" · {extract['reason']}" if extract.get("reason") else ""))
    if extract.get("pages"):
        typer.echo(f"  页数：{extract['pages']} · 字数：{extract.get('chars')}")
    if payload.get("dates"):
        typer.echo("  图上日期：" + "、".join(payload["dates"][:5]))
    if not payload["figures"]:
        typer.echo("  没抽到关键数字（图上可能没有这些字段，或需要换更清晰的图）")
    for figure in payload["figures"]:
        typer.echo(f"  - {figure['label']} = {figure['value']}{figure['unit']}  ← {figure['raw']}")
    reconciled = payload["reconciled"]
    typer.echo(
        f"  对账：{reconciled['matched']}/{reconciled['comparable']} 一致"
        f" · 不一致 {reconciled['mismatched']} · 无法核对 {reconciled['unverifiable']}"
        + (f" · 正确率 {reconciled['accuracy'] * 100:.1f}%" if reconciled["accuracy"] is not None else "")
    )
    for row in reconciled["rows"]:
        mark = {"match": "✓", "mismatch": "✗", "unverifiable": "?"}.get(row["verdict"], "?")
        local = row["local_value"] if row["local_value"] is not None else "—"
        typer.echo(
            f"    {mark} {row['label']}：图上 {row['doc_value']}{row.get('doc_unit') or ''}"
            f" / 本地 {local}（{row['local_source']}）"
            + (f"  注：{row['note']}" if row.get("note") else "")
        )
    if payload.get("saved"):
        typer.echo(f"  已保存：{payload['saved']}")


def verify_capabilities(
    as_json: Annotated[bool, typer.Option("--json", help="输出 JSON")] = False,
) -> None:
    """本机能做什么：PDF 文本层 / 图片 vision / 本地 OCR。"""
    info = verify.capabilities()
    if as_json:
        _echo_json(info)
        return
    typer.echo(f"PDF 文本层：{'可用（pypdf）' if info['pdf_text'] else '不可用 · ' + info['pdf_reason']}")
    typer.echo(f"图片识别：{'已配置 vision 后端' if info['image_vision'] else '不可用 · ' + info['image_reason']}")
    typer.echo(f"本地 OCR：{'可用' if info['local_ocr'] else '不可用 · ' + info['local_ocr_reason']}")
    typer.echo("对账容差：" + "、".join(f"{key}={value}" for key, value in info["tolerance"].items()))


def verify_file(
    path: Annotated[str, typer.Argument(help="PDF 或截图路径")],
    code: Annotated[str | None, typer.Option("--code", help="标的代码（图上没写时手工指定）")] = None,
    root: Annotated[str | None, typer.Option("--root", help="数据根（默认 data/）")] = None,
    no_save: Annotated[bool, typer.Option("--no-save", help="只打印，不落 data/verify/")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="输出 JSON")] = False,
) -> None:
    """查证一份 PDF / 截图：抽取 → 关键数字 → 与本地数据对账。"""
    file_path = Path(path)
    if not file_path.is_file():
        typer.echo(f"文件不存在：{file_path}")
        raise typer.Exit(code=2)
    mime = _MIME_BY_SUFFIX.get(file_path.suffix.lower(), "")
    if not mime:
        typer.echo(f"不支持的文件类型 {file_path.suffix}；只收 pdf / png / jpg / jpeg / webp / bmp")
        raise typer.Exit(code=2)
    data = file_path.read_bytes()
    payload = verify.verify(
        data,
        filename=file_path.name,
        mime=mime,
        root=Path(root) if root else None,
        code=code,
        persist=not no_save,
    )
    if as_json:
        _echo_json(payload)
    else:
        _print_result(payload)
    if not payload["ok"]:
        # 0 退出码会让脚本把"没识别"当成功；这里明确失败
        raise typer.Exit(code=2)


def verify_history(
    limit: Annotated[int, typer.Option("--limit", "-n", help="最多几条")] = 20,
    root: Annotated[str | None, typer.Option("--root", help="数据根（默认 data/）")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="输出 JSON")] = False,
) -> None:
    """最近查证过的文件（只读落盘结果）。"""
    items = verify.history(Path(root) if root else None, limit=limit)
    if as_json:
        _echo_json({"items": items, "capabilities": verify.capabilities()})
        return
    if not items:
        typer.echo("还没有查证记录（data/verify/ 为空）。")
        return
    for item in items:
        accuracy = item["accuracy"]
        typer.echo(
            f"{item['generated_at'] or '—'} · {item['filename']} · {item['status']}"
            f" · 数字 {item['figures']} 项"
            + (f" · 正确率 {accuracy * 100:.1f}%" if accuracy is not None else "")
        )


def register(verify_app: typer.Typer) -> None:
    """把三个命令挂到 `ldst verify` 子应用上。"""
    verify_app.command("capabilities", help="看本机能做什么（PDF/图片/OCR）")(verify_capabilities)
    verify_app.command("file", help="查证一份 PDF / 截图")(verify_file)
    verify_app.command("history", help="最近的查证记录")(verify_history)


__all__ = [
    "register",
    "verify_capabilities",
    "verify_file",
    "verify_history",
]
