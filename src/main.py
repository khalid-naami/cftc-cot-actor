import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List
from apify import Actor

from .scraper import fetch_and_parse_cftc_reports
from .metrics import calculate_orderflow_rankings


async def main() -> None:
    async with Actor:
        # 1. Retrieve user configuration from Apify Store / API / MCP
        actor_input: Dict[str, Any] = await Actor.get_input() or {}

        current_year = datetime.now(timezone.utc).year
        default_years = [current_year - 1, current_year]

        selected_assets = actor_input.get("assets") or ["ALL"]
        selected_categories = actor_input.get("categories") or ["ALL"]
        raw_years = actor_input.get("years")
        if isinstance(raw_years, list) and raw_years:
            selected_years: List[Any] = raw_years
        elif isinstance(raw_years, (int, str)) and str(raw_years).strip():
            selected_years = [raw_years]
        else:
            selected_years = default_years
        include_rankings = bool(actor_input.get("includeRankings", True))
        output_format = actor_input.get("outputFormat", "flat_records")
        proxy_input = actor_input.get("proxyConfiguration")

        proxy_config = None
        if isinstance(proxy_input, dict) and (proxy_input.get("useApifyProxy") or proxy_input.get("proxyUrls")):
            try:
                proxy_config = await Actor.create_proxy_configuration(actor_proxy_input=proxy_input)
                if proxy_config:
                    Actor.log.info("Configured Apify Proxy for CFTC downloads.")
            except Exception as exc:
                Actor.log.warning(
                    f"Could not initialize Apify Proxy ({exc}); continuing with direct + Public Reporting API fallback."
                )

        Actor.log.info(
            f"Starting CFTC COT Actor with parameters: assets={selected_assets}, "
            f"categories={selected_categories}, years={selected_years}"
        )

        # 2. Scrape and process CFTC reports
        scrape_result = await fetch_and_parse_cftc_reports(
            years=selected_years,
            selected_assets=selected_assets,
            selected_categories=selected_categories,
            proxy_config=proxy_config,
        )
        asset_dfs = scrape_result["asset_dfs"]
        flat_records = scrape_result["flat_records"]
        years_processed = scrape_result["years_processed"]
        data_sources = scrape_result["data_sources"]
        failed_years = scrape_result["failed_years"]
        empty_reason = scrape_result["empty_reason"]

        if not flat_records:
            if empty_reason == "UPSTREAM_FETCH_FAILED":
                msg = (
                    "Failed to retrieve CFTC COT data from both CFTC.gov ZIP archives and the "
                    "official CFTC Public Reporting API."
                )
                Actor.log.error(f"{msg} Errors by year: {failed_years}")
                await Actor.set_value(
                    "OUTPUT",
                    {
                        "status": "FAILED",
                        "error_code": "UPSTREAM_FETCH_FAILED",
                        "message": msg,
                        "failed_years": {str(k): v for k, v in failed_years.items()},
                        "total_records": 0,
                        "assets_count": 0,
                        "years_processed": [],
                        "data_sources": data_sources,
                        "rankings_included": False,
                    },
                )
                Actor.status_message = msg
                Actor.exit_code = 1
                return

            msg = f"No CFTC COT records extracted ({empty_reason or 'NO_MATCHING_RECORDS'}). Verify input filters and years."
            Actor.log.warning(msg)
            await Actor.set_value(
                "OUTPUT",
                {
                    "status": "NO_DATA",
                    "error_code": empty_reason or "NO_MATCHING_RECORDS",
                    "message": msg,
                    "total_records": 0,
                    "assets_count": 0,
                    "years_processed": years_processed,
                    "data_sources": data_sources,
                    "rankings_included": False,
                },
            )
            Actor.status_message = msg
            return

        Actor.log.info(
            f"Successfully processed {len(flat_records)} total weekly report records "
            f"across {len(asset_dfs)} assets (sources={data_sources})."
        )

        # 3. Calculate smart money orderflow rankings if requested
        if include_rankings and asset_dfs:
            Actor.log.info("Computing Smart Money Orderflow Rankings...")
            rankings_data = calculate_orderflow_rankings(asset_dfs)

            # Save the latest rankings overview to Key-Value Store for instant dashboard/AI retrieval
            await Actor.set_value("ORDERFLOW_RANKINGS", rankings_data)
            Actor.log.info("Saved ORDERFLOW_RANKINGS into Key-Value Store.")

        # 4. Push results to Apify Dataset
        if output_format == "aggregated_by_asset":
            aggregated = []
            for asset_name, df in asset_dfs.items():
                records = [
                    {
                        "Date": str(r["Date"]),
                        "ISO_Date": str(r["ISO_Date"]),
                        "Asset": str(r["Asset"]),
                        "Category": str(r["Category"]),
                        "Contract_Code": str(r["Contract_Code"]),
                        "Long": int(r["Long"]),
                        "Short": int(r["Short"]),
                        "Change long": int(r["Change long"]),
                        "Change short": int(r["Change short"]),
                        "Net position": int(r["Net position"]),
                        "url_report": str(r["url_report"]),
                    }
                    for _, r in df.iterrows()
                ]
                aggregated.append({
                    "asset": asset_name,
                    "category": records[0]["Category"] if records else "other",
                    "latest_date": records[0]["Date"] if records else "",
                    "total_reports": len(records),
                    "history": records,
                })
            await Actor.push_data(aggregated)
            Actor.log.info(f"Pushed {len(aggregated)} aggregated asset datasets to Apify Dataset.")
        else:
            await Actor.push_data(flat_records)
            Actor.log.info(f"Pushed {len(flat_records)} flat records to Apify Dataset.")

        # Store run summary in Key-Value store
        summary: Dict[str, Any] = {
            "status": "SUCCESS",
            "total_records": len(flat_records),
            "assets_count": len(asset_dfs),
            "years_processed": years_processed,
            "data_sources": data_sources,
            "rankings_included": include_rankings,
        }
        if failed_years:
            summary["partial_failures"] = {str(k): v for k, v in failed_years.items()}

        await Actor.set_value("OUTPUT", summary)
        Actor.status_message = (
            f"Extracted {len(flat_records)} CFTC COT weekly records across {len(asset_dfs)} assets "
            f"(years: {years_processed})."
        )
        Actor.log.info("Actor execution completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
