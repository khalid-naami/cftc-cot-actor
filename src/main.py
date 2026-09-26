import asyncio
from datetime import datetime
from typing import Dict, Any
from apify import Actor

from .scraper import download_and_parse_cftc_reports
from .metrics import calculate_orderflow_rankings


async def main() -> None:
    async with Actor:
        # 1. Retrieve user configuration from Apify Store / API / MCP
        actor_input: Dict[str, Any] = await Actor.get_input() or {}
        
        current_year = datetime.now().year
        default_years = [current_year - 1, current_year]
        
        selected_assets = actor_input.get("assets", ["ALL"])
        selected_categories = actor_input.get("categories", ["ALL"])
        selected_years = actor_input.get("years") or default_years
        include_rankings = actor_input.get("includeRankings", True)
        output_format = actor_input.get("outputFormat", "flat_records")

        Actor.log.info(f"Starting CFTC COT Actor with parameters: assets={selected_assets}, categories={selected_categories}, years={selected_years}")

        # 2. Scrape and process CFTC reports
        asset_dfs, flat_records = await download_and_parse_cftc_reports(
            years=selected_years,
            selected_assets=selected_assets,
            selected_categories=selected_categories
        )

        if not flat_records:
            Actor.log.warning("No records extracted. Please verify input parameters.")
            return

        Actor.log.info(f"Successfully processed {len(flat_records)} total weekly report records across {len(asset_dfs)} assets.")

        # 3. Calculate smart money orderflow rankings if requested
        rankings_data = {}
        if include_rankings and asset_dfs:
            Actor.log.info("Computing Smart Money Orderflow Rankings...")
            rankings_data = calculate_orderflow_rankings(asset_dfs)
            
            # Save the latest rankings overview to Key-Value Store for instant dashboard/AI retrieval
            await Actor.set_value("ORDERFLOW_RANKINGS", rankings_data)
            Actor.log.info("Saved ORDERFLOW_RANKINGS into Key-Value Store.")

        # 4. Push results to Apify Dataset
        if output_format == "aggregated_by_asset":
            # Push aggregated summary per asset
            aggregated = []
            for asset_name, df in asset_dfs.items():
                records = df.to_dict(orient="records")
                aggregated.append({
                    "asset": asset_name,
                    "category": records[0]["Category"] if records else "other",
                    "latest_date": records[0]["Date"] if records else "",
                    "total_reports": len(records),
                    "history": records
                })
            await Actor.push_data(aggregated)
            Actor.log.info(f"Pushed {len(aggregated)} aggregated asset datasets to Apify Dataset.")
        else:
            # Push flat records (standard tabular format for CSV / Excel / JSON)
            await Actor.push_data(flat_records)
            Actor.log.info(f"Pushed {len(flat_records)} flat records to Apify Dataset.")

        # Store run summary in Key-Value store
        summary = {
            "status": "SUCCESS",
            "total_records": len(flat_records),
            "assets_count": len(asset_dfs),
            "years_processed": selected_years,
            "rankings_included": include_rankings
        }
        await Actor.set_value("OUTPUT", summary)
        Actor.log.info("Actor execution completed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
