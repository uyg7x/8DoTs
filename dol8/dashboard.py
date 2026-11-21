from __future__ import annotations
import os
import sys
import threading
import time
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT_DIR)

from dol8 import capabilities

try:
    import psutil
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    import uvicorn
except ImportError:
    print("Run: pip install fastapi uvicorn psutil", file=sys.stderr)
    sys.exit(2)

# ---- DB SETUP ----
DB_PATH = os.path.join(ROOT_DIR, "dol8_history.db")
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT,
                tier TEXT,
                power_w REAL,
                energy_j REAL,
                peak_w REAL
            )
        """)
init_db()

# ---- STATE ----
@dataclass
class SampleBuf:
    times: list
    power_w: list
    energy_j: list
    cpu_pct: list
    status: str
    peak_power: float
    tier: str
    source: str
    confidence: str

    def reset(self):
        self.times.clear()
        self.power_w.clear()
        self.energy_j.clear()
        self.cpu_pct.clear()
        self.peak_power = 0.0
        self.status = "RUNNING"

    def as_dict(self): return asdict(self)

BUF = SampleBuf([], [], [], [], "IDLE", 0.0, "?", "?", "?")
LOCK = threading.Lock()

_RAPL = None
_LAST_RAPL_SNAP = None
try:
    import dol8_core
    if capabilities._rapl_available():
        _RAPL = dol8_core.RaplReader("/sys/class/powercap")
except Exception:
    _RAPL = None

def _read_rapl_delta_joules():
    global _LAST_RAPL_SNAP
    if _RAPL is None: return None
    try: snap = _RAPL.read_all()
    except Exception: return None
    if _LAST_RAPL_SNAP is None:
        _LAST_RAPL_SNAP = snap
        return None
    prev = _LAST_RAPL_SNAP
    _LAST_RAPL_SNAP = snap
    try: return float(_RAPL.joules_between(prev, snap))
    except Exception: return None

def sample_loop():
    cumulative_j = 0.0
    t0 = time.time()
    while True:
        time.sleep(0.1)
        with LOCK:
            if BUF.status != "RUNNING": continue
            elapsed = time.time() - t0
            cpu_pct = float(psutil.cpu_percent(interval=None))
            rapl_j = _read_rapl_delta_joules()

            if rapl_j is not None:
                power_w = rapl_j / 0.1
                cumulative_j += rapl_j
            else:
                power_w = (cpu_pct / 100.0) * 65.0 # Proxy
                cumulative_j += power_w * 0.1

            if power_w > BUF.peak_power: BUF.peak_power = power_w

            BUF.times.append(round(elapsed, 2))
            BUF.power_w.append(round(power_w, 4))
            BUF.energy_j.append(round(cumulative_j, 4))
            BUF.cpu_pct.append(round(cpu_pct, 2))

            for attr in ("times", "power_w", "energy_j", "cpu_pct"):
                lst = getattr(BUF, attr)
                if len(lst) > 600: del lst[:len(lst) - 600]

app = FastAPI(title="dol8 Live Dashboard")

# (HTML remains exactly the same as previous version, just adding a History button)
INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dol8 Live Dashboard</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body class="bg-gray-900 text-gray-100 p-4 sm:p-8">
<div class="max-w-7xl mx-auto">
  <div class="flex flex-wrap gap-4 justify-between items-center mb-6">
    <h1 class="text-2xl sm:text-3xl font-bold text-blue-400">⚡ dol8 Energy Dashboard</h1>
    <div class="flex gap-2">
      <button onclick="startTest()" class="bg-blue-600 hover:bg-blue-700 px-4 py-2 rounded">Start</button>
      <button onclick="stopTest()" class="bg-gray-700 hover:bg-gray-600 px-4 py-2 rounded">Stop</button>
      <button onclick="loadHistory()" class="bg-purple-600 hover:bg-purple-700 px-4 py-2 rounded">History</button>
    </div>
  </div>
  <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 sm:gap-4 mb-6">
    <div class="bg-gray-800 p-4 rounded-lg border border-gray-700"><div class="text-xs text-gray-400 uppercase">Status</div><div id="status" class="text-xl font-bold text-green-400">IDLE</div></div>
    <div class="bg-gray-800 p-4 rounded-lg border border-gray-700"><div class="text-xs text-gray-400 uppercase">Peak Power</div><div id="peak" class="text-xl font-bold text-yellow-400">0.00 W</div></div>
    <div class="bg-gray-800 p-4 rounded-lg border border-gray-700"><div class="text-xs text-gray-400 uppercase">Tier / Source</div><div id="tier" class="text-xl font-bold text-purple-400">-</div></div>
    <div class="bg-gray-800 p-4 rounded-lg border border-gray-700"><div class="text-xs text-gray-400 uppercase">Confidence</div><div id="conf" class="text-xl font-bold text-pink-400">-</div></div>
  </div>
  <div class="bg-gray-800 p-4 rounded-lg border border-gray-700 mb-6"><canvas id="powerChart" height="100"></canvas></div>
  <div class="bg-gray-800 p-4 rounded-lg border border-gray-700"><canvas id="cpuChart" height="60"></canvas></div>
</div>
<script>
const ctxP = document.getElementById('powerChart').getContext('2d');
const ctxC = document.getElementById('cpuChart').getContext('2d');
const powerChart = new Chart(ctxP, {type:'line',data:{labels:[],datasets:[{label:'Power (W)',data:[],borderColor:'rgb(59,130,246)',fill:true,tension:0.3},{label:'Energy (J)',data:[],borderColor:'rgb(234,179,8)',yAxisID:'y1',tension:0.3}]},options:{responsive:true,animation:false,scales:{y:{beginAtZero:true,title:{display:true,text:'Watts'}},y1:{beginAtZero:true,position:'right',title:{display:true,text:'Joules'}}}}});
const cpuChart = new Chart(ctxC, {type:'line',data:{labels:[],datasets:[{label:'CPU %',data:[],borderColor:'rgb(34,197,94)',tension:0.3}]},options:{responsive:true,animation:false,scales:{y:{beginAtZero:true,max:100}}}});

async function startTest() { await fetch('/start', { method: 'POST' }); }
async function stopTest() { await fetch('/stop', { method: 'POST' }); }
async function loadHistory() {
    const res = await fetch('/history');
    const data = await res.json();
    alert("Past Runs: \\n" + data.map(r => `${r.timestamp} | ${r.tier} | ${r.energy_j?.toFixed(2)} J`).join("\\n"));
}

setInterval(async () => {
  const d = await (await fetch('/data')).json();
  document.getElementById('status').innerText = d.status;
  document.getElementById('status').className = d.status === 'RUNNING' ? 'text-xl font-bold text-yellow-400' : 'text-xl font-bold text-green-400';
  document.getElementById('peak').innerText = (d.peak_power||0).toFixed(2) + ' W';
  document.getElementById('tier').innerText = d.tier + ' / ' + d.source;
  document.getElementById('conf').innerText = d.confidence;
  powerChart.data.labels = d.times; powerChart.data.datasets[0].data = d.power_w; powerChart.data.datasets[1].data = d.energy_j; powerChart.update();
  cpuChart.data.labels = d.times; cpuChart.data.datasets[0].data = d.cpu_pct; cpuChart.update();
}, 250);
</script>
</body>
</html>
"""

@app.get("/")
def index() -> HTMLResponse:
    return HTMLResponse(content=INDEX_HTML)

@app.get("/data")
def get_data() -> dict:
    with LOCK: return BUF.as_dict()

@app.get("/history")
def get_history():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 10")
        return [dict(row) for row in cursor.fetchall()]

@app.post("/start")
def start_test() -> dict:
    with LOCK:
        BUF.reset()
        tier = capabilities.get_current_tier()
        BUF.tier = f"Tier {tier.tier}"
        BUF.source = tier.source
        BUF.confidence = tier.confidence
        BUF.status = "RUNNING"
    return {"ok": True}

@app.post("/stop")
def stop_test() -> dict:
    with LOCK:
        BUF.status = "STOPPED"
        # Save to SQLite DB
        if BUF.energy_j:
            final_energy = BUF.energy_j[-1]
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO runs (timestamp, tier, power_w, energy_j, peak_w) VALUES (?, ?, ?, ?, ?)",
                    (datetime.now().isoformat(), BUF.tier, BUF.power_w[-1] if BUF.power_w else 0, final_energy, BUF.peak_power)
                )
    return {"ok": True}

if __name__ == "__main__":
    threading.Thread(target=sample_loop, daemon=True).start()
    print("dashboard: http://127.0.0.1:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")