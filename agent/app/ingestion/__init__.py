"""Ingestion package (W2-M4 spike).

Houses the words+boxes reader (``app.ingestion.reader``) that emits the single
canonical NormBBox coordinate space (W2_ARCHITECTURE.md §2): text-layer first
(pypdfium2/pdfplumber) with a junk-density fallback to Tesseract OCR (§3, W2-D3).
PyMuPDF is banned (AGPL, W2-R6); this package never imports it.
"""
