"""Ingestion package (W2-M4 spike).

Houses the words+boxes reader (``app.ingestion.reader``) that emits the single
canonical NormBBox coordinate space (W2_ARCHITECTURE.md §2): text-layer first
(pypdfium2/pdfplumber) with a junk-density fallback to Tesseract OCR (§3, W2-D3).
(The former W2-R6 PyMuPDF/AGPL ban was removed by owner decision G-D2, 2026-07-19.)
"""
