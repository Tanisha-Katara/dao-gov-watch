# dao-gov-watch

Scans the top DAOs' Discourse forums every 24 hours for posts where a DAO is actively soliciting governance / tokenomics / go-to-market / research help, and publishes them to a dashboard you bookmark.

**What counts as a hit:** protocol-originated RFPs, paid advisor or research roles, scoped consulting/vendor asks, and explicit "we need outside help" posts in governance, tokenomics, go-to-market, or research with a real way to respond. Not: grant rounds, bounties, routine votes, delegate posts, analysis, retrospectives, or consultants pitching themselves to a DAO.

**How the filter works:** two stages.
1. Cheap regex pre-filter (governance vocabulary × call-to-action vocabulary).
2. Gemini 2.5 Flash classifier with a strict rubric. Only `is_opportunity=true` AND `confidence >= 0.7` get published.

Total cost: low, but Gemini quota depends on the model and whether billing is enabled. `GEMINI_MODEL` is overridable so you can adapt without code changes.

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

# One-time historical import for the last 30 days
python monitor_governance_posts.py --mode backfill --days 30
python render_dashboard.py

# Open the dashboard
open dashboard.html
```

## Historical backfill

To seed the dashboard with recent consulting opportunities, run a one-time backfill:

```bash
python monitor_governance_posts.py --mode backfill --days 30
python render_dashboard.py
```

This paginates each tracked Discourse forum, imports qualifying posts from the last 30 days, deduplicates by post URL, and preserves the live monitor cursor so future scheduled runs only pick up newer posts.

In GitHub Actions, use **Backfill DAO Forums** and keep the default `30` day window unless you want a narrower manual import.

## Forum discovery

To expand coverage without auto-changing the live watchlist, run the reviewed discovery pipeline:

```bash
python discover_forum_candidates.py
python discover_forum_candidates.py --top-n 25 --min-score 0.45
```

This uses DeFiLlama's free `/protocols` and `/overview/fees` endpoints, collapses protocol families with `forum_discovery_config.json`, validates candidate Discourse forums via `/posts.json`, and writes:

- `forum_candidates.json` for machine-readable review
- `forum_candidates.md` for a human-friendly add/review/reject report

The discovery workflow never edits `daos.json`. To approve a forum, copy the entry you want from the report into `daos.json`.

In GitHub Actions, **Discover DAO Forums** refreshes this review report every Monday at 01:00 UTC and commits only the discovery artifacts.

## Feedback loop

The dashboard now supports per-opportunity feedback:

- `Done` means the opportunity was relevant for you.
- `Not relevant` means you do not want to see more opportunities like that.

The front end stores this feedback in the browser immediately so the board can:

- hide completed opportunities from the default queue
- hide obviously irrelevant opportunities from the default queue
- reprioritize similar future opportunities using DAO, opportunity type, and text-pattern similarity

To let the backend learn from the same signals, connect the dashboard to GitHub with a fine-grained personal access token that has **Contents: Read and write** access to this repo only. The page writes your clicks into `feedback.json`, and future monitor runs read that file as a user-preference profile.

Important: GitHub Pages is static, so there is no server-side write path unless you provide that token from your browser. Without a token, the learning stays local to that browser only.

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

- **Too many false positives?** Open `keywords.json` and tighten the `ask_surface` patterns — require protocol-originated hiring language. Or add rejecting examples to `FEW_SHOTS` in `classifier.py`.
- **Missing real opportunities?** Loosen `gov_surface` / `ask_surface` regex, or lower `CONFIDENCE_THRESHOLD` in `monitor_governance_posts.py` (default 0.7).
- **Want more context per post?** Bump `EXCERPT_MAX_CHARS` in `monitor_governance_posts.py`.

## Files

| File | Role |
|---|---|
| `daos.json` | Curated list of DAO name → Discourse forum URL |
| `feedback.json` | User feedback store synced from the dashboard UI and read by backend runs |
| `feedback_profile.py` | Builds preference weights and prompt hints from `feedback.json` |
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
