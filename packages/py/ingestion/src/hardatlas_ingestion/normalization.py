import re
import unicodedata


def normalize_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip().casefold()
    return re.sub(r"\s+", " ", normalized)


def normalize_model_name(value: str) -> str:
    """Backward-compatible alias used by the optional hardware extension."""
    return normalize_label(value)
