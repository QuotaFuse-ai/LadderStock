"""出站渠道：console / desktop / email / webhook / 钉钉 / 飞书 / 企微（DEC-087）。

**这是对旧红线的一次显式推翻，不是绕开**：`watch/notifier.py` 的口径（DEC-006）
是「只落本机、不发网络请求、不写凭据」，2026-11-21 用户拍板新增出站渠道后，
边界收窄为三条不变量，全部由本模块执行：

1. **console / desktop 仍是纯本机**：委托 `watch.notifier`，一行业务都不新增；
2. **出站只发「提醒正文」，不发数据**：请求体里只有 title / body / 时间戳。body
   由调用方（`run.py`）拼装，可能含持仓盈亏百分数——这与 `web/inbox` 的简报口径
   一致（用户自己的收件箱内容投到用户自己配的渠道），**但渠道配置与密钥永远不
   出现在请求体里**；
3. **凭据只存名字不存值**：webhook URL 属于半敏感（有它就能往群里发消息），
   落库全量、**读面一律打码**（`public_config`）；SMTP 密码只存环境变量名
   （默认 `LDST_ALERT_SMTP_PASSWORD`），值从环境读取，`GET` / 日志都不回显。

失败语义沿用 `watch/notifier.DeliveryResult`：**投递失败不抛异常**，返回
`ok=False + degraded=True`，由调用方决定是告警（`alert run` 的 partial）还是
渠道测试里的人话报错。

三家 IM 机器人的出参都有 body 里的业务码（钉钉/企微 `errcode`、飞书 `code`），
**HTTP 200 不代表送达**——`errcode != 0` 一律按失败处理并回带 `errmsg`。
"""

from __future__ import annotations

import os
import re
import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

import httpx

from ladderstock.errors import EXIT_USAGE, LadderStockError
from ladderstock.watch.notifier import DeliveryResult
from ladderstock.watch.notifier import send as send_local

CHANNEL_CONSOLE = "console"
CHANNEL_DESKTOP = "desktop"
CHANNEL_EMAIL = "email"
CHANNEL_WEBHOOK = "webhook"
CHANNEL_DINGTALK = "dingtalk"
CHANNEL_FEISHU = "feishu"
CHANNEL_WECOM = "wecom"

CHANNEL_KINDS: tuple[str, ...] = (
    CHANNEL_CONSOLE,
    CHANNEL_DESKTOP,
    CHANNEL_EMAIL,
    CHANNEL_WEBHOOK,
    CHANNEL_DINGTALK,
    CHANNEL_FEISHU,
    CHANNEL_WECOM,
)

DEFAULT_SMTP_PASSWORD_ENV = "LDST_ALERT_SMTP_PASSWORD"
HTTP_TIMEOUT_SECONDS = 10.0

_CID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now().astimezone().isoformat(timespec="seconds")


def normalize_channel_id(value: str) -> str:
    """渠道 id 只当标识符用（进 URL 路径 / 列表键），所以早拒非法字符。"""
    text = str(value or "").strip().lower()
    if not _CID.match(text):
        raise LadderStockError(
            f"渠道 id 需要 a-z / 0-9 / - / _（2–32 位），收到 {value!r}", EXIT_USAGE
        )
    return text


# --------------------------------------------------------------------------- #
# 打码
# --------------------------------------------------------------------------- #


def mask_url(url: str) -> str:
    """URL 打码：保留 scheme 与 host（用户认得出来配的是哪家），路径与参数全隐。"""
    text = str(url or "")
    if not text:
        return ""
    match = re.match(r"^(https?://[^/]+)(/.*)?$", text)
    if match is None:
        return "***"
    return f"{match.group(1)}/***"


def public_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """渠道配置的**读面投影**：webhook 家族的 URL 打码；其余字段原样。"""
    out = dict(config or {})
    if kind in (CHANNEL_WEBHOOK, CHANNEL_DINGTALK, CHANNEL_FEISHU, CHANNEL_WECOM):
        if out.get("url"):
            out["url"] = mask_url(str(out["url"]))
    out.pop("password", None)  # 防御：就算有人手写进库，读面也不出
    return out


# --------------------------------------------------------------------------- #
# 配置校验
# --------------------------------------------------------------------------- #


def _need(config: dict[str, Any], key: str) -> Any:
    value = config.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise LadderStockError(f"渠道配置缺字段：{key}", EXIT_USAGE)
    return value


def validate_channel_config(kind: str, config: dict[str, Any]) -> dict[str, Any]:
    """按类型校验配置并归一；非法即 exit 2。密钥值即使传进来也被剥掉。"""
    clean: dict[str, Any] = {}
    if kind in (CHANNEL_WEBHOOK, CHANNEL_DINGTALK, CHANNEL_FEISHU, CHANNEL_WECOM):
        url = str(_need(config, "url")).strip()
        if not url.startswith(("http://", "https://")):
            raise LadderStockError(
                f"{kind} 的 url 必须以 http(s):// 开头，收到 {url!r}", EXIT_USAGE
            )
        clean["url"] = url
    elif kind == CHANNEL_EMAIL:
        clean["smtp_host"] = str(_need(config, "smtp_host")).strip()
        try:
            port = int(config.get("smtp_port") or 465)
        except (TypeError, ValueError):
            raise LadderStockError(
                f"smtp_port 需要整数，收到 {config.get('smtp_port')!r}"
            ) from None
        if not 1 <= port <= 65535:
            raise LadderStockError(f"smtp_port 超范围：{port}", EXIT_USAGE)
        clean["smtp_port"] = port
        clean["from_addr"] = str(_need(config, "from_addr")).strip()
        to = config.get("to_addrs") or []
        if isinstance(to, str):
            to = [to]
        to = [str(item).strip() for item in to if str(item).strip()]
        if not to:
            raise LadderStockError("email 渠道至少要一个收件人（to_addrs）", EXIT_USAGE)
        clean["to_addrs"] = to
        clean["use_starttls"] = bool(config.get("use_starttls", port == 587))
        clean["user"] = str(config.get("user") or "").strip()
        clean["password_env"] = str(config.get("password_env") or DEFAULT_SMTP_PASSWORD_ENV).strip()
    elif kind in (CHANNEL_CONSOLE, CHANNEL_DESKTOP):
        clean = {}  # 本机渠道没有配置面
    else:
        raise LadderStockError(f"未知渠道类型 {kind!r}", EXIT_USAGE)
    return clean


# --------------------------------------------------------------------------- #
# 出站实现
# --------------------------------------------------------------------------- #

Poster = Callable[[str, dict[str, Any]], tuple[int, dict[str, Any]]]
SmtpSend = Callable[[str, int, EmailMessage, str | None, str | None], None]


def _default_poster(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    """默认出站通道：httpx POST JSON。返回 (HTTP 状态码, 响应 JSON body)。"""
    with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as client:
        response = client.post(url, json=payload)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


def _default_smtp_send(
    host: str,
    port: int,
    message: EmailMessage,
    user: str | None,
    password: str | None,
) -> None:
    use_starttls = bool(message.get("X-LDST-StartTLS", "").strip() == "1")
    del message["X-LDST-StartTLS"]
    if use_starttls or port == 587:
        with smtplib.SMTP(host, port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
            smtp.ehlo()
            try:
                smtp.starttls()
            except smtplib.SMTPNotSupportedError:
                pass  # 服务器不支持 TLS：按明文发，结果如实回给调用方
            if user and password:
                smtp.login(user, password)
            smtp.send_message(message)
        return
    with smtplib.SMTP_SSL(host, port, timeout=HTTP_TIMEOUT_SECONDS) as smtp:
        if user and password:
            smtp.login(user, password)
        smtp.send_message(message)


def _plain_ok_title(kind: str) -> str:
    return {
        CHANNEL_WEBHOOK: "已 POST 到通用 webhook",
        CHANNEL_DINGTALK: "钉钉机器人已收",
        CHANNEL_FEISHU: "飞书机器人已收",
        CHANNEL_WECOM: "企业微信群机器人已收",
    }[kind]


def _check_im_body(kind: str, body: dict[str, Any]) -> str:
    """三家 IM 的业务码校验；HTTP 200 只是「服务器理你了」，不是「送达了」。"""
    code = body.get("errcode", body.get("code"))
    if code is None or int(code) == 0:
        return _plain_ok_title(kind)
    message = str(body.get("errmsg") or body.get("msg") or body)[:200]
    return f"机器人拒绝（code={code}）：{message}"


def _send_http_channel(
    channel_id: str,
    kind: str,
    url: str,
    title: str,
    body: str,
    *,
    poster: Poster | None,
) -> DeliveryResult:
    stamp = _now_iso()
    post = poster or _default_poster
    payload: dict[str, Any]
    if kind == CHANNEL_WEBHOOK:
        payload = {"title": title, "body": body, "sent_at": stamp, "source": "ladderstock"}
    else:
        text = f"{title}\n{body}".strip()
        if kind == CHANNEL_FEISHU:
            payload = {"msg_type": "text", "content": {"text": text}}
        else:  # 钉钉 / 企微同构
            payload = {"msgtype": "text", "text": {"content": text}}
    try:
        status, response_body = post(url, payload)
    except Exception as exc:  # noqa: BLE001 - 网络失败属于投递降级，不是程序错误
        return DeliveryResult(
            channel=channel_id, ok=False, detail=f"请求失败：{exc}", degraded=True, sent_at=stamp
        )
    if status != 200:
        return DeliveryResult(
            channel=channel_id,
            ok=False,
            detail=f"HTTP {status}；URL 是否配对/过期",
            degraded=True,
            sent_at=stamp,
        )
    if kind == CHANNEL_WEBHOOK:
        return DeliveryResult(
            channel=channel_id, ok=True, detail=_plain_ok_title(kind), sent_at=stamp
        )
    detail = _check_im_body(kind, response_body)
    ok = not detail.startswith("机器人拒绝")
    return DeliveryResult(channel=channel_id, ok=ok, detail=detail, degraded=not ok, sent_at=stamp)


def _send_email(
    channel_id: str,
    config: dict[str, Any],
    title: str,
    body: str,
    *,
    smtp_send: SmtpSend | None,
) -> DeliveryResult:
    stamp = _now_iso()
    password_env = str(config.get("password_env") or DEFAULT_SMTP_PASSWORD_ENV)
    password = os.environ.get(password_env, "")
    user = str(config.get("user") or "")
    if user and not password:
        return DeliveryResult(
            channel=channel_id,
            ok=False,
            detail=f"SMTP 密码未配置：请设 {password_env}=（值只进环境变量）",
            degraded=True,
            sent_at=stamp,
        )
    message = EmailMessage()
    message["Subject"] = title
    message["From"] = str(config.get("from_addr") or "")
    message["To"] = ", ".join(str(item) for item in config.get("to_addrs") or [])
    message.set_content(body or " ")
    if config.get("use_starttls"):
        message["X-LDST-StartTLS"] = "1"
    host = str(config.get("smtp_host") or "")
    port = int(config.get("smtp_port") or 465)
    try:
        (smtp_send or _default_smtp_send)(host, port, message, user or None, password or None)
    except Exception as exc:  # noqa: BLE001
        return DeliveryResult(
            channel=channel_id,
            ok=False,
            detail=f"SMTP 发送失败：{exc}",
            degraded=True,
            sent_at=stamp,
        )
    return DeliveryResult(channel=channel_id, ok=True, detail="邮件已发出", sent_at=stamp)


def deliver(
    channel: dict[str, Any],
    title: str,
    body: str,
    *,
    poster: Poster | None = None,
    smtp_send: SmtpSend | None = None,
) -> DeliveryResult:
    """向一个渠道记录投递一条消息；失败不抛（口径同 `watch.notifier.send`）。

    `channel` 是 `store` 的一行（id / kind / config）。渠道停用由调用方过滤，
    这里不做二次判断——「给停用渠道发了消息」属于调用方 bug，测试里钉死。
    """
    channel_id = str(channel.get("id") or channel.get("kind") or "channel")
    kind = str(channel.get("kind") or "")
    config = channel.get("config") or {}
    if kind in (CHANNEL_CONSOLE, CHANNEL_DESKTOP):
        result = send_local(
            CHANNEL_CONSOLE if kind == CHANNEL_CONSOLE else CHANNEL_DESKTOP, title, body
        )
        return DeliveryResult(
            channel=channel_id,
            ok=result.ok,
            detail=result.detail,
            degraded=result.degraded,
            sent_at=result.sent_at,
        )
    if kind == CHANNEL_EMAIL:
        return _send_email(channel_id, config, title, body, smtp_send=smtp_send)
    if kind in (CHANNEL_WEBHOOK, CHANNEL_DINGTALK, CHANNEL_FEISHU, CHANNEL_WECOM):
        url = str(config.get("url") or "")
        if not url:
            return DeliveryResult(
                channel=channel_id,
                ok=False,
                detail="渠道没配 url",
                degraded=True,
                sent_at=_now_iso(),
            )
        return _send_http_channel(channel_id, kind, url, title, body, poster=poster)
    return DeliveryResult(
        channel=channel_id,
        ok=False,
        detail=f"未知渠道类型 {kind!r}",
        degraded=True,
        sent_at=_now_iso(),
    )


__all__ = [
    "CHANNEL_KINDS",
    "DEFAULT_SMTP_PASSWORD_ENV",
    "deliver",
    "mask_url",
    "normalize_channel_id",
    "public_config",
    "validate_channel_config",
]
