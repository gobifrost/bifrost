"""Normalize CLI artifacts and summarize measured invocation telemetry."""
import json
from pathlib import Path
import statistics

HERE = Path(__file__).resolve().parent
for path in (HERE / "results").glob("*"):
    if path.suffix not in (".json", ".log"):
        continue
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    value = raw.decode(encoding)
    path.write_text(value.replace("\r\n", "\n"), encoding="utf-8", newline="\n")

metrics = json.loads((HERE / "results/cloud-metrics.json").read_text())["data"]
invocations = [r for r in metrics if r["httpStatus"] > 0]
summary = {}
for row in invocations:
    name = row["url"].rsplit("/", 1)[-1] or "diagnostic_or_memory"
    summary.setdefault(name, []).append(row)
output = {}
for name, rows in summary.items():
    good = [r for r in rows if r["httpStatus"] == 200]
    output[name] = {"invocations": len(rows), "http_statuses": [r["httpStatus"] for r in rows]}
    if good:
        cpu = statistics.median(r["cpuTimeMs"] for r in good)
        mean_cpu = statistics.mean(r["cpuTimeMs"] for r in good)
        output[name].update(median_cpu_ms=cpu, mean_cpu_ms=mean_cpu,
            median_wall_ms=statistics.median(r["wallTimeMs"] for r in good),
            estimated_marginal_usd_per_1000=0.0003+0.00002*mean_cpu,
            correctness="HTTP status alone is not workflow success; cross-check benchmark-cloud.json")
report = {"source": "Cloudflare logs.workersLogs, exact script filter; invocation rows only",
          "cost": "Request marginal estimate at observed mean CPU, including failed workflows; excludes allowances, monthly minimum, upstream costs. No successful cloud SDK cost measured.",
          "workloads": output}
(HERE / "results/metrics-summary.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8", newline="\n")
names = [n for n in ("json_transform", "async_context", "workspace_import", "http_orchestration", "multi_step") if n in output]
max_cpu = max(output[n]["median_cpu_ms"] for n in names)
bars = "".join(f'<div class="row"><span>{n}</span><div class="track"><div class="bar" style="width:{max(1,output[n]["median_cpu_ms"] / max_cpu*100):.1f}%"></div></div><b>{output[n]["median_cpu_ms"]:g} ms</b></div>' for n in names)
html = f'''<!doctype html><html><head><meta charset="utf-8"><style>
body{{font-family:var(--font-sans,system-ui);color:var(--foreground)}}
p{{font-size:13px;color:var(--muted-foreground)}}.row{{display:grid;grid-template-columns:145px 1fr 65px;gap:8px;align-items:center;margin:12px 0;font-size:12px}}
.track{{height:18px;background:var(--secondary,#29323e);border-radius:4px}}.bar{{height:18px;background:var(--chart-1,#5b9bea);border-radius:4px}}b{{text-align:right}}
</style></head><body><strong>Cloudflare Invocation CPU — Observed Medians</strong><p>Small sample, exact test Worker. Not a Linux speed comparison.</p>{bars}<p>SDK read/write proof ran locally against the isolated API; cloud SDK workloads had no Bifrost credentials.</p></body></html>'''
(HERE / "results/measurements.html").write_text(html, encoding="utf-8", newline="\n")
print(json.dumps(output, indent=2))
