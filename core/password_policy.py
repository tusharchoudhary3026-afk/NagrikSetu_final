"""Pure password scoring and strong-password suggestions for account flows."""
from __future__ import annotations

import re
import secrets
import string
from pathlib import Path


COMMON_PASSWORDS_PATH = Path(__file__).resolve().parent.parent / "data" / "common_passwords.txt"
_COMMON_CACHE: set[str] | None = None
_CLASSES = (re.compile(r"[a-z]"), re.compile(r"[A-Z]"), re.compile(r"\d"),
            re.compile(r"[^A-Za-z0-9]"))
_SEQUENCES = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiop", "asdfghjkl", "zxcvbnm")


def _common_passwords() -> set[str]:
    global _COMMON_CACHE
    if _COMMON_CACHE is None:
        try:
            _COMMON_CACHE = {line.strip().casefold() for line in COMMON_PASSWORDS_PATH.read_text(
                encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")}
        except OSError:
            _COMMON_CACHE = {"password", "password123", "1234567890", "qwerty12345"}
    return _COMMON_CACHE


def _has_trivial_pattern(password: str) -> bool:
    folded = password.casefold()
    if re.search(r"(.)\1{3,}", folded):
        return True
    digits = re.sub(r"\D", "", folded)
    for sequence in (_SEQUENCES[0], _SEQUENCES[0][::-1]):
        if any(sequence[start:start + size] in digits
               for size in range(4, len(sequence) + 1)
               for start in range(len(sequence) - size + 1)):
            return True
    for sequence in _SEQUENCES[1:]:
        if any(sequence[index:index + 4] in folded or sequence[index:index + 4][::-1] in folded
               for index in range(len(sequence) - 3)):
            return True
    return False


def score_password(password: str, *, email: str | None = None, name: str | None = None,
                   phone: str | None = None) -> dict:
    """Return a deterministic score (0-4), failed rules and concrete suggestions."""
    value = password if isinstance(password, str) else ""
    folded = value.casefold()
    failures: list[str] = []
    suggestions: list[str] = []

    classes = sum(bool(pattern.search(value)) for pattern in _CLASSES)
    passphrase = len(value) >= 16 and len(re.findall(r"[A-Za-z]{2,}", value)) >= 4
    if len(value) < 10:
        failures.append("minimum_length")
        suggestions.append("Use at least 10 characters")
    if not (classes >= 3 or passphrase):
        failures.append("character_classes_or_passphrase")
        suggestions.append("Add a symbol or try a 4-word passphrase")
    if len(value) > 128:
        failures.append("maximum_length")
        suggestions.append("Keep the password at 128 characters or fewer")
    if folded in _common_passwords():
        failures.append("common_password")
        suggestions.append("Avoid a common password; choose an original phrase")
    if _has_trivial_pattern(value):
        failures.append("trivial_pattern")
        suggestions.append("Avoid repeated characters, keyboard patterns, and simple sequences")

    identity_tokens = []
    if email:
        local = str(email).split("@", 1)[0].casefold()
        if local:
            identity_tokens.append(local)
    if name:
        identity_tokens.extend(token.casefold() for token in re.findall(r"[A-Za-z0-9]+", str(name))
                               if len(token) >= 2)
        cleaned_name = re.sub(r"[^A-Za-z0-9]", "", str(name)).casefold()
        if cleaned_name:
            identity_tokens.append(cleaned_name)
    if phone:
        digits = re.sub(r"\D", "", str(phone))
        if digits:
            identity_tokens.extend([digits, digits[-10:]])
    if any(token in folded for token in identity_tokens):
        failures.append("contains_personal_information")
        suggestions.append("Avoid your email name, name, or phone number")

    # Only count the pattern and common-password checks after the minimum length
    # is met. Otherwise an empty/very short value incorrectly starts at 2/4.
    minimum_length_met = 10 <= len(value) <= 128
    score = (
        int(minimum_length_met)
        + int(classes >= 3 or passphrase)
        + int(minimum_length_met and not _has_trivial_pattern(value))
        + int(minimum_length_met and folded not in _common_passwords())
    )
    accepted = not failures
    if accepted:
        suggestions = ["This password meets the current strength checks"]
        score = 4
    else:
        # Failed rules (for example a common password or personal information)
        # must never be presented as a full-strength password.
        score = min(score, 3)
    return {"score": score, "failed_rules": failures, "suggestions": list(dict.fromkeys(suggestions)),
            "accepted": accepted}


def generate_strong_password(length: int = 20) -> str:
    """Generate a random password with all four classes using the secrets module."""
    if not isinstance(length, int) or length < 16 or length > 128:
        raise ValueError("Generated passwords must be 16 to 128 characters long.")
    groups = (string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#$%^&*()-_=+")
    chars = [secrets.choice(group) for group in groups]
    alphabet = "".join(groups)
    chars.extend(secrets.choice(alphabet) for _ in range(length - len(chars)))
    # Fisher-Yates driven by secrets avoids predictable class positions.
    for index in range(len(chars) - 1, 0, -1):
        swap = secrets.randbelow(index + 1)
        chars[index], chars[swap] = chars[swap], chars[index]
    return "".join(chars)


def password_suggestions(password: str, **identity) -> dict:
    """UI-friendly alias retained to make the scoring API explicit."""
    return score_password(password, **identity)
