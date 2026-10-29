"""Hotspot Detection Engine for NyayaFlow.

Groups scoped complaints by geographic area and issue category over comparison
time windows to detect concentration spikes for human review.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
import pandas as pd

from core.staff_dashboard import list_staff_cases


def detect_hotspot_signals(
    user: Dict[str, Any],
    window_days: int = 7,
    as_of: Optional[datetime] = None,
    area_filter: str = "All authorized areas",
    category_filter: str = "All categories",
    min_count_threshold: int = 3,
    surge_multiplier: float = 1.5,
) -> Dict[str, Any]:
    """Analyze authorized complaints to identify localized frequency surges.

    Args:
        user: Authenticated user session dictionary with role and scope permissions.
        window_days: Comparison evaluation window in days.
        as_of: Reference cutoff timestamp (defaults to UTC now).
        area_filter: Selected ward/area filter name.
        category_filter: Selected category/issue filter name.
        min_count_threshold: Minimum complaint count required to flag.
        surge_multiplier: Threshold multiplier over baseline count.

    Returns:
        Structured report dictionary matching the 13_Hotspots.py interface contract.
    """
    if as_of is None:
        as_of = datetime.now(timezone.utc)
    elif as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)

    # 1. Fetch authorized cases scoped to the user
    cases = list_staff_cases(user)

    current_start = as_of - timedelta(days=window_days)
    current_end = as_of
    comparison_start = current_start - timedelta(days=window_days)
    comparison_end = current_start

    data_label = "Operational simulation data · Synthetic baseline"
    rule_desc = (
        f"Flagged if current window count >= {min_count_threshold} and exceeds preceding "
        f"{window_days}-day baseline by >= {int(surge_multiplier * 100)}% (or new cluster appearance)"
    )

    if not cases:
        return {
            "status": "insufficient_data",
            "data_label": data_label,
            "current_count": 0,
            "areas_flagged": 0,
            "window_days": window_days,
            "rule": rule_desc,
            "hotspots": [],
            "current_start": current_start.strftime("%Y-%m-%d"),
            "current_end": current_end.strftime("%Y-%m-%d"),
            "comparison_start": comparison_start.strftime("%Y-%m-%d"),
            "comparison_end": comparison_end.strftime("%Y-%m-%d"),
        }

    # Normalize cases into a DataFrame
    df = pd.DataFrame(cases)

    # Convert timestamps with timezone safety
    df["created_at_dt"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    df = df.dropna(subset=["created_at_dt"])

    # Standardize area and category fields
    df["area_clean"] = df.apply(
        lambda r: r.get("area_name") or r.get("location") or "Unknown area", axis=1
    )
    df["category_clean"] = df.apply(
        lambda r: r.get("issue") or "Uncategorized", axis=1
    )

    # Apply UI filters
    if area_filter and area_filter != "All authorized areas":
        df = df[df["area_clean"] == area_filter]
    if category_filter and category_filter != "All categories":
        df = df[df["category_clean"] == category_filter]

    # Split into current period and baseline period
    current_df = df[(df["created_at_dt"] >= current_start) & (df["created_at_dt"] < current_end)]
    baseline_df = df[(df["created_at_dt"] >= comparison_start) & (df["created_at_dt"] < current_start)]

    current_total = len(current_df)
    if current_total == 0:
        return {
            "status": "ok",
            "data_label": data_label,
            "current_count": 0,
            "areas_flagged": 0,
            "window_days": window_days,
            "rule": rule_desc,
            "hotspots": [],
            "current_start": current_start.strftime("%Y-%m-%d"),
            "current_end": current_end.strftime("%Y-%m-%d"),
            "comparison_start": comparison_start.strftime("%Y-%m-%d"),
            "comparison_end": comparison_end.strftime("%Y-%m-%d"),
        }

    hotspot_signals: List[Dict[str, Any]] = []

    # Group current cases by area and issue category
    current_grouped = current_df.groupby(["area_clean", "category_clean"])

    for (area, category), grp in current_grouped:
        curr_count = len(grp)
        if curr_count < min_count_threshold:
            continue

        # Calculate baseline count for this exact area + category
        base_matches = baseline_df[
            (baseline_df["area_clean"] == area) & (baseline_df["category_clean"] == category)
        ]
        base_count = len(base_matches)
        increase = curr_count - base_count

        # Surge qualification
        is_surge = False
        why_flagged = ""

        if base_count == 0 and curr_count >= min_count_threshold:
            is_surge = True
            why_flagged = f"New cluster ({curr_count} cases, 0 in baseline period)"
        elif base_count > 0 and curr_count >= (base_count * surge_multiplier):
            is_surge = True
            pct = int(((curr_count - base_count) / base_count) * 100)
            why_flagged = f"+{pct}% increase over baseline ({curr_count} vs {base_count})"
        elif curr_count >= (min_count_threshold * 2):
            is_surge = True
            why_flagged = f"High absolute volume ({curr_count} complaints recorded)"

        if is_surge:
            interpretation = (
                f"Concentration surge in {area} for '{category}'. Prompt for field verification; "
                f"evaluates repeat infrastructure failure or duplicate reporting without inferring fault."
            )
            hotspot_signals.append({
                "area": area,
                "category": category,
                "current_count": curr_count,
                "baseline_count": base_count,
                "increase": increase,
                "rule": why_flagged,
                "data_label": data_label,
                "interpretation": interpretation,
                "complaint_ids": grp["id"].tolist(),
            })

    # Sort descending by current count
    hotspot_signals.sort(key=lambda x: x["current_count"], reverse=True)

    return {
        "status": "ok",
        "data_label": data_label,
        "current_count": current_total,
        "areas_flagged": len(hotspot_signals),
        "window_days": window_days,
        "rule": rule_desc,
        "hotspots": hotspot_signals,
        "current_start": current_start.strftime("%Y-%m-%d"),
        "current_end": current_end.strftime("%Y-%m-%d"),
        "comparison_start": comparison_start.strftime("%Y-%m-%d"),
        "comparison_end": comparison_end.strftime("%Y-%m-%d"),
    }