"""Validation and normalization helpers for phone numbers and text inputs."""

import re


_CLEAN_CHARS_RE = re.compile(r"[\s\-\(\)\.]+")
_INDIAN_10_DIGIT_RE = re.compile(r"^[6-9]\d{9}$")
_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def normalize_phone_number(raw_phone: str) -> str:
    """Validate and normalize a phone number to E.164 format.

    Supports:
    - 10-digit Indian mobile numbers (e.g. '9876543210' -> '+919876543210')
    - Indian numbers with leading '0' or '91' (e.g. '09876543210', '919876543210')
    - Standard E.164 numbers (e.g. '+919876543210', '+14155552671')
    """
    if not isinstance(raw_phone, str):
        raise ValueError("Phone number must be a string.")

    cleaned = _CLEAN_CHARS_RE.sub("", raw_phone.strip())
    if not cleaned:
        raise ValueError("Phone number cannot be empty.")

    # Handle 00 international prefix (e.g., 00919876543210 -> +919876543210)
    if cleaned.startswith("00"):
        cleaned = "+" + cleaned[2:]

    if cleaned.startswith("+"):
        if not _E164_RE.match(cleaned):
            raise ValueError(
                f"Invalid E.164 phone number '{raw_phone}'. Expected + followed by 8 to 15 digits."
            )
        # Ensure +91 numbers have valid 10-digit Indian mobile/landline length when applicable
        if cleaned.startswith("+91") and len(cleaned) != 13:
            raise ValueError(
                f"Invalid Indian phone number '{raw_phone}'. Expected +91 followed by 10 digits."
            )
        return cleaned

    # Digits only from this point
    if not cleaned.isdigit():
        raise ValueError(f"Phone number '{raw_phone}' contains invalid characters.")

    # 10-digit Indian mobile number
    if _INDIAN_10_DIGIT_RE.match(cleaned):
        return f"+91{cleaned}"

    # 11-digit Indian number starting with 0
    if len(cleaned) == 11 and cleaned.startswith("0") and _INDIAN_10_DIGIT_RE.match(cleaned[1:]):
        return f"+91{cleaned[1:]}"

    # 12-digit Indian number starting with 91
    if len(cleaned) == 12 and cleaned.startswith("91") and _INDIAN_10_DIGIT_RE.match(cleaned[2:]):
        return f"+91{cleaned[2:]}"

    raise ValueError(
        f"Invalid phone number '{raw_phone}'. Provide a 10-digit Indian mobile number or E.164 format."
    )


def normalize_optional_text(value: str | None) -> str | None:
    """Trim optional text and convert blank strings to None."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None