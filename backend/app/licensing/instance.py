"""The firm's sealed installation id and machine fingerprint (spec §7.1).

Platform code ``08`` (server). Trait: the host's ``/etc/machine-id`` mounted read-only into the
container (``MACHINE_ID_FILE``); without it, 8 random digits stored on the firm.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from app.config import Settings
from app.licensing.constants import PLATFORM_CODE_SERVER
from app.licensing.installid import fingerprint, make_install_id, random_fingerprint
from app.models.firm import Firm

log = logging.getLogger("invoiceflow.licensing.instance")
_MACHINE_ID_RE = re.compile(r"^[0-9a-fA-F-]{8,64}$")


def read_machine_trait(path: str) -> str | None:
    try:
        text = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not _MACHINE_ID_RE.match(text):
        return None
    return f"machine-id:{text}"


def new_instance(settings: Settings, created: int) -> tuple[str, str, str]:
    """Returns (instance_id, fingerprint, fingerprint_source). Called once when a firm is created."""
    trait = read_machine_trait(settings.machine_id_file)
    if trait is not None:
        fp, source = fingerprint(settings.license_installation_key, trait), "machine-id"
    else:
        fp, source = random_fingerprint(), "random"
        log.info("no machine id available; using a stored random fingerprint for the new firm")
    iid = make_install_id(settings.license_installation_key, PLATFORM_CODE_SERVER, created, fp)
    return iid, fp, source


def current_fingerprint(settings: Settings, firm: Firm) -> str:
    """Computed at every check-in (spec §7.1): from the machine when the id was sealed with one."""
    if firm.instance_fp_source == "machine-id":
        trait = read_machine_trait(settings.machine_id_file)
        if trait is not None:
            return fingerprint(settings.license_installation_key, trait)
    return firm.instance_fp
