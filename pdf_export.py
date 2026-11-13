"""Word -> PDF using LibreOffice (free).

On Streamlit Cloud, LibreOffice is installed from packages.txt. On a PC without
LibreOffice the app still works; it just offers the Word file only.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

_CANDIDATES = [
    "soffice",
    "libreoffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]


def soffice_path() -> str | None:
    for candidate in _CANDIDATES:
        found = shutil.which(candidate)
        if found:
            return found
        if Path(candidate).is_file():
            return candidate
    return None


def available() -> bool:
    return soffice_path() is not None


def docx_to_pdf(data: bytes, timeout: int = 180) -> bytes | None:
    """Convert a .docx (as bytes) to PDF bytes, or None if that is not possible here."""
    exe = soffice_path()
    if not exe:
        return None
    with tempfile.TemporaryDirectory(prefix="rq_pdf_") as folder:
        work = Path(folder)
        source = work / "document.docx"
        source.write_bytes(data)
        # a private LibreOffice profile per conversion, so two at once never clash
        profile = (work / "profile").resolve().as_uri()
        command = [exe, f"-env:UserInstallation={profile}", "--headless", "--norestore",
                   "--convert-to", "pdf", "--outdir", str(work), str(source)]
        try:
            subprocess.run(command, capture_output=True, timeout=timeout, check=False)
        except (subprocess.TimeoutExpired, OSError):
            return None
        result = work / "document.pdf"
        return result.read_bytes() if result.exists() and result.stat().st_size > 0 else None
