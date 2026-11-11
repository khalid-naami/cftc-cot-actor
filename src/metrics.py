"""
Smart Money Orderflow & Ranking Metrics Calculation.
"""
import math
from typing import Any, Dict, Optional, Tuple
import pandas as pd


def _safe_float(val: Any) -> float:
    try:
        num = float(val)
        return 0.0 if (math.isnan(num) or math.isinf(num)) else num
    except (ValueError, TypeError):
        return 0.0


def calculate_orderflow_rankings(asset_dfs: Dict[str, pd.DataFrame]) -> Dict[str, Any]:
    """
    Calculates institutional orderflow injection rankings across all assets.
    Compares the latest weekly change against historical injections.
    """
    long_rankings = []
    short_rankings = []

    for asset_name, df in asset_dfs.items():
        if df.empty:
            continue

        rank, value, latest_date = _compute_asset_rank(df)
        if rank is None:
            continue

        item = {
            "asset": asset_name,
            "rank": rank,
            "orderflow_value": int(round(value)),
            "date": latest_date,
            "direction": "LONG" if value >= 0 else "SHORT"
        }

        if value >= 0:
            long_rankings.append(item)
        else:
            short_rankings.append(item)

    # Sort rankings: rank 1 is highest priority
    long_rankings.sort(key=lambda x: x["rank"])
    short_rankings.sort(key=lambda x: x["rank"])

    return {
        "bullish_rankings": long_rankings,
        "bearish_rankings": short_rankings
    }


def _compute_asset_rank(df: pd.DataFrame) -> Tuple[Optional[int], float, str]:
    """
    Internal calculation for a single asset dataframe.
    """
    list_rank_long = []
    list_rank_short = []
    last_change = 0.0
    latest_date = str(df["Date"].iloc[0]) if "Date" in df.columns and not df.empty else ""

    for row in range(len(df)):
        change_long = _safe_float(df.iloc[row]["Change long"])
        change_short = _safe_float(df.iloc[row]["Change short"])

        # Calculate gross orderflow injection
        total_long = change_long if change_long >= 0 else 0.0
        total_short = change_long if change_long < 0 else 0.0

        if change_short >= 0:
            total_short -= change_short
        else:
            total_long += abs(change_short)

        diff = total_long + total_short

        if row == 0:
            last_change = diff

        row_date = df.iloc[row]["Date"] if "Date" in df.columns else ""
        if diff >= 0:
            list_rank_long.append((diff, row, row_date))
        else:
            list_rank_short.append((diff, row, row_date))

    list_rank_long.sort(reverse=True, key=lambda x: x[0])
    list_rank_short.sort(key=lambda x: x[0])

    if last_change >= 0:
        for idx, item in enumerate(list_rank_long):
            if item[1] == 0:
                return idx + 1, last_change, latest_date
    else:
        for idx, item in enumerate(list_rank_short):
            if item[1] == 0:
                return idx + 1, last_change, latest_date

    return None, 0.0, latest_date
