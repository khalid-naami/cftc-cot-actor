# CFTC Commitment of Traders (COT) Reports & Orderflow Analytics 📈

An automated, serverless **Apify Actor** that scrapes, cleans, and analyzes weekly **Commitment of Traders (COT)** reports from the official [U.S. Commodity Futures Trading Commission (CFTC)](https://www.cftc.gov/).

Designed for Forex traders, Commodity analysts, Crypto investors, and Algorithmic trading systems looking for **Smart Money Orderflow** data.

---

## 🚀 Features

- **Multi-Asset Coverage (20+ Assets):**
  - **Forex:** EUR, JPY, GBP, AUD, NZD, CAD, CHF, USD, MXN, BRL, ZAR.
  - **Cryptocurrencies:** Bitcoin (BTC), Ethereum (ETH).
  - **Metals & Energy:** Gold (GOLD), Silver (SILVER), Copper (COPPER), Crude Oil (OIL), Natural Gas (GAS).
  - **Indices:** S&P 500, NASDAQ-100, Dow Jones.
- **Smart Money Metrics:**
  - `long_positions` & `short_positions` (Non-Commercial positions).
  - `change_long` & `change_short` (Weekly position injection/unwinding).
  - `net_position` ($Long - Short$).
- **Smart Money Orderflow Rankings:** Automatically computes the weekly institutional buying and selling pressure ranking against historical data.
- **AI & MCP Ready:** Fully compatible with Claude, Cursor, and AI agents via the **Model Context Protocol (MCP)**.
- **Export Formats:** JSON, CSV, Excel, XML.

---

## 📥 Input Configuration

```json
{
  "assets": ["GOLD", "EUR", "BTC", "OIL"],
  "categories": ["ALL"],
  "years": [2025, 2026],
  "includeRankings": true,
  "outputFormat": "flat_records"
}
```

### Input Parameters:
| Field | Type | Default | Description |
|---|---|---|---|
| `assets` | Array | `["ALL"]` | List of assets to scrape or `["ALL"]`. |
| `categories` | Array | `["ALL"]` | Filter by `forex`, `crypto`, `metals`, `index`, `other`. |
| `years` | Array | `[2025, 2026]` | Years of historical reports to fetch from CFTC. |
| `includeRankings` | Boolean | `true` | Computes Smart Money Orderflow injection rankings. |
| `outputFormat` | String | `flat_records` | `flat_records` or `aggregated_by_asset`. |

---

## 📤 Output Example (Dataset)

```json
{
  "date": "24/09/25",
  "iso_date": "2025-09-24",
  "asset": "GOLD",
  "category": "metals",
  "contract_code": "088691",
  "long_positions": 284520,
  "short_positions": 41200,
  "change_long": 14200,
  "change_short": -3100,
  "net_position": 243320,
  "report_id": "deacmxsf"
}
```

---

## 🤖 Using with Apify Client in Python (e.g. Streamlit)

```python
from apify_client import ApifyClient
import pandas as pd

client = ApifyClient("YOUR_APIFY_TOKEN")

# Run the actor
run = client.actor("your-username/cftc-cot-reports-analytics").call(
    run_input={"assets": ["GOLD", "EUR", "BTC"], "years": [2025, 2026]}
)

# Fetch results as DataFrame
items = client.dataset(run["defaultDatasetId"]).list_items().items
df = pd.DataFrame(items)
print(df.head())
```

---

## 🛠️ Local Development & Testing

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Run with Apify CLI
apify run

# Or run directly with Python
python -m src.main
```
