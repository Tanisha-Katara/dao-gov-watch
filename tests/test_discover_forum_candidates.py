from __future__ import annotations

import json
import ssl
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

import discover_forum_candidates as discover


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class AliasAggregationTests(unittest.TestCase):
    def test_family_aliases_collapse_ethena_and_kamino(self) -> None:
        config = {
            "ignored_categories": [],
            "ignored_slugs": [],
            "family_aliases": {
                "ethena": {
                    "protocol_name": "Ethena",
                    "defillama_slugs": ["ethena-usde", "ethena-usdtb"],
                    "fee_names": ["Ethena USDe", "Ethena USDtb"],
                },
                "kamino": {
                    "protocol_name": "Kamino",
                    "defillama_slugs": ["kamino-lend", "kamino-liquidity"],
                    "fee_names": ["Kamino Lend", "Kamino Liquidity"],
                },
            },
        }
        slug_to_key, name_to_key, family_names = discover.build_alias_maps(config)
        tracked_keys = set()
        rows = [
            {"name": "Ethena USDe", "slug": "ethena-usde", "category": "Basis Trading", "tvl": 100, "url": "https://app.ethena.fi"},
            {"name": "Ethena USDtb", "slug": "ethena-usdtb", "category": "RWA", "tvl": 40, "url": "https://app.ethena.fi"},
            {"name": "Kamino Lend", "slug": "kamino-lend", "category": "Lending", "tvl": 80, "url": "https://kamino.com"},
            {"name": "Kamino Liquidity", "slug": "kamino-liquidity", "category": "Liquidity Manager", "tvl": 20, "url": "https://kamino.com"},
        ]

        candidates = discover.aggregate_protocols(
            rows,
            config=config,
            tracked_keys=tracked_keys,
            slug_to_key=slug_to_key,
            name_to_key=name_to_key,
            family_names=family_names,
        )

        self.assertEqual(sorted(candidates), ["ethena", "kamino"])
        self.assertEqual(candidates["ethena"]["protocol_name"], "Ethena")
        self.assertEqual(candidates["ethena"]["tvl"], 140.0)
        self.assertEqual(sorted(candidates["ethena"]["defillama_slugs"]), ["ethena-usde", "ethena-usdtb"])
        self.assertEqual(candidates["kamino"]["tvl"], 100.0)

    def test_tracked_alias_family_is_excluded(self) -> None:
        config = {
            "ignored_categories": [],
            "ignored_slugs": [],
            "family_aliases": {
                "eigenlayer": {
                    "protocol_name": "EigenLayer",
                    "defillama_slugs": ["eigencloud"],
                    "fee_names": ["EigenCloud", "EigenLayer"],
                }
            },
        }
        slug_to_key, name_to_key, family_names = discover.build_alias_maps(config)
        tracked_keys = discover.build_tracked_keys([{"name": "EigenLayer", "forum_url": "https://forum.eigenlayer.xyz"}], family_names)
        rows = [
            {"name": "EigenCloud", "slug": "eigencloud", "category": "Restaking", "tvl": 100, "url": "https://www.eigencloud.xyz"}
        ]

        candidates = discover.aggregate_protocols(
            rows,
            config=config,
            tracked_keys=tracked_keys,
            slug_to_key=slug_to_key,
            name_to_key=name_to_key,
            family_names=family_names,
        )

        self.assertEqual(candidates, {})


class ForumValidationTests(unittest.TestCase):
    def test_validate_forum_url_success(self) -> None:
        def fetcher(_: str) -> dict:
            return {"latest_posts": [{"created_at": "2026-04-20T00:00:00Z"}, {"updated_at": "2026-04-22T00:00:00Z"}]}

        result = discover.validate_forum_url("https://forum.example.org", fetcher=fetcher)

        self.assertEqual(result["forum_status"], "ok")
        self.assertEqual(result["latest_post_ts"], "2026-04-22T00:00:00Z")

    def test_validate_forum_url_dns_failure(self) -> None:
        def fetcher(_: str) -> dict:
            raise urllib.error.URLError("nodename nor servname provided, or not known")

        result = discover.validate_forum_url("https://forum.example.org", fetcher=fetcher)

        self.assertEqual(result["forum_status"], "dns_error")

    def test_validate_forum_url_http_403(self) -> None:
        def fetcher(_: str) -> dict:
            raise urllib.error.HTTPError("https://forum.example.org/posts.json", 403, "Forbidden", hdrs=None, fp=None)

        result = discover.validate_forum_url("https://forum.example.org", fetcher=fetcher)

        self.assertEqual(result["forum_status"], "http_403")

    def test_validate_forum_url_not_discourse(self) -> None:
        def fetcher(_: str) -> dict:
            return {"posts": []}

        result = discover.validate_forum_url("https://forum.example.org", fetcher=fetcher)

        self.assertEqual(result["forum_status"], "not_discourse")

    def test_validate_forum_url_invalid_json(self) -> None:
        def fetcher(_: str) -> dict:
            raise json.JSONDecodeError("bad json", doc="", pos=0)

        result = discover.validate_forum_url("https://forum.example.org", fetcher=fetcher)

        self.assertEqual(result["forum_status"], "invalid_json")

    def test_validate_forum_url_timeout(self) -> None:
        def fetcher(_: str) -> dict:
            raise TimeoutError("timed out")

        result = discover.validate_forum_url("https://forum.example.org", fetcher=fetcher)

        self.assertEqual(result["forum_status"], "timeout")

    def test_validate_forum_url_tls_error(self) -> None:
        def fetcher(_: str) -> dict:
            raise urllib.error.URLError(ssl.SSLError("TLS failure"))

        result = discover.validate_forum_url("https://forum.example.org", fetcher=fetcher)

        self.assertEqual(result["forum_status"], "tls_error")


class RankingTests(unittest.TestCase):
    def test_apply_percentiles_and_thresholds(self) -> None:
        candidates = {
            "alpha": {"protocol_name": "Alpha", "canonical_key": "alpha", "tvl": 100.0, "fees_7d": 90.0},
            "beta": {"protocol_name": "Beta", "canonical_key": "beta", "tvl": 70.0, "fees_7d": 30.0},
            "gamma": {"protocol_name": "Gamma", "canonical_key": "gamma", "tvl": 20.0, "fees_7d": 5.0},
        }
        for candidate in candidates.values():
            candidate.update({"forum_activity_score": 0.0, "score": 0.0, "recommendation": "skip"})

        discover.apply_percentiles(candidates)
        now = datetime(2026, 4, 24, tzinfo=timezone.utc)

        alpha = candidates["alpha"]
        alpha["forum_status"] = "ok"
        alpha["latest_post_ts"] = iso(now - discover.timedelta(days=2))
        alpha["forum_activity_score"] = discover.forum_activity_score(alpha["latest_post_ts"], now=now)
        alpha["score"] = round((0.45 * alpha["tvl_percentile"]) + (0.35 * alpha["fees_7d_percentile"]) + (0.20 * alpha["forum_activity_score"]), 4)
        alpha["recommendation"] = discover.choose_recommendation(alpha)

        beta = candidates["beta"]
        beta["forum_status"] = "ok"
        beta["latest_post_ts"] = iso(now - discover.timedelta(days=40))
        beta["forum_activity_score"] = discover.forum_activity_score(beta["latest_post_ts"], now=now)
        beta["score"] = round((0.45 * beta["tvl_percentile"]) + (0.35 * beta["fees_7d_percentile"]) + (0.20 * beta["forum_activity_score"]), 4)
        beta["recommendation"] = discover.choose_recommendation(beta)

        gamma = candidates["gamma"]
        gamma["forum_status"] = "tls_error"
        gamma["latest_post_ts"] = None
        gamma["score"] = round((0.45 * gamma["tvl_percentile"]) + (0.35 * gamma["fees_7d_percentile"]), 4)
        gamma["pre_score"] = 0.5
        gamma["recommendation"] = discover.choose_recommendation(gamma)

        self.assertEqual(alpha["recommendation"], "add_now")
        self.assertEqual(beta["recommendation"], "review")
        self.assertEqual(gamma["recommendation"], "review")


class IntegrationTests(unittest.TestCase):
    def test_discovery_outputs_stable_sections_and_order(self) -> None:
        config = {
            "ignored_categories": [],
            "ignored_slugs": [],
            "family_aliases": {
                "ethena": {
                    "protocol_name": "Ethena",
                    "defillama_slugs": ["ethena-usde", "ethena-usdtb"],
                    "fee_names": ["Ethena USDe", "Ethena USDtb"],
                },
                "kamino": {
                    "protocol_name": "Kamino",
                    "defillama_slugs": ["kamino-lend"],
                    "fee_names": ["Kamino Lend"],
                },
                "obol": {
                    "protocol_name": "Obol",
                    "defillama_slugs": ["obol"],
                    "fee_names": ["Obol"],
                },
            },
            "forum_overrides": {
                "ethena": "https://gov.ethenafoundation.com",
                "kamino": "https://gov.kamino.finance",
                "obol": "https://community.obol.org",
            },
        }
        protocols_payload = [
            {"name": "Ethena USDe", "slug": "ethena-usde", "category": "Basis Trading", "tvl": 110, "url": "https://app.ethena.fi"},
            {"name": "Ethena USDtb", "slug": "ethena-usdtb", "category": "RWA", "tvl": 40, "url": "https://app.ethena.fi"},
            {"name": "Kamino Lend", "slug": "kamino-lend", "category": "Lending", "tvl": 90, "url": "https://kamino.com"},
            {"name": "Obol", "slug": "obol", "category": "Staking Pool", "tvl": 20, "url": "https://obol.org"},
            {"name": "Lido", "slug": "lido", "category": "Liquid Staking", "tvl": 200, "url": "https://lido.fi"},
        ]
        fees_payload = {
            "protocols": [
                {"displayName": "Ethena USDe", "category": "Basis Trading", "total7d": 100},
                {"displayName": "Kamino Lend", "category": "Lending", "total7d": 60},
                {"displayName": "Obol", "category": "Staking Pool", "total7d": 10},
                {"displayName": "Lido", "category": "Liquid Staking", "total7d": 200},
            ]
        }
        daos = [{"name": "Lido", "forum_url": "https://research.lido.fi"}]
        now = datetime(2026, 4, 24, tzinfo=timezone.utc)

        def fetcher(url: str) -> dict:
            if "ethenafoundation" in url:
                return {"latest_posts": [{"created_at": "2026-04-23T00:00:00Z"}]}
            if "kamino.finance" in url:
                return {"latest_posts": [{"created_at": "2026-04-10T00:00:00Z"}]}
            if "research.lido.fi" in url:
                return {"latest_posts": [{"created_at": "2026-04-21T00:00:00Z"}]}
            raise urllib.error.URLError("nodename nor servname provided, or not known")

        candidates, existing_broken = discover.discover_candidates(
            protocols_payload=protocols_payload,
            fees_payload=fees_payload,
            daos=daos,
            config=config,
            top_n=10,
            now=now,
            fetcher=fetcher,
        )

        self.assertEqual([item["protocol_name"] for item in candidates], ["Ethena", "Kamino", "Obol"])
        self.assertEqual(candidates[0]["recommendation"], "add_now")
        self.assertEqual(candidates[1]["recommendation"], "review")
        self.assertEqual(candidates[2]["recommendation"], "skip")
        self.assertEqual(existing_broken, [])

        markdown = discover.render_markdown_report(
            generated_at="2026-04-24T00:00:00Z",
            top_n=10,
            min_score=0.45,
            candidates=candidates,
            existing_broken=existing_broken,
        )

        self.assertIn("## Add Now", markdown)
        self.assertIn("## Review", markdown)
        self.assertIn("## Rejected", markdown)
        self.assertLess(markdown.index("Ethena"), markdown.index("Kamino"))


class WorkflowTests(unittest.TestCase):
    def test_discovery_workflow_commits_only_candidate_artifacts(self) -> None:
        workflow = Path("/Users/tanishakatara/dao-gov-watch/.github/workflows/discover_forums.yml").read_text()

        self.assertIn("cron: '0 1 * * 1'", workflow)
        self.assertIn("python discover_forum_candidates.py", workflow)
        self.assertIn("git add forum_candidates.json forum_candidates.md", workflow)
        self.assertNotIn("state.json", workflow)
        self.assertNotIn("opportunities.json", workflow)
        self.assertNotIn("dashboard.html", workflow)

    def test_monitor_workflow_ignores_discovery_artifacts(self) -> None:
        workflow = Path("/Users/tanishakatara/dao-gov-watch/.github/workflows/monitor.yml").read_text()

        self.assertIn("forum_candidates.json", workflow)
        self.assertIn("forum_candidates.md", workflow)


if __name__ == "__main__":
    unittest.main()
