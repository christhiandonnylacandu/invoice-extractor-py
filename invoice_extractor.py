"""
Port dari workflow n8n "Invoice Extractor (Dual-LLM cross-check + HITL)".
Alur sama, sumber gambar & penyimpanan disederhanakan buat versi CLI:
  foto lokal -> Groq (extract) -> OpenRouter/Gemini (cross-check) -> validasi -> JSON lokal.
"""

import base64
import json
import mimetypes
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")

if not GROQ_API_KEY or not OPENROUTER_API_KEY:
    print("GROQ_API_KEY dan/atau OPENROUTER_API_KEY belum diisi.")
    print("Salin .env.example ke .env, lalu isi kedua key sebelum jalankan lagi.")
    sys.exit(1)

GROQ_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"
OPENROUTER_MODEL = "google/gemini-2.5-flash"

EXTRACT_PROMPT = (
    "Anda adalah extractor data invoice. Baca gambar invoice dan kembalikan HANYA JSON "
    "dengan keys: vendor, invoice_no, invoice_date, due_date, currency, subtotal, tax, "
    "total, confidence. Tanggal format YYYY-MM-DD. subtotal, tax, total berupa angka "
    "tanpa pemisah ribuan. confidence antara 0 sampai 1. Jika tidak ada gunakan null."
)
CROSSCHECK_PROMPT = (
    "Baca gambar invoice ini secara independen. Kembalikan HANYA JSON dengan keys: "
    "total, invoice_no, currency. total berupa angka tanpa pemisah ribuan. "
    "Jika tidak ada gunakan null."
)


def image_to_data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "image/jpeg"
    b64 = base64.b64encode(path.read_bytes()).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def extract_with_groq(data_url: str) -> dict:
    """Setara node 'Baca Groq' + 'Parse Groq'."""
    resp = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
        json={
            "model": GROQ_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": EXTRACT_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return {}


def crosscheck_with_openrouter(data_url: str) -> dict:
    """Setara node 'Cek Silang (Gemini 2.5 Flash)' — independent second read via OpenRouter."""
    resp = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
        json={
            "model": OPENROUTER_MODEL,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": CROSSCHECK_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
        },
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, re.DOTALL)
        return json.loads(match.group(0)) if match else {}


def normalize_number(value):
    """Port dari fungsi norm() di node 'Bandingkan dan Validasi' (JS -> Python)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^0-9.,-]", "", str(value))
    if s == "":
        return None
    has_comma = "," in s
    has_dot = "." in s
    if has_comma and has_dot:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        s = s.replace(",", ".")
    elif has_dot:
        parts = s.split(".")
        if len(parts) > 2 or len(parts[-1]) == 3:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def validate(groq_data: dict, openrouter_data: dict) -> dict:
    """Setara node 'Bandingkan dan Validasi'."""
    g_total = normalize_number(groq_data.get("total"))
    o_total = normalize_number(openrouter_data.get("total"))
    sub = normalize_number(groq_data.get("subtotal"))
    tax = normalize_number(groq_data.get("tax"))
    g_no = str(groq_data.get("invoice_no") or "").strip()
    o_no = str(openrouter_data.get("invoice_no") or "").strip()
    conf = float(groq_data.get("confidence") or 0)

    totals_agree = g_total is not None and o_total is not None and abs(g_total - o_total) < 0.01
    no_agree = g_no != "" and o_no != "" and g_no == o_no
    math_ok = True
    if sub is not None and tax is not None and g_total is not None:
        math_ok = abs((sub + tax) - g_total) <= max(1, g_total * 0.02)
    required = bool(groq_data.get("vendor")) and g_total is not None and bool(groq_data.get("invoice_date"))

    reasons = []
    if not totals_agree:
        reasons.append("Total beda Groq vs Gemini")
    if not no_agree:
        reasons.append("No invoice beda atau kosong")
    if not math_ok:
        reasons.append("Subtotal plus pajak tidak sama dengan total")
    if not required:
        reasons.append("Field wajib kurang")
    if 0 < conf < 0.7:
        reasons.append("Confidence rendah")

    status = "auto" if (totals_agree and math_ok and required and conf >= 0.7) else "review"

    return {
        "processed_at": datetime.now(timezone.utc).isoformat(),
        "sumber": "Python CLI",
        "vendor": groq_data.get("vendor"),
        "invoice_no": groq_data.get("invoice_no") or openrouter_data.get("invoice_no"),
        "invoice_date": groq_data.get("invoice_date"),
        "due_date": groq_data.get("due_date"),
        "currency": groq_data.get("currency") or openrouter_data.get("currency"),
        "subtotal": sub,
        "tax": tax,
        "total": g_total,
        "groq_total": g_total,
        "gemini_total": o_total,
        "confidence": conf,
        "status": status,
        "reason": "; ".join(reasons) if reasons else "OK",
    }


def main():
    if len(sys.argv) != 2:
        print("Pakai: python invoice_extractor.py <path_ke_foto_invoice>")
        sys.exit(1)

    image_path = Path(sys.argv[1])
    if not image_path.exists():
        print(f"File tidak ditemukan: {image_path}")
        sys.exit(1)

    data_url = image_to_data_url(image_path)

    print("-> Membaca invoice via Groq...")
    groq_data = extract_with_groq(data_url)

    print("-> Cross-check via OpenRouter (Gemini 2.5 Flash)...")
    openrouter_data = crosscheck_with_openrouter(data_url)

    result = validate(groq_data, openrouter_data)
    print(json.dumps(result, indent=2, ensure_ascii=False))

    output_dir = Path("output")
    output_dir.mkdir(exist_ok=True)
    out_file = output_dir / f"{image_path.stem}_result.json"
    out_file.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDisimpan ke {out_file}")

    if result["status"] == "review":
        print("Status: REVIEW — perlu dicek manusia. Alasan:", result["reason"])
    else:
        print("Status: AUTO — lolos validasi otomatis.")


if __name__ == "__main__":
    main()
