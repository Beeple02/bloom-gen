# Bloomberg Labs — Daily Report Generator

A private webapp for Bloomberg Labs on DemocracyCraft. Pulls live data from
the Atlas Market API and streams a styled Bloomberg-style report via Claude.

## Files

```
main.py          — Single-file Flask app (all HTML inline)
requirements.txt — Python dependencies
railway.toml     — Railway deployment config
```

## Deploy to Railway

1. Push these 3 files to a GitHub repo (or use Railway CLI).
2. In Railway: **New Project → Deploy from GitHub**.
3. Set environment variables in Railway dashboard:

| Variable           | Value                                      |
|--------------------|--------------------------------------------|
| `ATLAS_URL`        | Your Atlas API base URL (no trailing slash)|
| `ATLAS_KEY`        | `atl_Bloom_mkt_reports_MKZaifOWZHoAlDSWYWBaGCtUfFxx5Fvd` |
| `ANTHROPIC_API_KEY`| Your Anthropic API key                     |

4. Railway auto-detects Python/Flask via nixpacks. Deploy will bind `PORT` automatically.

## Local Dev

```bash
pip install -r requirements.txt
export ATLAS_URL=https://your-atlas-url.com
export ATLAS_KEY=atl_Bloom_mkt_reports_...
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
# Open http://localhost:5000
```

## Usage

1. Open the app URL.
2. Click **▶ GENERATE DAILY REPORT**.
3. A report window opens and streams in real-time.
4. Use **Print / Save PDF** or screenshot for Discord.

## Architecture

```
Browser → Flask /api/generate
            ↓
          fetch_atlas_data()   ← 6 Atlas endpoints in parallel
            ↓
          anthropic.stream()   ← Claude Sonnet 4, system prompt
            ↓ SSE stream
          Report window        ← live-updating HTML
```
