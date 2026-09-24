"""Stored agent/model strings must render as text in the built-in dashboard.

``agent`` and ``model`` are caller-controlled: ``model`` comes from proxied
request bodies and both arrive through ``/api/ingest/routed``. The dashboard
concatenated them into ``innerHTML`` (and into an inline ``onclick``), so a
stored ``<img onerror=...>`` ran in the operator's browser, which holds the
Token Spy API key in sessionStorage.

The real dashboard script and chart module run under Node with a minimal DOM
stub; the assertions inspect the HTML they actually produce.
"""
import importlib.util
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4

import pytest

SERVICE = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
PAYLOAD = "<img src=x onerror=alert(1)>');alert(2);//\""

pytestmark = pytest.mark.skipif(NODE is None, reason="node is required to execute the dashboard JavaScript")

DOM_STUB = """
const elements = {};
function element(id) {
  if (!elements[id]) {
    elements[id] = {
      id, innerHTML: '', textContent: '', value: '24', style: {}, dataset: {},
      clientWidth: 320, clientHeight: 280, width: 0, height: 0,
      addEventListener() {},
      getContext() { return new Proxy({}, { get: () => () => ({ width: 10 }), set: () => true }); },
    };
  }
  return elements[id];
}
globalThis.window = globalThis;
window.devicePixelRatio = 1;
window.addEventListener = () => {};
globalThis.document = { getElementById: element, addEventListener() {} };
globalThis.sessionStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.setInterval = () => 0;
globalThis.fetch = async () => { throw new Error('no network in tests'); };
"""


def load(filename):
    spec = importlib.util.spec_from_file_location(f"escaping_{uuid4().hex}", SERVICE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dashboard_script(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SERVICE))
    monkeypatch.setenv("DB_BACKEND", "sqlite")
    monkeypatch.setenv("TOKEN_SPY_API_KEY", "escaping-fixture-key")
    db = load("db.py")
    db.DB_PATH = str(tmp_path / "usage.db")
    monkeypatch.setitem(sys.modules, "db", db)
    api = load("main.py")
    inline = re.findall(r"<script>(.*?)</script>", api.DASHBOARD_HTML, re.S)
    assert len(inline) == 1, "expected exactly one inline dashboard script"
    return inline[0]


def run_node(tmp_path, source):
    harness = tmp_path / "harness.js"
    harness.write_text(source, encoding="utf-8")
    result = subprocess.run([NODE, str(harness)], capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.strip().splitlines()[-1])


def assert_inert(html):
    # Summary cards upper-case agent names, so compare case-insensitively.
    assert "<img" not in html.lower()
    assert "&lt;img src=x onerror=alert(1)&gt;" in html.lower()


def test_dashboard_renders_stored_strings_as_text(tmp_path, dashboard_script):
    bad = json.dumps(PAYLOAD)
    rendered = run_node(tmp_path, DOM_STUB + dashboard_script + f"""
const bad = {bad};
renderTable([{{timestamp: '2026-09-24T00:00:00Z', agent: bad, model: bad,
  input_tokens: 1, output_tokens: 1, cache_read_tokens: 0, cache_write_tokens: 0,
  system_prompt_total_chars: 0, conversation_history_chars: 0, estimated_cost_usd: 0, duration_ms: 10}}]);
renderSummary([
  {{agent: bad, turns: 1, total_cost: 0, avg_input_tokens: 1, is_local_model: false}},
  {{agent: bad, turns: 1, total_cost: 0, avg_input_tokens: 1, is_local_model: true}},
]);
renderSessionPanel([{{agent: bad, recommendation: 'reset_recommended', current_session_turns: 1,
  current_history_chars: 10, session_char_limit: 100, is_local_model: false,
  last_turn_cost: 0, avg_cost_last_5: 0, cache_write_pct_last_5: 0, cost_since_last_reset: 0}}]);
console.log(JSON.stringify({{
  table: element('recent-table').innerHTML,
  summary: element('summary-cards').innerHTML,
  sessions: element('session-panel').innerHTML,
}}));
""")

    assert_inert(rendered["table"])
    assert_inert(rendered["summary"])
    assert_inert(rendered["sessions"])
    # The reset control must not splice the agent into inline JavaScript.
    handlers = re.findall(r'onclick="([^"]*)"', rendered["sessions"])
    assert handlers == ["resetSession(this.dataset.agent, this)"]


def test_chart_legend_renders_series_labels_as_text(tmp_path):
    charts = (SERVICE / "dashboard_charts.js").read_text(encoding="utf-8")
    bad = json.dumps(PAYLOAD)
    rendered = run_node(tmp_path, DOM_STUB + charts + f"""
const legendEl = element('cost-legend');
TokenSpyCharts.line(element('cost-chart'), {{
  legendEl,
  series: [{{label: {bad}, color: '#58a6ff', points: [{{x: 1, y: 1}}, {{x: 2, y: 2}}]}}],
  yFormatter: v => String(v),
  xFormatter: v => String(v),
}});
console.log(JSON.stringify({{legend: legendEl.innerHTML}}));
""")

    assert_inert(rendered["legend"])
