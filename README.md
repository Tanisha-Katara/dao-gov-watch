# dao-gov-watch

Scans the top DAOs' Discourse forums every 30 minutes for posts where a DAO is actively soliciting governance / tokenomics / research help, and publishes them to a dashboard you bookmark.

**What counts as a hit:** RFPs, open grant rounds, contractor hires, explicit "we need X help" asks with a way to respond. Not: routine votes, delegate posts, analysis, retrospectives.

**How the filter works:** two stages.
1. Cheap regex pre-filter (governance vocabulary × call-to-action vocabulary).
2. Gemini 2.5 Flash classifier with a strict rubric. Only `is_opportunity=true` AND `confidence >= 0.7` get published.

Total cost: $0 (GitHub Actions free tier + Gemini Free tier; ~25 LLM calls/day against a 1,500/day quota).

## First-time setup

### 1. Rotate your Gemini key
If the key was ever pasted in chat, an email, or a commit, rotate it at <https://aistudio.google.com/app/apikey>. Delete the old one, generate a new one.

### 2. Store the key — locally

```bash
# Stores in macOS Keychain under service name 'gemini-api-key'.
security add-generic-password -a "$USER" -s gemini-api-key -w <your-new-key>
```

The scripts look up Keychain first, then `GEMINI_API_KEY` env var. The raw key is never written to any file in this repo.

### 3. Store the key — GitHub Actions
In the GitHub repo: **Settings → Secrets and variables → Actions → New repository secret**
- Name: `GEMINI_API_KEY`
- Value: paste the new key

### 4. Enable GitHub Pages
**Settings → Pages → Source: Deploy from branch → Branch: `master` / `/ (root)` → Save.**

Your dashboard will live at `https://<username>.github.io/<repo>/dashboard.html` within a few minutes of the first successful run. Bookmark it.

### 5. First run
Push the repo. The first scheduled run (or click **Actions → Monitor DAO Forums → Run workflow**) bootstraps `state.json` with the current newest-post IDs for each forum and emits zero hits. Subsequent runs start classifying new posts only — you won't get a backlog spam.

## Local development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Bootstrap / test a single cycle (uses Keychain key if stored, else GEMINI_API_KEY env)
python monitor_governance_posts.py
python render_dashboard.py

# Open the dashboard
open dashboard.html
```

Try the classifier directly:

```bash
python classifier.py "Arbitrum" "Looking for governance consultants" "We are opening an RFP for a delegation framework review. Budget $50k. Reply in thread by May 30."
```

## Known forum limitations

- **Curve** (`gov.curve.finance`) and **Rocket Pool** (`dao.rocketpool.net`) sit behind Cloudflare bot protection that requires a JS-capable browser. The lightweight `urllib`-based fetcher is blocked with 403. Re-add to `daos.json` if you later add a headless-browser fetch path (e.g. Playwright) — for now they're excluded.

## Adding a DAO

Append to `daos.json`:

```json
{ "name": "NewDAO", "forum_url": "https://forum.newdao.org" }
```

Any forum running Discourse exposes `/posts.json` — that's the only requirement. On next run the new forum will bootstrap (record last-seen, no alerts) and then start contributing.

## Tuning

- **Too many false positives?** Open `keywords.json` and tighten the `ask_surface` patterns — require more specific phrasing. Or add rejecting examples to `FEW_SHOTS` in `classifier.py`.
- **Missing real opportunities?** Loosen `gov_surface` / `ask_surface` regex, or lower `CONFIDENCE_THRESHOLD` in `monitor_governance_posts.py` (default 0.7).
- **Want more context per post?** Bump `EXCERPT_MAX_CHARS` in `monitor_governance_posts.py`.

## Files

| File | Role |
|---|---|
| `daos.json` | Curated list of DAO name → Discourse forum URL |
| `keywords.json` | Regex pre-filter (gov vocabulary × ask vocabulary, AND) |
| `classifier.py` | Gemini intent classifier with rubric + few-shots |
| `monitor_governance_posts.py` | Orchestrator (fetch → prefilter → classify → append) |
| `render_dashboard.py` | Renders `opportunities.json` → `dashboard.html` |
| `state.json` | Per-forum `last_seen_post_id` (auto-managed) |
| `opportunities.json` | Append-only log of qualified hits (auto-managed) |
| `dashboard.html` | Generated dashboard (auto-managed) |
| `.github/workflows/monitor.yml` | GitHub Actions cron |

## Security notes

- `.gitignore` excludes `.env*` files.
- The raw API key never touches a file in this repo. Local: Keychain. CI: GitHub Secrets (auto-redacted in logs).
- No code path prints, logs, or commits the key.
- If you suspect leakage: rotate at <https://aistudio.google.com/app/apikey>, update Keychain + GitHub Secret.
