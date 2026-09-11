# Invoice Extractor (Python CLI)

A command-line port of my [n8n Invoice Extractor workflow](https://github.com/christhiandonnylacandu/invoice-extractor-n8n), rewritten in Python to call the LLM APIs directly instead of through n8n nodes.

Give it a photo of an invoice; it returns validated structured data as JSON.

```
python invoice_extractor.py samples/invoice.jpg
```

```json
{
  "vendor": "PT Contoh Sejahtera",
  "invoice_no": "INV-2026-0142",
  "invoice_date": "2026-08-01",
  "currency": "IDR",
  "subtotal": 4500000.0,
  "tax": 495000.0,
  "total": 4995000.0,
  "groq_total": 4995000.0,
  "claude_total": 4995000.0,
  "confidence": 0.93,
  "status": "auto",
  "reason": "OK"
}
```

## Pipeline

| Step | What happens | Maps to n8n node |
|---|---|---|
| 1. Encode | Local image → base64 `data:` URL | `Siapkan Gambar` |
| 2. Extract | **Groq / Llama 4 Scout** reads the invoice → full JSON | `Baca Groq` + `Parse Groq` |
| 3. Cross-check | **Claude Haiku 4.5** independently reads total / invoice no / currency | `Cek Silang` |
| 4. Validate | Normalise numbers, compare the two models, math-check, confidence gate → `auto` or `review` | `Bandingkan dan Validasi` |
| 5. Save | Write result JSON to `output/` | (Sheets/Telegram left to the n8n version) |

### Validation rules (`validate()`)

`status` is `auto` only when **all** hold, otherwise `review` with a reason string:

- the two models' totals agree within 0.01
- `subtotal + tax` equals `total` within `max(1, total × 2%)`
- vendor, total and invoice date are all present
- the extractor's own confidence is ≥ 0.7

### Indonesian number handling (`normalize_number()`)

Handles both `1.250.000,00` and `1,250,000.00` by checking which separator appears last, so the arithmetic checks work regardless of the invoice's formatting locale.

## Setup

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # then fill in the two keys
```

`.env`:

```
GROQ_API_KEY=...      # console.groq.com  (free tier)
ANTHROPIC_API_KEY=... # console.anthropic.com
```

Keys are read from the environment only — nothing is committed (`.env` is git-ignored).

## Status

Core pipeline (encode → extract → cross-check → validate → save) is implemented. Telegram intake and Google Sheets output are intentionally out of scope here — that plumbing lives in the [n8n version](https://github.com/christhiandonnylacandu/invoice-extractor-n8n). Run it against your own invoices with your own keys.

## Stack

Python 3.13 · `requests` · `anthropic` · `python-dotenv` · Groq API · Anthropic API
