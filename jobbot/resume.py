"""Resume text extraction.

Parses once, caches to a sibling .txt file so re-runs (and repeated scoring
calls) don't re-parse the PDF every time.
"""

from __future__ import annotations

from pathlib import Path


def extract_resume_text(resume_path: str | Path) -> str:
    path = Path(resume_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(
            f"Resume not found at {path}. Set profile.resume_path in config.yaml."
        )

    cache = path.with_suffix(".cache.txt")
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        return cache.read_text(encoding="utf-8")

    if path.suffix.lower() == ".pdf":
        text = _extract_pdf(path)
    elif path.suffix.lower() in (".txt", ".md"):
        text = path.read_text(encoding="utf-8")
    elif path.suffix.lower() == ".docx":
        text = _extract_docx(path)
    else:
        raise ValueError(f"Unsupported resume format: {path.suffix}")

    cache.write_text(text, encoding="utf-8")
    return text


def _extract_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _extract_docx(path: Path) -> str:
    from docx import Document  # python-docx

    doc = Document(str(path))
    return "\n".join(p.text for p in doc.paragraphs)
