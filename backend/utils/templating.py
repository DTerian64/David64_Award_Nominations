"""
utils/templating.py
===================
Resolve and render per-tenant, per-language templates from dbo.EmailTemplates.

Resolution falls back along:
    (tenant, key, lang) -> (tenant, key, 'en') -> (1, key, lang) -> (1, key, 'en')

Rendering uses a Jinja2 sandbox. The body is autoescaped (HTML); the subject is
rendered as plain text (no escaping). Resolved rows are cached per process with
a short TTL — templates change rarely and the worker scales to zero.
"""

import logging
import time

from jinja2.sandbox import SandboxedEnvironment

import utils.sqlhelper2 as sqlhelper

logger = logging.getLogger(__name__)

DEFAULT_TENANT_ID = 1
_CACHE_TTL_SECONDS = 60
_cache: dict = {}

_html_env = SandboxedEnvironment(autoescape=True)
_text_env = SandboxedEnvironment(autoescape=False)


class TemplateNotFound(Exception):
    """No template row matched the key for the tenant or the default tenant."""


def _resolve_row(tenant_id: int, key: str, lang: str):
    now = time.time()
    ck = (key, tenant_id, lang)
    hit = _cache.get(ck)
    if hit and hit[0] > now:
        return hit[1]

    rows = sqlhelper.get_email_template_candidates(key, tenant_id, lang)
    by = {(int(r[0]), r[1]): r for r in rows}
    row = None
    for pref in [(tenant_id, lang), (tenant_id, "en"),
                 (DEFAULT_TENANT_ID, lang), (DEFAULT_TENANT_ID, "en")]:
        if pref in by:
            row = by[pref]
            break

    _cache[ck] = (now + _CACHE_TTL_SECONDS, row)
    return row


def render(tenant_id: int, key: str, lang: str, context: dict) -> dict:
    """Return {'subject': str|None, 'body': str} for the resolved template."""
    row = _resolve_row(tenant_id, key, lang)
    if row is None:
        raise TemplateNotFound(f"No template for key={key!r} tenant={tenant_id} lang={lang!r}")
    _tid, _lang, subject, body = row
    return {
        "subject": _text_env.from_string(subject).render(**context) if subject else None,
        "body":    _html_env.from_string(body).render(**context),
    }


def clear_cache() -> None:
    _cache.clear()
