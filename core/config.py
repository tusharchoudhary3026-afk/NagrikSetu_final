from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent


def resolve_db_path(value: str | Path | None = None) -> Path:
    path = Path(value if value is not None else os.environ.get(
        "NYAYAFLOW_DB_PATH", ROOT / "db" / "nyayaflow.db")).expanduser()
    return path if path.is_absolute() else ROOT / path


DB_PATH = resolve_db_path()
SCHEMA_PATH = ROOT / "db" / "schema.sql"
RESP_TABLE = ROOT / "data" / "responsibility_table.csv"
KEYWORDS = ROOT / "data" / "keywords.csv"
COMPLAINTS = ROOT / "data" / "complaints.csv"
TEST_MANIFEST = ROOT / "data" / "test_split_manifest.json"
THRESHOLD_FILE = ROOT / "data" / "threshold.json"
SAFETY_TEST_CASES = ROOT / "data" / "safety_test_cases.csv"
GOV_ALLOWLIST = ROOT / "data" / "gov_allowlist.csv"

REASON_CODES = ["wrong_category", "missing_info", "overlapping_ownership", "duplicate_report", "other"]
REVIEW_ACTIONS = ["accept", "edit", "clarify", "escalate", "transfer"]
REASON_REQUIRED = {"edit", "escalate", "transfer", "duplicate_linked", "duplicate_unlinked",
                   "extraction_corrected"}

# Team sets this on a validation split (see scripts/evaluate.py). Placeholder only.
CONFIDENCE_THRESHOLD = 0.40

SIM_LABEL = "SIMULATED / SYNTHETIC DATA - not real municipal data"


_FEATURE_DEFAULTS = {
    "voice": True,
    "photos": True,
    "hotspots": True,
    "citizen_review": True,
    "learning": True,
}


def feature_enabled(name: str) -> bool:
    """Read a local feature switch; authorization is deliberately never optional."""
    key = str(name or "").strip().casefold()
    if key == "rbac":
        return True
    if key not in _FEATURE_DEFAULTS:
        raise ValueError(f"Unknown feature flag: {name}")
    raw = os.environ.get(f"NYAYAFLOW_FEATURE_{key.upper()}")
    if raw is None:
        return _FEATURE_DEFAULTS[key]
    return raw.strip().casefold() not in {"0", "false", "no", "off", "disabled"}
