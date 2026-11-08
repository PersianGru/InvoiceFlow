#!/usr/bin/env python3
"""Secret scan for the repository and for built image filesystems (CI and pre-commit).

Mirrors ``.gitleaks.toml`` so the scan also runs where gitleaks is not installed. Exit code 1 on a hit.

    python scripts/secret_scan.py            # the working tree
    python scripts/secret_scan.py <dir> ...  # any directories (e.g. an exported image rootfs)
    python scripts/secret_scan.py --strict <dir>  # release bundles: the allow-list does not apply
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

RULES = {
    "21cel-api-key": re.compile(rb"21c_sk_[A-Za-z0-9_-]{6,}"),
    "21cel-access-token": re.compile(rb"21c_at_[A-Za-z0-9_.-]{6,}"),
    "21cel-webhook-secret": re.compile(rb"whsec_[A-Za-z0-9_-]{6,}"),
    "21cel-installation-key": re.compile(rb"ik1\.[1-9]\.[A-Za-z0-9_-]{43}"),
    "private-key": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "invoiceflow-license-key": re.compile(rb"\bIVF-(?:[0-9A-Z]{5}-){4,7}[0-9A-Z]{5}\b"),
    "invoiceflow-api-key": re.compile(rb"ivfk_[a-z0-9]{8}_[a-z0-9]{16,64}"),
    "invoiceflow-webhook-secret": re.compile(rb"ivfwh_[A-Za-z0-9]{16,}"),
    "telegram-bot-token": re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    "meta-access-token": re.compile(rb"\bEAA[A-Za-z0-9]{20,}\b"),
    "aws-access-key": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "stripe-secret-key": re.compile(rb"\b(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}\b"),
    "openai-api-key": re.compile(rb"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}\b"),
}

ALLOW_PATHS = (
    ".gitleaks.toml",
    "scripts/secret_scan.py",
)
# The product's own installation key ships inside the product (spec §7.1): this one file may hold it,
# release bundles included. Every other rule still applies to it.
EMBEDDED_KEY_FILE = "backend/app/licensing/project.py"
EMBEDDED_KEY_RULE = "21cel-installation-key"

ALLOW_VALUES: tuple[bytes, ...] = ()
SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "__pycache__",
    ".next",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".local",
    "data",
    ".expo",
}
MAX_BYTES = 8 * 1024 * 1024


def scan(root: Path, strict: bool = False) -> list[str]:
    hits: list[str] = []
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts) or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if not strict and any(rel == a or rel.startswith(a) for a in ALLOW_PATHS):
            continue
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            continue
        for name, pat in RULES.items():
            if rel == EMBEDDED_KEY_FILE and name == EMBEDDED_KEY_RULE:
                continue
            for m in pat.finditer(data):
                if not strict and m.group(0) in ALLOW_VALUES:
                    continue
                line = data.count(b"\n", 0, m.start()) + 1
                hits.append(f"{rel}:{line}: {name}")
    return hits


def main(argv: list[str]) -> int:
    strict = "--strict" in argv  # release bundles: no allow-list at all
    roots = [Path(a) for a in argv[1:] if a != "--strict"] or [Path(__file__).resolve().parents[1]]
    all_hits: list[str] = []
    for r in roots:
        all_hits.extend(scan(r.resolve(), strict))
    if all_hits:
        print("SECRET SCAN FAILED:")
        for h in all_hits:
            print("  " + h)
        return 1
    print(f"secret scan: clean ({', '.join(str(r) for r in roots)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
