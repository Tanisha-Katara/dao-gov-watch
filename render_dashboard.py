"""Render opportunities.json into a single-file dashboard.html.

Bookmarkable governance opportunity board with a strong empty state,
live freshness indicator, and clear historical-vs-live record labels.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional


BASE = Path(__file__).parent
REFRESH_CADENCE_MINUTES = 30
DEFAULT_MIN_CONFIDENCE = 0.70
LIVE_MODE = "live"
BACKFILL_MODE = "backfill"

TYPE_LABELS = {
    "rfp": "RFP",
    "grant": "Grant",
    "hire": "Hire",
    "advisory_request": "Advisory",
    "other": "Other",
}


def load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def load_opportunities() -> list[dict]:
    return load_json(BASE / "opportunities.json", [])


def load_daos() -> list[dict]:
    return load_json(BASE / "daos.json", [])


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fmt_timestamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def item_post_ts(item: dict) -> Optional[str]:
    value = item.get("post_ts") or item.get("ts") or item.get("detected_ts")
    return value if isinstance(value, str) and value else None


def item_detected_ts(item: dict) -> Optional[str]:
    value = item.get("detected_ts") or item.get("ts") or item.get("post_ts")
    return value if isinstance(value, str) and value else None


def item_post_dt(item: dict) -> datetime:
    raw = item_post_ts(item) or item_detected_ts(item)
    if raw:
        try:
            return parse_ts(raw)
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=timezone.utc)


def item_detected_dt(item: dict) -> datetime:
    raw = item_detected_ts(item) or item_post_ts(item)
    if raw:
        try:
            return parse_ts(raw)
        except ValueError:
            pass
    return item_post_dt(item)


def item_ingest_mode(item: dict) -> str:
    raw = item.get("ingest_mode")
    if raw in {LIVE_MODE, BACKFILL_MODE}:
        return raw
    return LIVE_MODE


def next_scheduled_run(now: datetime) -> datetime:
    run = now.astimezone(timezone.utc).replace(second=0, microsecond=0)
    if run.minute < 30:
        return run.replace(minute=30)
    return (run + timedelta(hours=1)).replace(minute=0)


def bucket_counts(items: list[dict], now: datetime) -> tuple[int, int, int]:
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    week = sum(1 for item in items if item_post_dt(item) >= week_ago)
    month = sum(1 for item in items if item_post_dt(item) >= month_ago)
    return week, month, len(items)


def is_new(item: dict, now: datetime) -> bool:
    return item_post_dt(item) >= now - timedelta(hours=24)


def is_historical(item: dict) -> bool:
    return item_ingest_mode(item) == BACKFILL_MODE


def label_for_type(opportunity_type: str) -> str:
    return TYPE_LABELS.get(opportunity_type, opportunity_type.replace("_", " ").title())


def render_type_badge(opportunity_type: str) -> str:
    safe_type = html.escape(opportunity_type)
    safe_label = html.escape(label_for_type(opportunity_type))
    return f'<span class="type type-{safe_type}">{safe_label}</span>'


def render_featured(item: dict, now: datetime) -> str:
    posted_at = fmt_timestamp(item_post_dt(item))
    safe_dao = html.escape(item["dao"])
    safe_title = html.escape(item["title"])
    safe_url = html.escape(item["post_url"])
    safe_cta = html.escape(item["call_to_action"])
    safe_reason = html.escape(item["one_line_reason"])
    new_badge = '<span class="badge-new">New</span>' if is_new(item, now) else ""
    historical_badge = '<span class="badge-historical">Historical</span>' if is_historical(item) else ""

    return f"""
<section class="panel feature-card">
  <div class="section-head">
    <div>
      <div class="section-kicker">Latest Signal</div>
      <h2>Most recent opportunity</h2>
    </div>
    <span class="pill">Posted {posted_at}</span>
  </div>
  <div class="feature-meta">
    <span class="chip chip-dao">{safe_dao}</span>
    {render_type_badge(item["opportunity_type"])}
    {historical_badge}
    {new_badge}
  </div>
  <a class="feature-title" href="{safe_url}" target="_blank" rel="noopener">{safe_title}</a>
  <p class="feature-cta">{safe_cta}</p>
  <p class="feature-reason">{safe_reason}</p>
  <div class="feature-footer">
    <span class="score">Confidence {item["confidence"]:.2f}</span>
    <a class="op-link" href="{safe_url}" target="_blank" rel="noopener">Open forum post</a>
  </div>
</section>
"""


def render_card(item: dict, now: datetime) -> str:
    posted_at = fmt_timestamp(item_post_dt(item))
    safe_dao = html.escape(item["dao"])
    safe_title = html.escape(item["title"])
    safe_url = html.escape(item["post_url"])
    safe_cta = html.escape(item["call_to_action"])
    safe_reason = html.escape(item["one_line_reason"])
    classes = "op-card is-new" if is_new(item, now) else "op-card"
    new_badge = '<span class="badge-new">New</span>' if is_new(item, now) else ""
    historical_badge = '<span class="badge-historical">Historical</span>' if is_historical(item) else ""

    return f"""
<article class="{classes}" data-dao="{safe_dao}" data-type="{html.escape(item["opportunity_type"])}" data-conf="{item["confidence"]:.3f}">
  <div class="op-top">
    <span class="timestamp">Posted {posted_at}</span>
    <span class="confidence">{item["confidence"]:.2f}</span>
  </div>
  <div class="op-meta">
    <span class="chip chip-dao">{safe_dao}</span>
    {render_type_badge(item["opportunity_type"])}
    {historical_badge}
    {new_badge}
  </div>
  <a class="op-title" href="{safe_url}" target="_blank" rel="noopener">{safe_title}</a>
  <p class="op-cta">{safe_cta}</p>
  <p class="op-reason">{safe_reason}</p>
  <a class="op-link" href="{safe_url}" target="_blank" rel="noopener">Open forum post</a>
</article>
"""


CSS = """
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&family=Space+Grotesk:wght@500;700&display=swap');

:root{
  color-scheme:light;
  --bg:#f4ede3;
  --surface:rgba(255,251,244,0.82);
  --surface-strong:#fffaf2;
  --ink:#1f2430;
  --muted:#5b6472;
  --line:rgba(31,36,48,0.12);
  --accent:#b5542f;
  --accent-strong:#8a3f24;
  --accent-soft:#f5ddd0;
  --teal:#0f766e;
  --gold:#9a6b12;
  --shadow:0 18px 50px rgba(102,72,45,0.15);
  --radius-xl:28px;
  --radius-lg:22px;
  --radius-md:16px;
}
*{box-sizing:border-box}
html{
  background:
    radial-gradient(circle at top left, rgba(255,232,212,0.95), transparent 30%),
    radial-gradient(circle at top right, rgba(208,237,232,0.8), transparent 34%),
    linear-gradient(180deg, var(--bg) 0%, #f7f1e8 45%, #fdfaf5 100%);
}
body{
  margin:0;
  min-height:100vh;
  color:var(--ink);
  font-family:"IBM Plex Sans","Avenir Next","Helvetica Neue",sans-serif;
  line-height:1.6;
}
body::before{
  content:"";
  position:fixed;
  inset:0;
  pointer-events:none;
  opacity:0.4;
  background-image:
    linear-gradient(rgba(255,255,255,0.18) 1px, transparent 1px),
    linear-gradient(90deg, rgba(255,255,255,0.18) 1px, transparent 1px);
  background-size:24px 24px;
  mask-image:linear-gradient(180deg, rgba(0,0,0,0.08), transparent 82%);
}
a{color:inherit}
.wrap{max-width:1240px;margin:0 auto;padding:32px 22px 56px;position:relative}
.panel{
  background:var(--surface);
  border:1px solid var(--line);
  border-radius:var(--radius-xl);
  box-shadow:var(--shadow);
  backdrop-filter:blur(12px);
}
.hero,.info-grid,.empty-layout,.board-grid{
  display:grid;
  gap:18px;
  margin-bottom:18px;
}
.hero{grid-template-columns:minmax(0,1.5fr) minmax(280px,0.95fr)}
.info-grid{grid-template-columns:minmax(0,1.1fr) minmax(0,0.9fr)}
.empty-layout,.board-grid{grid-template-columns:minmax(0,1.35fr) minmax(280px,0.95fr)}
.hero-copy{padding:30px}
.hero-side,.section-card,.empty-state,.notes-card,.feature-card,.feed-panel{padding:24px}
.hero-side{display:grid;gap:12px;align-content:start}
.eyebrow,.section-kicker,.timestamp,.confidence,.badge-new,.badge-historical,.mini-label{
  font-family:"IBM Plex Mono","SFMono-Regular",Consolas,monospace;
}
.eyebrow,.section-kicker,.mini-label{
  font-size:11px;
  text-transform:uppercase;
  letter-spacing:0.14em;
}
.eyebrow{color:var(--accent-strong)}
h1,h2,.feature-title,.op-title{
  font-family:"Space Grotesk","Avenir Next","Helvetica Neue",sans-serif;
  letter-spacing:-0.04em;
}
h1{
  font-size:clamp(2.6rem,6vw,4.8rem);
  line-height:0.95;
  margin:12px 0 16px;
}
h2{
  font-size:clamp(1.5rem,3vw,2rem);
  line-height:1.02;
  margin:6px 0 0;
}
.sub,.feed-sub,.empty-lead,.feature-reason,.op-reason,.rules,.side-note,.footer{color:var(--muted)}
.sub{font-size:17px;max-width:58ch;margin:0}
.meta-row,.chip-row,.feature-meta,.op-meta,.feature-footer,.footer{
  display:flex;
  flex-wrap:wrap;
  gap:10px;
}
.meta-row{margin-top:22px}
.pill,.chip,.type,.score,.confidence,.badge-new,.badge-historical,.visible-counter{
  display:inline-flex;
  align-items:center;
  gap:8px;
  border-radius:999px;
}
.pill,.chip{
  padding:9px 12px;
  border:1px solid var(--line);
  background:rgba(255,255,255,0.68);
  font-size:12px;
}
.status-pill.is-fresh{
  background:rgba(15,118,110,0.1);
  color:var(--teal);
  border-color:rgba(15,118,110,0.22);
}
.status-pill.is-warning{
  background:rgba(154,107,18,0.12);
  color:var(--gold);
  border-color:rgba(154,107,18,0.2);
}
.status-pill.is-danger{
  background:rgba(181,84,47,0.12);
  color:var(--accent-strong);
  border-color:rgba(181,84,47,0.2);
}
.stat-grid,.mini-grid,.feed-grid{
  display:grid;
  gap:12px;
}
.stat-grid,.mini-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
.stat,.mini-card,.op-card{
  border:1px solid var(--line);
  background:rgba(255,255,255,0.74);
}
.stat,.mini-card{
  border-radius:var(--radius-md);
  padding:14px 16px;
}
.stat .n{
  font-family:"Space Grotesk","Avenir Next","Helvetica Neue",sans-serif;
  font-size:30px;
  line-height:1;
  margin-bottom:6px;
}
.stat .l{
  font-size:11px;
  letter-spacing:0.12em;
  text-transform:uppercase;
  color:var(--muted);
}
.side-note{
  padding:14px 16px;
  border-radius:var(--radius-md);
  border:1px solid var(--line);
  background:linear-gradient(135deg, rgba(255,255,255,0.68), rgba(255,240,227,0.9));
  font-size:14px;
}
.section-head,.feed-head,.op-top{
  display:flex;
  justify-content:space-between;
  gap:12px;
  align-items:flex-start;
}
.section-head,.feed-head{margin-bottom:18px}
.rules{margin:0;padding-left:18px}
.rules li + li{margin-top:8px}
.empty-lead,.feature-cta,.op-cta{font-size:16px}
.chip-dao{
  background:rgba(181,84,47,0.12);
  color:var(--accent-strong);
}
.type{
  padding:6px 10px;
  font-size:12px;
  font-weight:600;
  border:1px solid transparent;
}
.type-rfp{background:#d9ecff;color:#174690;border-color:rgba(23,70,144,0.12)}
.type-grant{background:#ddf8e5;color:#14643f;border-color:rgba(20,100,63,0.12)}
.type-hire{background:#fdf0d0;color:#925b00;border-color:rgba(146,91,0,0.12)}
.type-advisory_request{background:#eee5ff;color:#5b2ca0;border-color:rgba(91,44,160,0.12)}
.type-other{background:#edf2f7;color:#45556c;border-color:rgba(69,85,108,0.12)}
.badge-new{
  padding:6px 10px;
  background:var(--accent);
  color:#fff;
  font-size:11px;
  letter-spacing:0.08em;
  text-transform:uppercase;
}
.badge-historical{
  padding:6px 10px;
  background:rgba(69,85,108,0.12);
  color:#45556c;
  border:1px solid rgba(69,85,108,0.12);
  font-size:11px;
  letter-spacing:0.08em;
  text-transform:uppercase;
}
.feature-title,.op-title{
  text-decoration:none;
  font-weight:700;
}
.feature-title{
  display:block;
  font-size:clamp(1.7rem,3vw,2.35rem);
  line-height:1.04;
  margin:0 0 14px;
}
.op-title{
  font-size:1.35rem;
  line-height:1.12;
}
.feature-title:hover,.op-title:hover,.op-link:hover{text-decoration:underline}
.feature-cta,.feature-reason,.op-cta,.op-reason{margin:0}
.feature-footer{
  justify-content:space-between;
  margin-top:20px;
  align-items:center;
}
.score,.confidence{
  padding:7px 11px;
  background:var(--accent-soft);
  color:var(--accent-strong);
  font-size:12px;
}
.feed-sub{margin:8px 0 0}
.filters{
  display:grid;
  grid-template-columns:repeat(4,minmax(0,1fr));
  gap:12px;
  align-items:end;
  margin-bottom:18px;
}
.filters label{
  display:flex;
  flex-direction:column;
  gap:8px;
  font-size:12px;
  color:var(--muted);
}
.filters select,.filters input[type=range]{
  width:100%;
  font:inherit;
}
.filters select{
  appearance:none;
  border:1px solid var(--line);
  border-radius:14px;
  padding:12px 14px;
  background:rgba(255,255,255,0.9);
  color:var(--ink);
}
.filters input[type=range]{accent-color:var(--accent)}
.conf-readout{
  color:var(--accent-strong);
  font-family:"IBM Plex Mono","SFMono-Regular",Consolas,monospace;
}
.visible-counter{
  justify-content:center;
  min-height:48px;
  padding:12px 14px;
  border:1px solid var(--line);
  background:rgba(255,255,255,0.7);
  font-size:13px;
  color:var(--muted);
}
.feed-grid{grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}
.op-card{
  border-radius:var(--radius-lg);
  padding:18px;
  display:flex;
  flex-direction:column;
  gap:14px;
}
.op-card.is-new{
  background:linear-gradient(180deg, rgba(255,248,239,0.98), rgba(255,255,255,0.78));
  border-color:rgba(181,84,47,0.22);
}
.timestamp{
  font-size:12px;
  color:var(--muted);
}
.op-link{
  margin-top:auto;
  color:var(--accent-strong);
  font-weight:600;
  text-decoration:none;
}
.empty-filter{
  margin-top:16px;
  padding:18px;
  text-align:center;
  color:var(--muted);
  border:1px dashed rgba(31,36,48,0.18);
  border-radius:var(--radius-md);
  background:rgba(255,255,255,0.5);
}
.footer{
  justify-content:space-between;
  margin-top:12px;
  padding:0 6px;
  font-size:12px;
}
@media (max-width:980px){
  .hero,.info-grid,.empty-layout,.board-grid,.feed-grid{grid-template-columns:1fr}
  .filters{grid-template-columns:repeat(2,minmax(0,1fr))}
}
@media (max-width:640px){
  .wrap{padding:20px 14px 42px}
  .hero-copy,.hero-side,.section-card,.empty-state,.notes-card,.feature-card,.feed-panel{padding:20px}
  h1{font-size:clamp(2.4rem,14vw,3.8rem)}
  .stat-grid,.mini-grid,.filters,.feed-grid{grid-template-columns:1fr}
  .section-head,.feed-head,.feature-footer,.footer{flex-direction:column;align-items:flex-start}
}
"""


JS_TEMPLATE = """
const UPDATED_AT = __UPDATED_AT__;

const cards = Array.from(document.querySelectorAll('.op-card'));
const daoSel = document.getElementById('f-dao');
const typeSel = document.getElementById('f-type');
const confRange = document.getElementById('f-conf');
const confVal = document.getElementById('f-conf-val');
const visibleCount = document.getElementById('visible-count');
const emptyFilter = document.getElementById('empty-filter');
const statusPill = document.getElementById('status-pill');

function formatAge(totalMinutes) {
  if (totalMinutes < 1) return 'just now';
  if (totalMinutes < 60) return `${totalMinutes}m`;
  const hours = Math.floor(totalMinutes / 60);
  const minutes = totalMinutes % 60;
  if (totalMinutes < 1440) return minutes ? `${hours}h ${minutes}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  const remHours = hours % 24;
  return remHours ? `${days}d ${remHours}h` : `${days}d`;
}

function updateFreshness() {
  if (!statusPill) return;
  const updated = new Date(UPDATED_AT);
  if (Number.isNaN(updated.getTime())) return;

  const ageMinutes = Math.max(0, Math.round((Date.now() - updated.getTime()) / 60000));
  statusPill.classList.remove('is-fresh', 'is-warning', 'is-danger');
  statusPill.title = `Last refresh: ${updated.toLocaleString()}`;

  if (ageMinutes <= 75) {
    statusPill.textContent = `Fresh · updated ${formatAge(ageMinutes)} ago`;
    statusPill.classList.add('is-fresh');
    return;
  }

  if (ageMinutes <= 180) {
    statusPill.textContent = `Delayed · updated ${formatAge(ageMinutes)} ago`;
    statusPill.classList.add('is-warning');
    return;
  }

  statusPill.textContent = `Stale · updated ${formatAge(ageMinutes)} ago`;
  statusPill.classList.add('is-danger');
}

function applyFilters() {
  if (!daoSel || !typeSel || !confRange) return;

  const dao = daoSel.value;
  const type = typeSel.value;
  const minConf = parseFloat(confRange.value);

  if (confVal) {
    confVal.textContent = minConf.toFixed(2);
  }

  let visible = 0;
  for (const card of cards) {
    const matchDao = dao === '' || card.dataset.dao === dao;
    const matchType = type === '' || card.dataset.type === type;
    const matchConf = parseFloat(card.dataset.conf || '0') >= minConf;
    const show = matchDao && matchType && matchConf;
    card.hidden = !show;
    if (show) visible += 1;
  }

  if (visibleCount) {
    visibleCount.textContent = String(visible);
  }
  if (emptyFilter) {
    emptyFilter.hidden = visible !== 0;
  }
}

updateFreshness();
window.setInterval(updateFreshness, 60000);

if (daoSel && typeSel && confRange) {
  daoSel.addEventListener('change', applyFilters);
  typeSel.addEventListener('change', applyFilters);
  confRange.addEventListener('input', applyFilters);
  applyFilters();
}
"""


def render(items: list[dict], daos: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    updated = fmt_timestamp(now)
    updated_iso = now.astimezone(timezone.utc).isoformat()
    next_run = fmt_timestamp(next_scheduled_run(now))
    week, month, total = bucket_counts(items, now)

    items_sorted = sorted(items, key=item_post_dt, reverse=True)
    dao_names = [dao["name"] for dao in daos]
    dao_count = len(dao_names)
    coverage_chips = "".join(f'<span class="chip">{html.escape(name)}</span>' for name in dao_names) or '<span class="chip">No DAO forums configured</span>'

    if not items_sorted:
        body = f"""
<section class="empty-layout">
  <section class="panel empty-state">
    <div class="section-kicker">Queue Status</div>
    <h2>No live opportunities right now</h2>
    <p class="empty-lead">Nothing is currently published because no recent forum posts passed both the keyword screen and classifier threshold. That keeps this page quiet until there is a real signal.</p>
    <div class="mini-grid">
      <div class="mini-card">
        <span class="mini-label">Latest Refresh</span>
        <div>{updated}</div>
      </div>
      <div class="mini-card">
        <span class="mini-label">Next Scheduled Scan</span>
        <div>{next_run}</div>
      </div>
      <div class="mini-card">
        <span class="mini-label">Publishing Threshold</span>
        <div>{DEFAULT_MIN_CONFIDENCE:.2f}+ classifier confidence</div>
      </div>
      <div class="mini-card">
        <span class="mini-label">Publishing Rule</span>
        <div>Clear external ask required</div>
      </div>
    </div>
  </section>
  <section class="panel notes-card">
    <div class="section-kicker">Page Health</div>
    <h2>Quiet market or stale workflow?</h2>
    <p class="empty-lead">The freshness pill above helps you tell the difference between a calm inbox and an automation issue.</p>
    <ul class="rules">
      <li><strong>Fresh</strong> means the scheduler is landing roughly on time.</li>
      <li><strong>Delayed</strong> usually means a run is late, queued, or Pages has not published yet.</li>
      <li><strong>Stale</strong> is your cue to inspect the latest GitHub Actions run.</li>
    </ul>
  </section>
</section>
"""
    else:
        daos_in_data = sorted({item["dao"] for item in items_sorted}, key=str.lower)
        types_in_data = sorted({item["opportunity_type"] for item in items_sorted})
        dao_opts = '<option value="">All DAOs</option>' + "".join(
            f'<option value="{html.escape(name)}">{html.escape(name)}</option>' for name in daos_in_data
        )
        type_opts = '<option value="">All types</option>' + "".join(
            f'<option value="{html.escape(opportunity_type)}">{html.escape(label_for_type(opportunity_type))}</option>'
            for opportunity_type in types_in_data
        )
        featured_html = render_featured(items_sorted[0], now)
        cards_html = "".join(render_card(item, now) for item in items_sorted)

        body = f"""
<section class="board-grid">
  {featured_html}
  <section class="panel notes-card">
    <div class="section-kicker">Reading Guide</div>
    <h2>Designed for fast scanning</h2>
    <p class="empty-lead">Every published card is a post that passed both the regex screen and the Gemini classifier. Historical labels mark posts imported by the one-time backfill, while live hits will appear without that badge.</p>
    <ul class="rules">
      <li><strong>Posted</strong> shows the original forum post timestamp, not when this dashboard ingested the opportunity.</li>
      <li><strong>Historical</strong> means the opportunity was imported through the one-time 30-day backfill.</li>
      <li><strong>New</strong> marks forum posts from the last 24 hours.</li>
    </ul>
  </section>
</section>

<section class="panel feed-panel">
  <div class="feed-head">
    <div>
      <div class="section-kicker">Opportunity Feed</div>
      <h2>Scan the latest asks</h2>
      <p class="feed-sub">Reverse-chronological archive by forum post date, mixing live detections and historical imports in one feed.</p>
    </div>
  </div>
  <div class="filters">
    <label>DAO
      <select id="f-dao">{dao_opts}</select>
    </label>
    <label>Type
      <select id="f-type">{type_opts}</select>
    </label>
    <label>Min confidence <span class="conf-readout" id="f-conf-val">{DEFAULT_MIN_CONFIDENCE:.2f}</span>
      <input type="range" id="f-conf" min="0" max="1" step="0.05" value="{DEFAULT_MIN_CONFIDENCE:.2f}">
    </label>
    <div class="visible-counter"><span id="visible-count">{len(items_sorted)}</span> visible</div>
  </div>
  <div class="feed-grid">
    {cards_html}
  </div>
  <div class="empty-filter" id="empty-filter" hidden>No opportunities match your current filters.</div>
</section>
"""

    js = JS_TEMPLATE.replace("__UPDATED_AT__", json.dumps(updated_iso))

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="Bookmarkable live board for DAO governance, tokenomics, and research opportunities.">
<title>DAO Gov Watch</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <section class="hero">
    <div class="panel hero-copy">
      <div class="eyebrow">Live DAO Governance Opportunity Monitor</div>
      <h1>DAO Gov Watch</h1>
      <p class="sub">Signals for external governance, tokenomics, and research work pulled from top DAO forums. Built to be checked in under a minute.</p>
      <div class="meta-row">
        <span class="pill status-pill" id="status-pill">Checking freshness...</span>
        <span class="pill">Updated {updated}</span>
        <span class="pill">Next scheduled scan {next_run}</span>
        <span class="pill">Refreshes every {REFRESH_CADENCE_MINUTES} min</span>
        <span class="pill">Confidence {DEFAULT_MIN_CONFIDENCE:.2f}+</span>
      </div>
    </div>
    <aside class="panel hero-side">
      <div class="stat-grid">
        <div class="stat"><div class="n">{week}</div><div class="l">This week</div></div>
        <div class="stat"><div class="n">{month}</div><div class="l">Last 30 days</div></div>
        <div class="stat"><div class="n">{total}</div><div class="l">All-time</div></div>
        <div class="stat"><div class="n">{dao_count}</div><div class="l">DAOs watched</div></div>
      </div>
      <div class="side-note">Only clear external asks make it through: RFPs, grant rounds, contractor hires, and explicit requests for governance research help.</div>
    </aside>
  </section>

  <section class="info-grid">
    <section class="panel section-card">
      <div class="section-head">
        <div>
          <div class="section-kicker">Coverage</div>
          <h2>Watching {dao_count} DAO forums</h2>
        </div>
        <span class="pill">Public Discourse only</span>
      </div>
      <div class="chip-row">{coverage_chips}</div>
    </section>
    <section class="panel section-card">
      <div class="section-head">
        <div>
          <div class="section-kicker">Filter Logic</div>
          <h2>High-signal by design</h2>
        </div>
      </div>
      <ul class="rules">
        <li>RFPs, open grant rounds, contractor hires, and explicit research or tokenomics asks can qualify.</li>
        <li>Routine governance proposals, delegate intros, and general discussion posts are filtered out.</li>
        <li>Posts must show a real path to engage and clear the {DEFAULT_MIN_CONFIDENCE:.2f} confidence threshold.</li>
      </ul>
    </section>
  </section>

  {body}

  <div class="footer">
    <span>Generated from public DAO governance forums. Times are shown in UTC; freshness uses your local clock.</span>
    <span>{total} published opportunities in the archive.</span>
  </div>
</div>
<script>{js}</script>
</body>
</html>
"""


def main() -> int:
    items = load_opportunities()
    daos = load_daos()
    html_text = render(items, daos)
    (BASE / "dashboard.html").write_text(html_text)
    print(f"Wrote dashboard.html ({len(items)} opportunities)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
