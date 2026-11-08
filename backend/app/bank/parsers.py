"""Bank statement parsers: ISO 20022 CAMT.053 (any 001.0x minor version) and SWIFT MT940.

Both produce the same flat shape; amounts are signed from the account holder's point of view
(debit < 0, credit > 0). Parsers never raise on missing optional data; a file that is neither
format raises ``StatementFormatError``.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
from xml.etree.ElementTree import Element  # noqa: S405 - parsed with defusedxml

from defusedxml.ElementTree import fromstring as safe_fromstring


class StatementFormatError(ValueError):
    pass


@dataclass(slots=True)
class ParsedTransaction:
    external_id: str
    booking_date: date
    amount: Decimal
    currency: str
    value_date: date | None = None
    counterparty_name: str | None = None
    counterparty_iban: str | None = None
    remittance_info: str | None = None
    end_to_end_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedStatement:
    format: str  # camt053 | mt940
    iban: str
    currency: str
    bic: str | None = None
    account_name: str | None = None
    from_date: date | None = None
    to_date: date | None = None
    opening_balance: Decimal | None = None
    closing_balance: Decimal | None = None
    transactions: list[ParsedTransaction] = field(default_factory=list)


# ---------------------------------------------------------------------------------------- helpers
def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(el: Element | None, *path: str) -> Element | None:
    cur = el
    for name in path:
        if cur is None:
            return None
        cur = next((c for c in cur if _local(c.tag) == name), None)
    return cur


def _children(el: Element | None, name: str) -> list[Element]:
    return [] if el is None else [c for c in el if _local(c.tag) == name]


def _text(el: Element | None, *path: str) -> str | None:
    found = _child(el, *path)
    if found is None or found.text is None:
        return None
    value = found.text.strip()
    return value or None


def _dec(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(value.replace(",", ".").strip()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def _iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _hash_id(*parts: object) -> str:
    return "h:" + hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:32]


def normalise_iban(value: str | None) -> str | None:
    if not value:
        return None
    v = "".join(ch for ch in value if ch.isalnum()).upper()
    return v or None


# ---------------------------------------------------------------------------------------- CAMT.053
def parse_camt053(data: bytes) -> list[ParsedStatement]:
    try:
        root = safe_fromstring(data)
    except Exception as e:  # noqa: BLE001 - any XML failure is a format error
        raise StatementFormatError("not well-formed XML") from e
    if _local(root.tag) != "Document" or "camt.053" not in (
        root.tag.split("}")[0] if "}" in root.tag else ""
    ):
        if _child(root, "BkToCstmrStmt") is None:
            raise StatementFormatError("not a camt.053 document")
    statements: list[ParsedStatement] = []
    for stmt in _children(_child(root, "BkToCstmrStmt"), "Stmt"):
        acct = _child(stmt, "Acct")
        iban = normalise_iban(_text(acct, "Id", "IBAN")) or (_text(acct, "Id", "Othr", "Id") or "")
        st = ParsedStatement(
            format="camt053",
            iban=iban,
            currency=(_text(acct, "Ccy") or "EUR").upper(),
            bic=_text(acct, "Svcr", "FinInstnId", "BICFI") or _text(acct, "Svcr", "FinInstnId", "BIC"),
            account_name=_text(acct, "Nm") or _text(acct, "Ownr", "Nm"),
            from_date=_iso(_text(stmt, "FrToDt", "FrDtTm")),
            to_date=_iso(_text(stmt, "FrToDt", "ToDtTm")),
        )
        for bal in _children(stmt, "Bal"):
            code = _text(bal, "Tp", "CdOrPrtry", "Cd")
            amount = _dec(_text(bal, "Amt"))
            if amount is not None and _text(bal, "CdtDbtInd") == "DBIT":
                amount = -amount
            if code == "OPBD":
                st.opening_balance = amount
            elif code == "CLBD":
                st.closing_balance = amount
        for i, ntry in enumerate(_children(stmt, "Ntry")):
            amt_el = _child(ntry, "Amt")
            amount = _dec(amt_el.text if amt_el is not None else None)
            if amount is None:
                continue
            if _text(ntry, "CdtDbtInd") == "DBIT":
                amount = -amount
            currency = (amt_el.get("Ccy") if amt_el is not None else None) or st.currency
            booking = _iso(_text(ntry, "BookgDt", "Dt") or _text(ntry, "BookgDt", "DtTm"))
            value_date = _iso(_text(ntry, "ValDt", "Dt") or _text(ntry, "ValDt", "DtTm"))
            if booking is None:
                booking = value_date or date.today()
            details = _children(_child(ntry, "NtryDtls"), "TxDtls")
            tx = details[0] if details else None
            parties = _child(tx, "RltdPties")
            if amount < 0:
                name = _text(parties, "Cdtr", "Nm") or _text(parties, "Cdtr", "Pty", "Nm")
                cp_iban = _text(parties, "CdtrAcct", "Id", "IBAN")
            else:
                name = _text(parties, "Dbtr", "Nm") or _text(parties, "Dbtr", "Pty", "Nm")
                cp_iban = _text(parties, "DbtrAcct", "Id", "IBAN")
            rmt = _child(tx, "RmtInf")
            unstructured = [u.text.strip() for u in _children(rmt, "Ustrd") if u.text and u.text.strip()]
            structured_ref = _text(_child(rmt, "Strd"), "CdtrRefInf", "Ref")
            remittance = " ".join(unstructured + ([structured_ref] if structured_ref else []))
            if not remittance:
                remittance = _text(ntry, "AddtlNtryInf") or ""
            e2e = _text(tx, "Refs", "EndToEndId")
            ref = _text(ntry, "AcctSvcrRef") or _text(tx, "Refs", "AcctSvcrRef") or _text(tx, "Refs", "TxId")
            ext = ref or _hash_id(iban, booking, amount, currency, e2e or "", remittance, i)
            st.transactions.append(
                ParsedTransaction(
                    external_id=ext[:120],
                    booking_date=booking,
                    value_date=value_date,
                    amount=amount,
                    currency=currency.upper(),
                    counterparty_name=(name or None) and name[:200],
                    counterparty_iban=normalise_iban(cp_iban),
                    remittance_info=remittance[:1000] or None,
                    end_to_end_id=(e2e or None) and e2e[:70],
                    raw={
                        "entry_ref": _text(ntry, "NtryRef"),
                        "status": _text(ntry, "Sts") or _text(ntry, "Sts", "Cd"),
                    },
                )
            )
        statements.append(st)
    if not statements:
        raise StatementFormatError("camt.053 without statements")
    return statements


# ---------------------------------------------------------------------------------------- MT940
_TAG_RE = re.compile(r"^:(\d{2}[A-Z]?):", re.M)
_61_RE = re.compile(
    r"^(?P<date>\d{6})(?P<entry>\d{4})?(?P<mark>R?[CD])(?P<fc>[A-Z])?(?P<amount>[\d,]+)"
    r"(?P<type>[A-Z][A-Z0-9]{3})(?P<ref>[^/\n]{0,16})(?://(?P<bankref>[^\n]*))?",
    re.S,
)
_86_SUBFIELD_RE = re.compile(r"\?(\d{2})")
_SLASH_RE = re.compile(r"/(EREF|MREF|CRED|DEBT|REMI|IBAN|NAME|SVWZ|PURP|RTRN)/")


def _mt_date(value: str, year_hint: int | None = None) -> date:
    yy, mm, dd = int(value[:2]), int(value[2:4]), int(value[4:6])
    return date(2000 + yy, mm, dd)


def _split_fields(text: str) -> list[tuple[str, str]]:
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    out: list[tuple[str, str]] = []
    positions = list(_TAG_RE.finditer(body))
    for i, m in enumerate(positions):
        end = positions[i + 1].start() if i + 1 < len(positions) else len(body)
        value = body[m.end() : end].strip("\n")
        if value.endswith("\n-"):
            value = value[:-2]
        out.append((m.group(1), value))
    return out


def _parse_86(value: str) -> dict[str, str]:
    """Structured ``:86:`` conventions: ``?20..?29`` remittance, ``?30/?31`` bank/IBAN, ``?32/?33``
    name (German SEPA layout) and the ``/REMI/`` / ``/NAME/`` / ``/IBAN/`` slash layout."""
    flat = " ".join(line.strip() for line in value.split("\n"))
    out: dict[str, str] = {}
    if "?" in flat and _86_SUBFIELD_RE.search(flat):
        parts = _86_SUBFIELD_RE.split(flat)
        # parts: [prefix, code, text, code, text, ...]
        remittance, name = [], []
        for code, text in zip(parts[1::2], parts[2::2], strict=False):
            text = text.strip()
            n = int(code)
            if 20 <= n <= 29 or 60 <= n <= 63:
                remittance.append(text)
            elif n == 31:
                out["iban"] = text
            elif n in (32, 33):
                name.append(text)
        joined = " ".join(r for r in remittance if r)
        for key, label in (
            ("EREF+", "eref"),
            ("SVWZ+", "remittance"),
            ("MREF+", "mref"),
            ("KREF+", "kref"),
            ("CRED+", "cred"),
        ):
            if key in joined:
                seg = joined.split(key, 1)[1]
                for stop in ("EREF+", "SVWZ+", "MREF+", "KREF+", "CRED+", "ABWA+", "ABWE+"):
                    seg = seg.split(stop, 1)[0]
                out[label] = seg.strip()
        out["remittance"] = out.get("remittance") or joined
        if name:
            out["name"] = " ".join(name)
        return out
    if _SLASH_RE.search(flat):
        pieces = _SLASH_RE.split(flat)
        for code, text in zip(pieces[1::2], pieces[2::2], strict=False):
            text = text.strip()
            if code in ("REMI", "SVWZ"):
                out["remittance"] = text
            elif code == "NAME":
                out["name"] = text
            elif code == "IBAN":
                out["iban"] = text
            elif code == "EREF":
                out["eref"] = text
        out.setdefault("remittance", flat)
        return out
    out["remittance"] = flat
    return out


def parse_mt940(data: bytes) -> list[ParsedStatement]:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    if ":20:" not in text or ":61:" not in text and ":60F:" not in text and ":60M:" not in text:
        raise StatementFormatError("not an MT940 message")
    statements: list[ParsedStatement] = []
    for message in re.split(r"\n-\s*\n|\r\n-\r\n", text.replace("\r\n", "\n")):
        if ":20:" not in message:
            continue
        fields = _split_fields(message)
        st = ParsedStatement(format="mt940", iban="", currency="EUR")
        pending_61: ParsedTransaction | None = None
        seq = 0
        for tag, value in fields:
            if tag == "25" or tag == "25P":
                acct = value.strip().split("\n")[0]
                if "/" in acct and not acct.upper().startswith(
                    ("DE", "NL", "BE", "FR", "AT", "CH", "PL", "ES", "IT", "LU")
                ):
                    acct = acct.split("/", 1)[1]
                st.iban = normalise_iban(acct) or acct
            elif tag in ("60F", "60M"):
                m = re.match(r"([CD])(\d{6})([A-Z]{3})([\d,]+)", value.strip())
                if m:
                    amt = _dec(m.group(4)) or Decimal("0")
                    st.currency = m.group(3)
                    st.opening_balance = -amt if m.group(1) == "D" else amt
                    st.from_date = st.from_date or _mt_date(m.group(2))
            elif tag in ("62F", "62M"):
                m = re.match(r"([CD])(\d{6})([A-Z]{3})([\d,]+)", value.strip())
                if m:
                    amt = _dec(m.group(4)) or Decimal("0")
                    st.closing_balance = -amt if m.group(1) == "D" else amt
                    st.to_date = _mt_date(m.group(2))
            elif tag == "61":
                m = _61_RE.match(value.strip())
                if not m:
                    pending_61 = None
                    continue
                seq += 1
                amount = _dec(m.group("amount")) or Decimal("0")
                mark = m.group("mark")
                if mark in ("D", "RC"):
                    amount = -amount
                booking = _mt_date(m.group("date"))
                value_date = booking
                entry = m.group("entry")
                if entry:
                    try:
                        booking = date(booking.year, int(entry[:2]), int(entry[2:]))
                        if booking > value_date and (booking - value_date).days > 300:
                            booking = date(booking.year - 1, booking.month, booking.day)
                    except ValueError:
                        booking = value_date
                bankref = (m.group("bankref") or "").strip().split("\n")[0]
                custref = (m.group("ref") or "").strip()
                ext = bankref or _hash_id(st.iban, booking, amount, custref, seq)
                pending_61 = ParsedTransaction(
                    external_id=ext[:120],
                    booking_date=booking,
                    value_date=value_date,
                    amount=amount,
                    currency=st.currency,
                    raw={"type": m.group("type"), "customer_ref": custref, "bank_ref": bankref},
                )
                st.transactions.append(pending_61)
            elif tag == "86" and pending_61 is not None:
                info = _parse_86(value)
                pending_61.remittance_info = (info.get("remittance") or "")[:1000] or None
                pending_61.counterparty_name = (info.get("name") or "")[:200] or None
                pending_61.counterparty_iban = normalise_iban(info.get("iban"))
                pending_61.end_to_end_id = (info.get("eref") or "")[:70] or None
                pending_61 = None
        if st.transactions or st.opening_balance is not None:
            statements.append(st)
    if not statements:
        raise StatementFormatError("MT940 without statements")
    return statements


# ---------------------------------------------------------------------------------------- detection
def parse_statement(data: bytes) -> list[ParsedStatement]:
    head = data[:4096].lstrip()
    if head.startswith(b"<") or b"<Document" in head:
        return parse_camt053(data)
    if b":20:" in head or b":25:" in head:
        return parse_mt940(data)
    raise StatementFormatError("unknown statement format (expected camt.053 XML or MT940)")
