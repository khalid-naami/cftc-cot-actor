"""
Asynchronous CFTC COT data downloader and parser.
"""
import io
import zipfile
from typing import List, Dict, Tuple
import httpx
import pandas as pd
from apify import Actor

from .constants import ALL_ASSETS, ASSET_MAP


async def download_and_parse_cftc_reports(
    years: List[int],
    selected_assets: List[str] = None,
    selected_categories: List[str] = None
) -> Tuple[Dict[str, pd.DataFrame], List[Dict]]:
    """
    Downloads COT annual ZIP files from CFTC.gov and parses records for requested assets.
    """
    if not selected_assets or "ALL" in selected_assets:
        target_assets = ALL_ASSETS
    else:
        target_assets = [a for a in ALL_ASSETS if a[0] in selected_assets]

    if selected_categories and "ALL" not in selected_categories:
        target_assets = [a for a in target_assets if a[3] in selected_categories]

    all_years_dfs = []
    
    async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
        for year in years:
            url = f"https://www.cftc.gov/files/dea/history/deacot{year}.zip"
            Actor.log.info(f"Downloading CFTC COT data for year {year} from {url}...")
            try:
                response = await client.get(url)
                if response.status_code != 200:
                    Actor.log.warning(f"Failed to download {year} (HTTP Status {response.status_code})")
                    continue

                with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                    matched_files = [n for n in z.namelist() if n.endswith(".txt") or n.endswith(".csv")]
                    if not matched_files:
                        Actor.log.warning(f"No CSV or TXT file found inside deacot{year}.zip")
                        continue

                    with z.open(matched_files[0]) as f:
                        df_year = pd.read_csv(f, low_memory=False)
                        df_year.columns = [c.strip().strip('"') for c in df_year.columns]
                        all_years_dfs.append(df_year)

                Actor.log.info(f"Successfully loaded and extracted data for year {year}.")
            except Exception as e:
                Actor.log.error(f"Error while processing year {year}: {e}")

    if not all_years_dfs:
        Actor.log.error("No COT data could be downloaded from CFTC.")
        return {}, []

    df_full = pd.concat(all_years_dfs, ignore_index=True)
    asset_dfs: Dict[str, pd.DataFrame] = {}
    flat_records: List[Dict] = []

    for asset in target_assets:
        name = asset[0]
        code = asset[1]
        report_url = asset[2]
        category = asset[3]

        mask = df_full["CFTC Contract Market Code"].astype(str).str.strip().str.contains(code)
        df_asset = df_full[mask].copy()

        if df_asset.empty:
            Actor.log.warning(f"No CFTC records found for asset {name} ({code})")
            continue

        df_asset["Formatted_Date"] = pd.to_datetime(df_asset["As of Date in Form YYYY-MM-DD"]).dt.strftime("%d/%m/%y")
        df_asset["ISO_Date"] = pd.to_datetime(df_asset["As of Date in Form YYYY-MM-DD"]).dt.strftime("%Y-%m-%d")

        long_pos = pd.to_numeric(df_asset["Noncommercial Positions-Long (All)"], errors="coerce").fillna(0).astype(int)
        short_pos = pd.to_numeric(df_asset["Noncommercial Positions-Short (All)"], errors="coerce").fillna(0).astype(int)
        chg_long = pd.to_numeric(df_asset["Change in Noncommercial-Long (All)"], errors="coerce").fillna(0).astype(int)
        chg_short = pd.to_numeric(df_asset["Change in Noncommercial-Short (All)"], errors="coerce").fillna(0).astype(int)
        net_pos = long_pos - short_pos

        res_df = pd.DataFrame({
            "Date": df_asset["Formatted_Date"],
            "ISO_Date": df_asset["ISO_Date"],
            "Asset": name,
            "Category": category,
            "Contract_Code": code,
            "Long": long_pos,
            "Short": short_pos,
            "Change long": chg_long,
            "Change short": chg_short,
            "Net position": net_pos,
            "url_report": report_url
        })

        # Sort descending by date
        res_df = res_df.sort_values(by="Date", ascending=False, key=lambda x: pd.to_datetime(x, format="%d/%m/%y"))
        res_df = res_df.drop_duplicates(subset=["Date"])
        asset_dfs[name] = res_df

        # Create flat record dictionary list
        for _, row in res_df.iterrows():
            flat_records.append({
                "date": row["Date"],
                "iso_date": row["ISO_Date"],
                "asset": row["Asset"],
                "category": row["Category"],
                "contract_code": row["Contract_Code"],
                "long_positions": int(row["Long"]),
                "short_positions": int(row["Short"]),
                "change_long": int(row["Change long"]),
                "change_short": int(row["Change short"]),
                "net_position": int(row["Net position"]),
                "report_id": row["url_report"]
            })

    return asset_dfs, flat_records
