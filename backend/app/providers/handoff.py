"""Artifact delivery, kept separate from provider semantics.

Only LocalExportAdapter exists. There is deliberately no adapter that opens pull requests or
mutates cloud resources; that is a deferred increment requiring owner agreement.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from app.core.errors import Unsupported


class LocalExportAdapter:
    name = "local-export"

    def __init__(self, export_dir: str):
        self.root = Path(export_dir)

    def export(self, package_id: str, bundle_digest: str, files: dict[str, str]) -> str:
        target = self.root / package_id / bundle_digest.split(":", 1)[1][:16]
        for rel, content in sorted(files.items()):
            path = (target / rel).resolve()
            if not str(path).startswith(str(target.resolve())):
                raise Unsupported(f"Refusing to write outside export directory: {rel}")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return str(target)


def deterministic_zip(files: dict[str, str]) -> bytes:
    """Zip with fixed timestamps and ordering so identical bundles produce identical bytes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in sorted(files):
            info = zipfile.ZipInfo(rel, date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, files[rel].encode("utf-8"))
    return buf.getvalue()


def get_handoff_adapter(name: str, export_dir: str) -> LocalExportAdapter:
    if name != "local-export":
        raise Unsupported(f"Handoff adapter {name!r} is not implemented")
    return LocalExportAdapter(export_dir)
