# Startup Tracker

Automatically monitor startups for configurable-period signals — funding rounds, product launches, partnerships, leadership hires, and other notable developments — across news sources, websites, and social media.

Designed for VC investors, analysts, and researchers tracking early-stage companies.

## Quick Start

### 1. Install Dependencies

```bash
# Python
pip install -r requirements.txt

# Node.js (required for social media monitoring)
npm install -g @apify/mcpc
```

### 2. Configure API Keys

Create a `.env` file in the skill directory:

```
TAVILY_API_KEY=your_key_here
APIFY_TOKEN=your_token_here
```

- Get a Tavily key at https://tavily.com
- Get an Apify token at https://console.apify.com

### 3. Add Companies

When you first run the tracker, you'll be prompted to enter the list of companies to monitor. The config is saved to `config.json` automatically.

Or manually edit `config.json`:

```json
{
  "companies": [
    {
      "name": "Example AI",
      "website": "https://example-ai.com",
      "x_handle": "ExampleAI",
      "linkedin_url": "https://www.linkedin.com/company/example-ai/"
    }
  ]
}
```

### 4. Run

```bash
python tracker.py
```

### 5. Via Claude Code Skill

```
/startup-tracker
```

## Data Sources

| Source | Purpose | Required |
|--------|---------|----------|
| Tavily CLI | News / funding search | Yes |
| Crawl4AI | Website change monitoring | Recommended |
| Apify Actors | Twitter & LinkedIn posts | Recommended |

## API Key Loading Priority

1. Command line arguments (`--tavily-key`, `--apify-key`)
2. `.env` environment variables (`TAVILY_API_KEY`, `APIFY_TOKEN`)
3. `config.json` → `api_keys` field

## First Run Note

On the first run, Crawl4AI will establish a baseline hash for each monitored website. No change alerts will be produced on the first run — detection starts on the second run.

## License

MIT
