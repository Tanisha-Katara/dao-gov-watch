"""Render opportunities.json into a single-file dashboard.html.

Inlined CSS, tiny vanilla-JS filters, reverse-chronological table. Designed
to be bookmarked and checked once a day.
"""

from __future__ import annotations

import html
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


BASE = Path(__file__).parent

TYPE_LABELS = {
    "rfp": "RFP",
    "grant": "Grant",
    "hire": "Hire",
    "advisory_request": "Advisory",
    "other": "Other",
}


def load_opportunities() -> list[dict]:
    path = BASE / "opportunities.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def bucket_counts(items: list[dict], now: datetime) -> tuple[int, int, int]:
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    week = sum(1 for i in items if parse_ts(i["ts"]) >= week_ago)
    month = sum(1 for i in items if parse_ts(i["ts"]) >= month_ago)
    return week, month, len(items)


def is_new(ts: str, now: datetime) -> bool:
    return parse_ts(ts) >= now - timedelta(hours=24)


CSS = """
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0;background:#f6f8fa;color:#24292f;line-height:1.5}
.wrap{max-width:1280px;margin:0 auto;padding:24px}
h1{font-size:22px;margin:0 0 4px 0}
.sub{color:#57606a;font-size:13px;margin-bottom:20px}
.stats{display:flex;gap:12px;margin-bottom:16px;flex-wrap:wrap}
.stat{background:#fff;border:1px solid #d0d7de;border-radius:6px;padding:10px 14px;min-width:110px}
.stat .n{font-size:22px;font-weight:600}
.stat .l{font-size:11px;color:#57606a;text-transform:uppercase;letter-spacing:0.04em}
.filters{background:#fff;border:1px solid #d0d7de;border-radius:6px;padding:12px;margin-bottom:12px;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
.filters label{font-size:12px;color:#57606a;display:flex;flex-direction:column;gap:4px}
.filters select,.filters input[type=range]{font-size:13px}
.filters .conf-val{font-weight:600;color:#24292f;margin-left:6px}
table{width:100%;border-collapse:separate;border-spacing:0;background:#fff;border:1px solid #d0d7de;border-radius:6px;overflow:hidden;font-size:13px}
thead th{background:#f6f8fa;text-align:left;padding:8px 10px;border-bottom:1px solid #d0d7de;font-weight:600;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;color:#57606a}
tbody td{padding:10px;border-bottom:1px solid #eaeef2;vertical-align:top}
tbody tr:last-child td{border-bottom:none}
tr.new td{background:#fff8e1}
.badge{display:inline-block;padding:2px 7px;border-radius:10px;font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:0.04em}
.badge-new{background:#ff7b00;color:#fff;margin-left:6px}
.type{padding:2px 7px;border-radius:10px;font-size:11px;font-weight:500}
.type-rfp{background:#dbeafe;color:#1e40af}
.type-grant{background:#dcfce7;color:#166534}
.type-hire{background:#fef3c7;color:#92400e}
.type-advisory_request{background:#f3e8ff;color:#6b21a8}
.type-other{background:#f1f5f9;color:#475569}
.dao{font-weight:600;color:#0969da}
.title{color:#24292f;text-decoration:none}
.title:hover{text-decoration:underline}
.cta{color:#24292f;font-size:13px;max-width:320px}
.reason{color:#57606a;font-size:12px;max-width:280px;font-style:italic}
.conf{font-variant-numeric:tabular-nums;color:#57606a;font-size:12px}
.empty{background:#fff;border:1px solid #d0d7de;border-radius:6px;padding:40px;text-align:center;color:#57606a}
.footer{margin-top:16px;font-size:12px;color:#57606a;text-align:center}
"""


JS = """
const rows = Array.from(document.querySelectorAll('tbody tr'));
const daoSel = document.getElementById('f-dao');
const typeSel = document.getElementById('f-type');
const confRange = document.getElementById('f-conf');
const confVal = document.getElementById('f-conf-val');

function apply() {
  const dao = daoSel.value;
  const type = typeSel.value;
  const minConf = parseFloat(confRange.value);
  confVal.textContent = minConf.toFixed(2);
  let visible = 0;
  for (const r of rows) {
    const matchDao = dao === '' || r.dataset.dao === dao;
    const matchType = type === '' || r.dataset.type === type;
    const matchConf = parseFloat(r.dataset.conf) >= minConf;
    const show = matchDao && matchType && matchConf;
    r.style.display = show ? '' : 'none';
    if (show) visible++;
  }
  document.getElementById('visible-count').textContent = visible;
}
daoSel.addEventListener('change', apply);
typeSel.addEventListener('change', apply);
confRange.addEventListener('input', apply);
apply();
"""


def render(items: list[dict]) -> str:
    now = datetime.now(timezone.utc)
    week, month, total = bucket_counts(items, now)

    items_sorted = sorted(items, key=lambda i: i["ts"], reverse=True)
    daos_in_data = sorted({i["dao"] for i in items})
    types_in_data = sorted({i["opportunity_type"] for i in items})

    updated = now.strftime("%Y-%m-%d %H:%M UTC")

    if not items_sorted:
        body = '<div class="empty">No opportunities yet. The tool just started watching — new ones will appear here as they post.</div>'
    else:
        dao_opts = '<option value="">All DAOs</option>' + "".join(
            f'<option value="{html.escape(d)}">{html.escape(d)}</option>' for d in daos_in_data
        )
        type_opts = '<option value="">All types</option>' + "".join(
            f'<option value="{html.escape(t)}">{html.escape(TYPE_LABELS.get(t, t))}</option>' for t in types_in_data
        )
        rows_html = []
        for i in items_sorted:
            dt = parse_ts(i["ts"])
            new_class = " new" if is_new(i["ts"], now) else ""
            new_badge = '<span class="badge badge-new">NEW</span>' if is_new(i["ts"], now) else ""
            otype = i["opportunity_type"]
            rows_html.append(
                f'<tr class="row{new_class}" '
                f'data-dao="{html.escape(i["dao"])}" '
                f'data-type="{html.escape(otype)}" '
                f'data-conf="{i["confidence"]}">'
                f'<td>{fmt_date(dt)}</td>'
                f'<td class="dao">{html.escape(i["dao"])}</td>'
                f'<td><span class="type type-{html.escape(otype)}">{html.escape(TYPE_LABELS.get(otype, otype))}</span></td>'
                f'<td><a class="title" href="{html.escape(i["post_url"])}" target="_blank" rel="noopener">{html.escape(i["title"])}</a>{new_badge}</td>'
                f'<td class="cta">{html.escape(i["call_to_action"])}</td>'
                f'<td class="conf">{i["confidence"]:.2f}</td>'
                f'<td class="reason">{html.escape(i["one_line_reason"])}</td>'
                f'</tr>'
            )
        body = f"""
<div class="filters">
  <label>DAO<select id="f-dao">{dao_opts}</select></label>
  <label>Type<select id="f-type">{type_opts}</select></label>
  <label>Min confidence <span class="conf-val" id="f-conf-val">0.70</span>
    <input type="range" id="f-conf" min="0" max="1" step="0.05" value="0.70"></label>
  <span style="margin-left:auto;font-size:12px;color:#57606a"><span id="visible-count">0</span> shown</span>
</div>
<table>
  <thead><tr>
    <th>Date</th><th>DAO</th><th>Type</th><th>Title</th>
    <th>Call to action</th><th>Conf.</th><th>Why flagged</th>
  </tr></thead>
  <tbody>{''.join(rows_html)}</tbody>
</table>"""

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>DAO Gov Watch</title>
<style>{CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>DAO Gov Watch</h1>
  <div class="sub">Real calls-for-help from top DAO governance forums. Updated {updated}.</div>
  <div class="stats">
    <div class="stat"><div class="n">{week}</div><div class="l">This week</div></div>
    <div class="stat"><div class="n">{month}</div><div class="l">Last 30 days</div></div>
    <div class="stat"><div class="n">{total}</div><div class="l">All-time</div></div>
  </div>
  {body}
  <div class="footer">
    Source: public Discourse forums of {len(daos_in_data) or '—'} DAOs. Generated by dao-gov-watch.
  </div>
</div>
<script>{JS}</script>
</body>
</html>
"""


def main() -> int:
    items = load_opportunities()
    html_text = render(items)
    (BASE / "dashboard.html").write_text(html_text)
    print(f"Wrote dashboard.html ({len(items)} opportunities)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
