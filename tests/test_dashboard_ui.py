import os
import re
import subprocess
import tempfile
import unittest
from fastapi.testclient import TestClient
from dashboard.main import app

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_HTML_PATH = os.path.join(ROOT_DIR, "dashboard", "static", "index.html")


class TestDashboardUIAndDesign(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(INDEX_HTML_PATH, "r", encoding="utf-8") as f:
            cls.html = f.read()
        cls.client = TestClient(app)

    def test_no_undefined_css_variables(self):
        """Ensure every var(--...) used in index.html is defined in :root."""
        defined_vars = set(re.findall(r"(--[a-zA-Z0-9_-]+)\s*:", self.html))
        used_vars = set(re.findall(r"var\(\s*(--[a-zA-Z0-9_-]+)", self.html))
        undefined = used_vars - defined_vars
        self.assertEqual(
            undefined,
            set(),
            f"Found undefined CSS custom properties in index.html: {sorted(undefined)}",
        )

    def test_no_missing_dom_ids_for_get_element_by_id(self):
        """Ensure every static getElementById('...') call has a matching id in HTML/templates."""
        html_ids = set(re.findall(r'\bid=["\']([a-zA-Z0-9_-]+)["\']', self.html))
        get_ids = set(
            re.findall(r'getElementById\(\s*["\']([a-zA-Z0-9_-]+)["\']\s*\)', self.html)
        )
        missing = get_ids - html_ids
        self.assertEqual(
            missing,
            set(),
            f"Found getElementById() calls with missing DOM IDs: {sorted(missing)}",
        )

    def test_no_missing_js_event_handler_functions(self):
        """Ensure every onclick/oninput/onchange handler calls a defined JS function."""
        handlers = set(
            re.findall(
                r'\bon(?:click|input|change)\s*=\s*["\'](?:event\.stopPropagation\(\);\s*)?([a-zA-Z0-9_]+)\s*\(',
                self.html,
            )
        )
        js_funcs = set(re.findall(r"\bfunction\s+([a-zA-Z0-9_]+)\s*\(", self.html))
        js_consts = set(
            re.findall(r"\b(?:const|let|var|window\.)\s*([a-zA-Z0-9_]+)\s*=", self.html)
        )
        builtins = {"if", "window", "document", "localStorage", "navigator", "alert", "confirm"}
        missing = handlers - js_funcs - js_consts - builtins
        self.assertEqual(
            missing,
            set(),
            f"Found inline HTML handlers referencing undefined JS functions: {sorted(missing)}",
        )

    def test_javascript_syntax_valid(self):
        """Validate embedded <script> block syntax using node --check."""
        scripts = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        self.assertGreaterEqual(len(scripts), 1)
        main_script = scripts[-1]
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".js", encoding="utf-8", delete=False
        ) as tmp:
            tmp.write(main_script)
            tmp_path = tmp.name
        try:
            proc = subprocess.run(
                ["node", "--check", tmp_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(
                proc.returncode,
                0,
                f"JavaScript syntax check failed:\n{proc.stderr}",
            )
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

    def test_original_logos_restored_and_served(self):
        """Verify original Bybit, Polymarket, BTC, and ETH logos are referenced and served via /media/logo/..."""
        expected_logos = [
            "/media/logo/Bybit/Bybit_Logo_2.svg",
            "/media/logo/Polymarket/Polymarket_icon-blue_2.png",
            "/media/logo/Bitcoin/Bitcoin_id_4h5jyQK_0.svg",
            "/media/logo/Ethereum/Ethereum_Icon_0.jpeg",
        ]
        for logo_url in expected_logos:
            self.assertIn(logo_url, self.html, f"Logo {logo_url} not found in index.html")
            resp = self.client.get(logo_url)
            self.assertEqual(
                resp.status_code,
                200,
                f"Static logo {logo_url} returned HTTP {resp.status_code}",
            )

    def test_pnl_bento_hero_and_modern_components_present(self):
        """Verify Bento P&L Hero Grid, Market Inspector, Command Palette, and Sticky Footer exist."""
        required_ids = [
            "paper-kpi-profit",
            "paper-kpi-unrealized",
            "paper-kpi-best",
            "paper-kpi-fees",
            "paper-kpi-roi",
            "paper-kpi-balance",
            "paper-split-bybit-label",
            "paper-split-poly-label",
            "paper-split-bybit-bar",
            "paper-split-poly-bar",
            "paper-equity-chart",
            "inspector-aside",
            "cmd-palette-modal",
            "hotkeys-modal",
        ]
        for dom_id in required_ids:
            self.assertIn(f'id="{dom_id}"', self.html, f"Missing required element #{dom_id}")

    def test_dashboard_http_endpoints(self):
        """Verify main page and JSON API endpoints respond with HTTP 200."""
        endpoints = [
            "/",
            "/api/spreads",
            "/api/top_spreads",
            "/api/active_contracts",
            "/api/archived_arbs",
            "/api/ticks_stats",
        ]
        for ep in endpoints:
            resp = self.client.get(ep)
            self.assertEqual(resp.status_code, 200, f"Endpoint {ep} returned {resp.status_code}")


if __name__ == "__main__":
    unittest.main()
