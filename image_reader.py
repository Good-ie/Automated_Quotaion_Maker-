"""Read item lists from pictures (screenshots or photos) with Tesseract OCR (free).

On Streamlit Cloud, Tesseract is installed from packages.txt. The text that comes out
is shown to staff to check, then goes through the normal item reader.
"""
from __future__ import annotations

import re
import shutil
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageFilter, ImageOps

_WINDOWS_TESSERACT = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")

# bits of chat screens that are not part of the message: "10:41 AM \u2713\u2713" (OCR often
# reads the ticks as "OO", "0" or a stray "O" stuck to AM)
_CLOCK = re.compile(r"\s*\b\d{1,2}[:.]\d{2}\s*(?:[ap]\.?\s?m\.?)?\s*[\u2713\u2714O0oQvV/]{0,3}\s*$", re.IGNORECASE)
_TICKS = re.compile(r"[\u2713\u2714]+")
# a chat line that was wrapped onto the next line ends like this
_WRAPPED = re.compile(r"(?:[(,\-&/]|\b(?:for|of|and|with|the|to|a|an|in|on|at|by|from))\s*$", re.IGNORECASE)
# item codes: OCR mixes up the letter O and zero ("ITACO0O195" is ITAC00195)
_CODE = re.compile(r"\b[A-Z]{2,}[A-Z0-9]*\d[A-Z0-9]*\b")


def _tesseract():
    try:
        import pytesseract
    except ImportError:
        return None
    if shutil.which("tesseract"):
        return pytesseract
    if _WINDOWS_TESSERACT.exists():
        pytesseract.pytesseract.tesseract_cmd = str(_WINDOWS_TESSERACT)
        return pytesseract
    return None


def available() -> bool:
    return _tesseract() is not None


def prepare(image: Image.Image) -> Image.Image:
    """Make a picture easy to read: upright, grey, large enough, dark text on white."""
    image = ImageOps.exif_transpose(image)  # phone/PC photos taken sideways
    gray = ImageOps.grayscale(image.convert("RGB"))
    # small screenshots: enlarge so letters are ~30px tall
    if gray.width < 1600:
        factor = min(3.0, 1600 / max(gray.width, 1))
        gray = gray.resize((int(gray.width * factor), int(gray.height * factor)), Image.LANCZOS)
    # dark-mode screenshots (light text on dark): flip to dark text on light
    histogram = gray.histogram()
    dark_pixels = sum(histogram[:110])
    if dark_pixels > 0.5 * gray.width * gray.height:
        gray = ImageOps.invert(gray)
    gray = ImageOps.autocontrast(gray, cutoff=1)
    return gray.filter(ImageFilter.SHARPEN)


def _fix_code(match: re.Match) -> str:
    code = match.group(0)
    if sum(ch.isdigit() for ch in code) < 2:
        return code
    # an O touching a digit (or a run of O/0 that ends in a digit) is a zero
    return re.sub(r"O(?=[O0]*\d)|(?<=\d)O", "0", code)


def clean(text: str) -> str:
    """Tidy OCR text: drop chat times and ticks, rejoin wrapped chat lines, fix codes."""
    lines: list[str] = []
    for line in text.splitlines():
        line = _TICKS.sub("", line)
        line = _CLOCK.sub("", line).strip()
        line = re.sub(r"^[{|\[](?=[A-Z]{2,})", "I", line)  # "{TAC00195": a leading I read as { or |
        line = re.sub(r"\s{2,}", " ", line)
        if not re.search(r"[A-Za-z0-9]", line):
            continue  # rules, borders and other scraps
        line = _CODE.sub(_fix_code, line)
        starts_lower_or_closing = bool(re.match(r"[a-z)]|\d+\s*(?:unit|pcs|nos)\b", line))
        if lines and (_WRAPPED.search(lines[-1]) or (starts_lower_or_closing and lines[-1].count("(") > lines[-1].count(")"))):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return "\n".join(re.sub(r"\(\s+", "(", line) for line in lines)


def read_picture(data: bytes) -> str:
    """Text found in a picture, cleaned up. Raises RuntimeError if OCR is not installed."""
    tesseract = _tesseract()
    if tesseract is None:
        raise RuntimeError("Reading pictures needs Tesseract OCR, which is installed on the online app.")
    return read_image(Image.open(BytesIO(data)))


def read_image(image: Image.Image) -> str:
    """Text found in an already opened picture (also used for scanned PDF pages)."""
    tesseract = _tesseract()
    if tesseract is None:
        raise RuntimeError("Reading pictures needs Tesseract OCR, which is installed on the online app.")
    # psm 4: a single column of lines of different sizes, like a chat or a list
    text = tesseract.image_to_string(prepare(image), lang="eng", config="--psm 4")
    return clean(text)
