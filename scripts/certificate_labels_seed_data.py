"""
Certificate label seed data — the 'certificate' template key.

Unlike the email templates, the body is a small JSON object of localized label
strings (not HTML / Jinja). backend/utils/certificate.py reads the best-matching
row for (tenant, lang) and overlays these labels onto the certificate PDF, so the
certificate is localized from the same store. Subject is NULL (not an email).

Korean is AI-drafted — have a native speaker review before production.
"""

import json

_EN = {
    "title":           "Certificate of Excellence",
    "presented_to":    "This certificate is proudly presented to",
    "recognition":     "in recognition of an outstanding contribution, with a monetary award of {amount}.",
    "category_label":  "Category",
    "date_label":      "Date",
    "signatory_label": "Approving Manager",
}

_KO = {
    "title":           "우수상 증서",
    "presented_to":    "이 증서를 다음 분께 수여합니다",
    "recognition":     "뛰어난 기여를 인정하여 {amount}의 포상을 수여합니다.",
    "category_label":  "분류",
    "date_label":      "날짜",
    "signatory_label": "승인 관리자",
}

# Same shape as the email seed dicts: {key: {"subject", "body"}}. Subject is None
# (NULL) for the certificate key; body is the JSON label object.
CERT_EN = {"certificate": {"subject": None, "body": json.dumps(_EN, ensure_ascii=False)}}
CERT_KO = {"certificate": {"subject": None, "body": json.dumps(_KO, ensure_ascii=False)}}
