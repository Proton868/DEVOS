"""Shared input-sanitization helpers (security-audit P3f).

Several routes accept free-text fields (names, descriptions, search
queries, chat/memory content) that are persisted to the database and,
in some cases, later replayed back into an LLM prompt or rendered in the
UI. None of that data is executed as code, so this is not primarily an
injection-prevention layer (that's `governance.ucip.PromptInjectionScanner`,
which screens Brain tool-call inputs) -- it's a defense-in-depth hygiene
pass that strips ASCII control characters (NUL, ANSI escape sequences,
etc.) that have no legitimate purpose in these fields and have historically
been used for terminal/log injection, hidden-character smuggling, and
similar low-severity-but-real issues.

Two variants are provided because not all free-text fields are the same:

* `sanitize_name()` -- for short, single-line, identifier-like fields
  (names, tags, entity types, relation types, search queries). Strips
  ALL control characters including tab/newline/carriage-return, since
  a "name" should never legitimately contain one.
* `sanitize_freeform()` -- for longer free-text fields (descriptions,
  chat/memory content) that may legitimately contain multi-line text
  or embedded code blocks. Preserves tab/newline/carriage-return but
  still strips NUL, ESC, and other C0 control characters that have no
  legitimate use even in free-form text.

This mirrors (and is safe to use alongside) the sanitization pattern
already established in `api/routes/extras.py`'s `BuildRequest`/
`EndpointCreate` models.
"""
import re

# Strips every ASCII control character including tab/newline/CR/DEL --
# appropriate for fields that should always be a single line.
_STRICT_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# Strips ASCII control characters EXCEPT tab (\x09), newline (\x0a) and
# carriage-return (\x0d) -- appropriate for multi-line free text.
_FREEFORM_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_name(value):
    """Strip all control characters (incl. newlines) and surrounding
    whitespace from a short, single-line text field. Non-str values pass
    through unchanged, so this is safe to use directly as a Pydantic
    `mode="before"` validator on Optional[str] fields."""
    if isinstance(value, str):
        return _STRICT_CONTROL_RE.sub("", value).strip()
    return value


def sanitize_freeform(value):
    """Strip dangerous control characters while preserving legitimate
    whitespace (tabs/newlines/CR) from a multi-line free-text field, e.g.
    a description or chat/memory message body. Non-str values pass through
    unchanged."""
    if isinstance(value, str):
        return _FREEFORM_CONTROL_RE.sub("", value).strip()
    return value


def sanitize_name_list(value):
    """Apply `sanitize_name()` item-wise to a list of short strings (e.g.
    tags). Accepts a comma/semicolon-separated string too (split first).
    Empty/whitespace-only items are dropped."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [item for item in re.split(r"[,;]+", value) if item.strip()]
    cleaned = [sanitize_name(str(item)) for item in value]
    return [item for item in cleaned if item]
