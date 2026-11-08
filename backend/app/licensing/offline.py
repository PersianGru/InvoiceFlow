"""Offline activation helpers (spec §9): the request code and the license file."""

from __future__ import annotations

import base64
import io
import json
import re
from typing import Any

import qrcode
import qrcode.image.svg

from app.licensing.verifier import b64url_encode

_BEGIN = "-----BEGIN 21CEL LICENSE-----"
_END = "-----END 21CEL LICENSE-----"
_JWS_CHARS = re.compile(r"^[A-Za-z0-9_.-]+$")


def make_request_code(
    *,
    product: str,
    key: str,
    instance_id: str,
    fp: str,
    app_version: str,
    now: int,
    name: str = "",
) -> str:
    """``base64url(UTF-8 JSON)`` of the request object; ``f`` is required for sealed ids."""
    obj: dict[str, Any] = {
        "v": 1,
        "p": product,
        "k": key,
        "i": instance_id,
        "n": name[:120],
        "d": "",
        "pl": "server",
        "av": app_version,
        "f": fp,
        "t": now,
    }
    return b64url_encode(json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def decode_request_code(code: str) -> dict[str, Any]:
    raw = base64.urlsafe_b64decode(code + "=" * (-len(code) % 4))
    obj = json.loads(raw.decode("utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("request code is not an object")
    return obj


def request_code_qr_svg(code: str) -> str:
    img = qrcode.make(code, image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


class LicenseFileError(ValueError):
    pass


def parse_license_file(text: str) -> str:
    """Ignore the header lines, join the base64 lines into the JWS (spec §9 step 4)."""
    if not isinstance(text, str) or len(text) > 64 * 1024:
        raise LicenseFileError("this is not a 21CEL license file")
    body = text
    if _BEGIN in text:
        start = text.index(_BEGIN) + len(_BEGIN)
        end = text.find(_END, start)
        body = text[start : end if end >= 0 else None]
    lines = [ln.strip() for ln in body.replace("\r", "").split("\n")]
    # header lines are "Name: value"; a JWS never contains spaces or colons
    token_lines = [ln for ln in lines if ln and " " not in ln and ":" not in ln]
    jws = "".join(token_lines)
    if jws.count(".") != 2 or not _JWS_CHARS.match(jws):
        raise LicenseFileError("the license file does not contain a license token")
    return jws
