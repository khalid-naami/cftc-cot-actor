"""
Asynchronous CFTC COT data downloader and parser with browser TLS impersonation,
Apify Proxy support, and official CFTC Public Reporting Open Data API fallback.
"""
import asyncio
from http.cookiejar import CookieJar
import io
import json
import re
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse
import zipfile

import httpx
import pandas as pd
from apify import Actor

try:
    import impit
    HAS_IMPIT = True
except ImportError:
    impit = None  # type: ignore[assignment]
    HAS_IMPIT = False

from .constants import ALL_ASSETS

CFTC_HIST_URL_TEMPLATE = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"
CFTC_REFERER_URL = "https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalViewable/index.htm"
CFTC_SOCRATA_RESOURCE_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"

BROWSER_FALLBACK_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Upgrade-Insecure-Requests": "1",
}

CANONICAL_COLUMNS = [
    "CFTC Contract Market Code",
    "As of Date in Form YYYY-MM-DD",
    "Noncommercial Positions-Long (All)",
    "Noncommercial Positions-Short (All)",
    "Change in Noncommercial-Long (All)",
    "Change in Noncommercial-Short (All)",
]


def _normalize_years(years: Optional[List[Any]]) -> List[int]:
    if not years:
        return []
    seen = set()
    cleaned: List[int] = []
    for raw in years:
        try:
            year_int = int(str(raw).strip())
        except (ValueError, TypeError):
            Actor.log.warning(f"Ignoring invalid year value: {raw!r}")
            continue
        if 1986 <= year_int <= 2100 and year_int not in seen:
            seen.add(year_int)
            cleaned.append(year_int)
        elif year_int not in seen:
            Actor.log.warning(f"Year {year_int} is out of valid CFTC COT range (1986-2100); skipping.")
    return cleaned


def _filter_assets(
    selected_assets: Optional[List[str]] = None,
    selected_categories: Optional[List[str]] = None,
) -> List[List[str]]:
    norm_assets = {str(a).strip().upper() for a in (selected_assets or []) if str(a).strip()}
    if not norm_assets or "ALL" in norm_assets:
        target_assets = list(ALL_ASSETS)
    else:
        target_assets = [a for a in ALL_ASSETS if a[0].upper() in norm_assets]

    norm_cats = {str(c).strip().lower() for c in (selected_categories or []) if str(c).strip()}
    if norm_cats and "all" not in norm_cats:
        target_assets = [a for a in target_assets if a[3].lower() in norm_cats]

    return target_assets


async def _http_get(
    url: str,
    *,
    headers: Optional[Dict[str, str]] = None,
    proxy_url: Optional[str] = None,
    verify: bool = True,
    timeout: float = 45.0,
    browser_impersonate: bool = True,
) -> Tuple[int, str, bytes]:
    """
    Executes an async HTTP GET preferring impit Chrome TLS impersonation when enabled,
    with clean fallback to httpx.
    Returns (status_code, lowercased_content_type, body_bytes).
    """
    if HAS_IMPIT and browser_impersonate:
        client_kwargs: Dict[str, Any] = {
            "browser": "chrome",
            "timeout": timeout,
            "verify": verify,
            "follow_redirects": True,
            "cookie_jar": CookieJar(),
        }
        if proxy_url:
            client_kwargs["proxy"] = proxy_url
        async with impit.AsyncClient(**client_kwargs) as client:  # type: ignore[union-attr]
            resp = await client.get(url, headers=headers)
            content_type = (resp.headers.get("content-type") or "").lower()
            return resp.status_code, content_type, resp.content

    merged_headers = dict(BROWSER_FALLBACK_HEADERS) if browser_impersonate else {}
    if headers:
        merged_headers.update(headers)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        proxy=proxy_url,
        verify=verify,
        headers=merged_headers or None,
    ) as client:
        resp = await client.get(url)
        content_type = (resp.headers.get("content-type") or "").lower()
        return resp.status_code, content_type, resp.content


def _parse_zip_bytes(raw_bytes: bytes, year: int) -> Optional[pd.DataFrame]:
    if not raw_bytes or len(raw_bytes) < 4:
        return None
    # Reject HTML WAF/Cloudflare challenge pages before ZipFile raises generic errors
    head_lower = raw_bytes[:512].lstrip().lower()
    if head_lower.startswith(b"<!doctype") or head_lower.startswith(b"<html"):
        return None

    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as z:
        matched_files = [n for n in z.namelist() if n.lower().endswith(".txt") or n.lower().endswith(".csv")]
        if not matched_files:
            Actor.log.warning(f"No CSV or TXT file found inside deacot{year}.zip")
            return None

        with z.open(matched_files[0]) as f:
            df_year = pd.read_csv(f, low_memory=False)
            df_year.columns = [str(c).strip().strip('"') for c in df_year.columns]
            return df_year


async def _get_proxy_url(proxy_config: Any, session_id: str) -> Tuple[Optional[str], bool]:
    if proxy_config is None:
        return None, True
    try:
        info = await proxy_config.new_proxy_info(session_id=session_id)
        if info and info.url:
            verify_ssl = not bool(getattr(proxy_config, "is_man_in_the_middle", False))
            return info.url, verify_ssl
    except Exception as exc:
        Actor.log.warning(f"Failed to allocate proxy session ({session_id}): {exc}")
    return None, True


async def _fetch_zip_for_year(
    year: int,
    *,
    proxy_config: Any = None,
    skip_direct: bool = False,
) -> Tuple[Optional[pd.DataFrame], bool, Optional[str]]:
    """
    Attempts to download and parse deacot{year}.zip from CFTC.gov.
    Returns (df_year_or_None, direct_blocked_by_waf, error_message_or_None).
    """
    url = CFTC_HIST_URL_TEMPLATE.format(year=year)
    zip_headers = {
        "Referer": CFTC_REFERER_URL,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }

    direct_blocked = False
    last_error: Optional[str] = None

    # 1. Direct attempt with browser TLS fingerprint
    if not skip_direct:
        Actor.log.info(f"Downloading CFTC COT data for year {year} from {url}...")
        for attempt in range(2):
            try:
                status, content_type, body = await _http_get(
                    url,
                    headers=zip_headers,
                    browser_impersonate=True,
                )
                if status == 200:
                    try:
                        df_year = _parse_zip_bytes(body, year)
                        if df_year is not None:
                            return df_year, False, None
                        last_error = f"HTTP 200 non-ZIP response ({content_type or 'unknown'})"
                        if "html" in content_type or body[:128].lstrip().lower().startswith((b"<html", b"<!doctype")):
                            direct_blocked = True
                            break
                    except zipfile.BadZipFile:
                        last_error = "Corrupt or HTML-substituted ZIP payload"
                elif status in (403, 429):
                    last_error = f"HTTP Status {status}"
                    direct_blocked = True
                    Actor.log.warning(f"CFTC.gov blocked ZIP download for {year} ({last_error}).")
                    break
                else:
                    last_error = f"HTTP Status {status}"
                    if status == 404:
                        break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"

            if attempt == 0:
                await asyncio.sleep(1.5)

    # 2. Proxy retry if proxy_config is configured
    if proxy_config is not None:
        for attempt in range(1, 3):
            proxy_url, verify_ssl = await _get_proxy_url(proxy_config, f"cftc_{year}_{attempt}")
            if not proxy_url:
                break
            Actor.log.info(f"Retrying CFTC.gov ZIP download for year {year} via Apify Proxy (attempt {attempt})...")
            try:
                status, content_type, body = await _http_get(
                    url,
                    headers=zip_headers,
                    proxy_url=proxy_url,
                    verify=verify_ssl,
                    browser_impersonate=True,
                )
                if status == 200:
                    try:
                        df_year = _parse_zip_bytes(body, year)
                        if df_year is not None:
                            return df_year, direct_blocked, None
                        last_error = f"Proxy HTTP 200 non-ZIP response ({content_type or 'unknown'})"
                    except zipfile.BadZipFile:
                        last_error = "Proxy returned invalid ZIP payload"
                else:
                    last_error = f"Proxy HTTP Status {status}"
            except Exception as exc:
                last_error = f"Proxy {type(exc).__name__}: {exc}"
            await asyncio.sleep(1.5)

    if last_error and not skip_direct and not direct_blocked:
        Actor.log.warning(f"Failed to download CFTC ZIP for {year} ({last_error})")

    return None, direct_blocked, last_error


async def _fetch_socrata_for_year(
    year: int,
    contract_codes: List[str],
    *,
    proxy_config: Any = None,
) -> Tuple[Optional[pd.DataFrame], bool, Optional[str]]:
    """
    Fetches official CFTC Legacy Futures-Only COT data for `year` from
    CFTC's Socrata Open Data API (publicreporting.cftc.gov/resource/6dca-aqww.json).
    Returns (df_year_or_None, request_succeeded, error_message_or_None).
    """
    safe_codes = [re.sub(r"[^A-Za-z0-9]", "", code) for code in contract_codes]
    safe_codes = [c for c in safe_codes if c]

    clauses = [
        f"report_date_as_yyyy_mm_dd >= '{year}-01-01T00:00:00'",
        f"report_date_as_yyyy_mm_dd < '{year + 1}-01-01T00:00:00'",
    ]
    if safe_codes:
        in_list = ",".join(f"'{c}'" for c in sorted(set(safe_codes)))
        clauses.insert(0, f"cftc_contract_market_code in ({in_list})")

    where_expr = " AND ".join(clauses)
    page_size = 5000
    offset = 0
    all_rows: List[Dict[str, Any]] = []
    api_headers = {
        "Accept": "application/json",
        "User-Agent": BROWSER_FALLBACK_HEADERS["User-Agent"],
    }

    Actor.log.info(f"Fetching CFTC COT data for year {year} via official CFTC Public Reporting API...")

    while True:
        query_str = urllib.parse.urlencode({
            "$where": where_expr,
            "$order": "report_date_as_yyyy_mm_dd DESC, cftc_contract_market_code ASC",
            "$limit": page_size,
            "$offset": offset,
        })
        url = f"{CFTC_SOCRATA_RESOURCE_URL}?{query_str}"

        batch: Optional[List[Dict[str, Any]]] = None
        last_err: Optional[str] = None

        # Try direct first, then proxy if configured
        attempts: List[Tuple[Optional[str], bool]] = [(None, True)]
        if proxy_config is not None:
            p_url, v_ssl = await _get_proxy_url(proxy_config, f"socrata_{year}_{offset}")
            if p_url:
                attempts.append((p_url, v_ssl))

        for idx, (proxy_url, verify_ssl) in enumerate(attempts):
            for retry in range(2 if idx == 0 else 1):
                try:
                    status, _, body = await _http_get(
                        url,
                        headers=api_headers,
                        proxy_url=proxy_url,
                        verify=verify_ssl,
                        timeout=45.0,
                        browser_impersonate=False,
                    )
                    if status == 200:
                        parsed = json.loads(body.decode("utf-8"))
                        if isinstance(parsed, list):
                            batch = parsed
                            break
                        last_err = f"Unexpected Socrata JSON payload type: {type(parsed).__name__}"
                    else:
                        last_err = f"HTTP Status {status}"
                        if status == 400:
                            break
                except Exception as exc:
                    last_err = f"{type(exc).__name__}: {exc}"

                if retry == 0:
                    await asyncio.sleep(1.5)

            if batch is not None:
                break

        if batch is None:
            return None, False, last_err or "Unknown Socrata API failure"

        all_rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    if not all_rows:
        return pd.DataFrame(columns=CANONICAL_COLUMNS), True, None

    normalized = [
        {
            "CFTC Contract Market Code": str(r.get("cftc_contract_market_code") or "").strip(),
            "As of Date in Form YYYY-MM-DD": str(r.get("report_date_as_yyyy_mm_dd") or "")[:10],
            "Noncommercial Positions-Long (All)": (
                0 if r.get("noncomm_positions_long_all") is None else r.get("noncomm_positions_long_all")
            ),
            "Noncommercial Positions-Short (All)": (
                0 if r.get("noncomm_positions_short_all") is None else r.get("noncomm_positions_short_all")
            ),
            "Change in Noncommercial-Long (All)": (
                0 if r.get("change_in_noncomm_long_all") is None else r.get("change_in_noncomm_long_all")
            ),
            "Change in Noncommercial-Short (All)": (
                0 if r.get("change_in_noncomm_short_all") is None else r.get("change_in_noncomm_short_all")
            ),
        }
        for r in all_rows
    ]
    df_year = pd.DataFrame(normalized)
    Actor.log.info(f"Loaded {len(df_year)} CFTC COT rows for year {year} via Public Reporting API.")
    return df_year, True, None


def _to_int_series(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.replace(",", "", regex=False).str.strip()
    numeric = pd.to_numeric(cleaned, errors="coerce").fillna(0)
    return numeric.round().astype(int)


async def fetch_and_parse_cftc_reports(
    years: List[int],
    selected_assets: Optional[List[str]] = None,
    selected_categories: Optional[List[str]] = None,
    proxy_config: Any = None,
) -> Dict[str, Any]:
    """
    Downloads and parses COT reports for requested years, assets, and categories.
    Tries CFTC.gov annual ZIP archives with Chrome TLS impersonation (+ optional proxy) first,
    and automatically falls back to the official CFTC Public Reporting Socrata Open Data API
    if CFTC.gov returns HTTP 403 or fails.
    """
    target_assets = _filter_assets(selected_assets, selected_categories)
    if not target_assets:
        Actor.log.warning(
            f"No configured assets match assets={selected_assets} and categories={selected_categories}."
        )
        return {
            "asset_dfs": {},
            "flat_records": [],
            "years_processed": [],
            "data_sources": [],
            "failed_years": {},
            "empty_reason": "EMPTY_FILTER_MATCH",
        }

    norm_years = _normalize_years(years)
    if not norm_years:
        Actor.log.warning(f"No valid years supplied in input: {years}")
        return {
            "asset_dfs": {},
            "flat_records": [],
            "years_processed": [],
            "data_sources": [],
            "failed_years": {},
            "empty_reason": "NO_VALID_YEARS",
        }

    contract_codes = [a[1] for a in target_assets]
    all_years_dfs: List[pd.DataFrame] = []
    years_processed: List[int] = []
    sources_used = set()
    failed_years: Dict[int, str] = {}
    succeeded_fetches = 0
    zip_blocked_without_proxy = False

    for year in norm_years:
        skip_zip = zip_blocked_without_proxy and proxy_config is None
        df_year, direct_blocked, zip_error = await _fetch_zip_for_year(
            year,
            proxy_config=proxy_config,
            skip_direct=skip_zip,
        )

        if df_year is not None:
            Actor.log.info(f"Successfully loaded and extracted CFTC ZIP data for year {year}.")
            all_years_dfs.append(df_year)
            years_processed.append(year)
            sources_used.add("cftc_zip")
            succeeded_fetches += 1
            continue

        if direct_blocked and proxy_config is None and not zip_blocked_without_proxy:
            zip_blocked_without_proxy = True
            Actor.log.info(
                "CFTC.gov ZIP endpoint blocked datacenter IP (HTTP 403/429). "
                "Switching seamlessly to official CFTC Public Reporting API (publicreporting.cftc.gov)..."
            )

        df_api, api_ok, api_error = await _fetch_socrata_for_year(
            year,
            contract_codes,
            proxy_config=proxy_config,
        )
        if api_ok:
            succeeded_fetches += 1
            years_processed.append(year)
            sources_used.add("cftc_public_reporting_api")
            if df_api is not None and not df_api.empty:
                all_years_dfs.append(df_api)
            else:
                Actor.log.warning(f"Official CFTC Public Reporting API returned 0 rows for year {year}.")
        else:
            combined_err = f"ZIP={zip_error or 'skipped'}; API={api_error or 'failed'}"
            failed_years[year] = combined_err
            Actor.log.error(f"Failed to retrieve COT data for year {year} ({combined_err}).")

    if succeeded_fetches == 0:
        Actor.log.error("All upstream CFTC COT download sources failed.")
        return {
            "asset_dfs": {},
            "flat_records": [],
            "years_processed": [],
            "data_sources": sorted(sources_used),
            "failed_years": failed_years,
            "empty_reason": "UPSTREAM_FETCH_FAILED",
        }

    non_empty_dfs = [df for df in all_years_dfs if not df.empty]
    if not non_empty_dfs:
        Actor.log.warning("Upstream CFTC sources succeeded but returned 0 COT rows for the requested years.")
        return {
            "asset_dfs": {},
            "flat_records": [],
            "years_processed": years_processed,
            "data_sources": sorted(sources_used),
            "failed_years": failed_years,
            "empty_reason": "NO_MATCHING_RECORDS",
        }

    df_full = pd.concat(non_empty_dfs, ignore_index=True)
    if "CFTC Contract Market Code" not in df_full.columns:
        Actor.log.error("Missing 'CFTC Contract Market Code' column in downloaded CFTC dataset.")
        return {
            "asset_dfs": {},
            "flat_records": [],
            "years_processed": years_processed,
            "data_sources": sorted(sources_used),
            "failed_years": failed_years,
            "empty_reason": "INVALID_UPSTREAM_SCHEMA",
        }

    code_series = df_full["CFTC Contract Market Code"].astype(str).str.strip().str.strip('"')
    asset_dfs: Dict[str, pd.DataFrame] = {}
    flat_records: List[Dict[str, Any]] = []

    for asset in target_assets:
        name = asset[0]
        code = asset[1]
        report_url = asset[2]
        category = asset[3]

        mask = code_series.eq(code)
        if not mask.any():
            mask = code_series.str.contains(code, regex=False, na=False)
        df_asset = df_full[mask].copy()

        if df_asset.empty:
            Actor.log.warning(f"No CFTC records found for asset {name} ({code})")
            continue

        parsed_dates = pd.to_datetime(df_asset["As of Date in Form YYYY-MM-DD"], errors="coerce")
        valid_mask = parsed_dates.notna()
        if not valid_mask.any():
            Actor.log.warning(f"All date values invalid for asset {name} ({code})")
            continue

        df_asset = df_asset[valid_mask].copy()
        parsed_dates = parsed_dates[valid_mask]

        df_asset["Formatted_Date"] = parsed_dates.dt.strftime("%d/%m/%y")
        df_asset["ISO_Date"] = parsed_dates.dt.strftime("%Y-%m-%d")

        long_pos = _to_int_series(df_asset["Noncommercial Positions-Long (All)"])
        short_pos = _to_int_series(df_asset["Noncommercial Positions-Short (All)"])
        chg_long = _to_int_series(df_asset["Change in Noncommercial-Long (All)"])
        chg_short = _to_int_series(df_asset["Change in Noncommercial-Short (All)"])
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
            "url_report": report_url,
        })

        # Sort descending by ISO date and deduplicate overlapping weekly records
        res_df = res_df.sort_values(by="ISO_Date", ascending=False)
        res_df = res_df.drop_duplicates(subset=["ISO_Date"], keep="first").reset_index(drop=True)
        asset_dfs[name] = res_df

        for _, row in res_df.iterrows():
            flat_records.append({
                "date": str(row["Date"]),
                "iso_date": str(row["ISO_Date"]),
                "asset": str(row["Asset"]),
                "category": str(row["Category"]),
                "contract_code": str(row["Contract_Code"]),
                "long_positions": int(row["Long"]),
                "short_positions": int(row["Short"]),
                "change_long": int(row["Change long"]),
                "change_short": int(row["Change short"]),
                "net_position": int(row["Net position"]),
                "report_id": str(row["url_report"]),
            })

    return {
        "asset_dfs": asset_dfs,
        "flat_records": flat_records,
        "years_processed": years_processed,
        "data_sources": sorted(sources_used),
        "failed_years": failed_years,
        "empty_reason": None if flat_records else "NO_MATCHING_RECORDS",
    }


async def download_and_parse_cftc_reports(
    years: List[int],
    selected_assets: Optional[List[str]] = None,
    selected_categories: Optional[List[str]] = None,
    proxy_config: Any = None,
) -> Tuple[Dict[str, pd.DataFrame], List[Dict[str, Any]]]:
    """
    Backward-compatible wrapper returning (asset_dfs, flat_records).
    """
    result = await fetch_and_parse_cftc_reports(
        years=years,
        selected_assets=selected_assets,
        selected_categories=selected_categories,
        proxy_config=proxy_config,
    )
    return result["asset_dfs"], result["flat_records"]
