# Optional Week 2 demo documents

These two born-digital PDFs are synthetic, clinic-style demonstration inputs:

- `synthetic_clinic_intake.pdf`
- `synthetic_lab_report.pdf`

Both contain a real text layer and prominent **synthetic / not for clinical use**
markings. They contain no real patient information. Regenerate them deterministically:

```bash
cd agent
.venv/bin/python demo/fixtures/generate_demo_pdfs.py
```

They are deliberately outside `evals/golden` and are not referenced by the governed
50-case manifest. They are optional demo fixtures only.
