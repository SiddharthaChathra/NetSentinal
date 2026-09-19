"""
PDF rendering of the NetSentinel report.

Takes the exact document GET /api/report produces (so the PDF can never
disagree with the dashboard) plus time-series history, and lays it out as
a network engineer's audit report: executive summary, hosted scan with
layer-by-layer evidence, managed devices with agent telemetry, trend
graphs, backup readiness with the SLA arithmetic shown, and a methodology
appendix explaining every check, threshold and weight.
"""
from datetime import datetime, timezone
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)
from reportlab.graphics.shapes import Drawing, String
from reportlab.graphics.charts.lineplots import LinePlot
from reportlab.graphics.widgets.markers import makeMarker

from src.backup_readiness import (
    estimate_sla, _latency_factor, _loss_factor, DEFAULT_NOMINAL_LINK_MBPS,
    READY_THRESHOLD, AT_RISK_THRESHOLD, AGENT_STALE_SECONDS, _parse_ts,
)
from src.port_checker import get_backup_port_map

# --- Palette (print-friendly; cyan accent matches the product) ---------------
NAVY = colors.HexColor("#0f172a")
CYAN = colors.HexColor("#0891b2")
SLATE = colors.HexColor("#475569")
LIGHT = colors.HexColor("#f1f5f9")
LINE = colors.HexColor("#cbd5e1")
GREEN = colors.HexColor("#15803d")
AMBER = colors.HexColor("#b45309")
RED = colors.HexColor("#b91c1c")

_ss = getSampleStyleSheet()
H1 = ParagraphStyle("H1", parent=_ss["Title"], fontSize=22, leading=26, textColor=NAVY, alignment=TA_LEFT, spaceAfter=4)
H2 = ParagraphStyle("H2", parent=_ss["Heading2"], fontSize=14, leading=18, textColor=NAVY, spaceBefore=14, spaceAfter=6, keepWithNext=1)
H3 = ParagraphStyle("H3", parent=_ss["Heading3"], fontSize=11, leading=14, textColor=CYAN, spaceBefore=10, spaceAfter=4, keepWithNext=1)
BODY = ParagraphStyle("Body", parent=_ss["BodyText"], fontSize=9, leading=12.5, textColor=colors.black)
SMALL = ParagraphStyle("Small", parent=BODY, fontSize=8, leading=10.5, textColor=SLATE)
MONO = ParagraphStyle("Mono", parent=BODY, fontName="Courier", fontSize=8, leading=10.5)
NOTE = ParagraphStyle("Note", parent=BODY, fontSize=8.5, leading=11.5, textColor=SLATE, backColor=LIGHT,
                      borderPadding=(6, 8, 6, 8), spaceBefore=4, spaceAfter=8)


def _num(v, unit="", nd=1) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return "—"
    return f"{f:.{nd}f}{unit}" if f != int(f) or nd == 0 else f"{int(f)}{unit}"


def _esc(v) -> str:
    return (str(v) if v is not None else "—").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _p(text, style=BODY):
    return Paragraph(_esc(text) if not text.startswith("<") else text, style)


def _fmt_ts(value) -> str:
    if not value:
        return "—"
    try:
        dt = _parse_ts(value)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(value)


def _sev_color(sev: str):
    return {"critical": RED, "warning": AMBER}.get((sev or "").lower(), GREEN)


def _status_color(text: str):
    t = (text or "").upper()
    if t.startswith("PASS") or t in ("HEALTHY", "READY", "UP", "OK", "RESOLVED", "OPEN", "MEETS", "LIVE"):
        return GREEN
    if t in ("PARTIAL", "DEGRADED", "AT-RISK", "WARNING", "TIGHT") or "WARNING" in t:
        return AMBER
    if t in ("N/A", "NOT RUN", "—"):
        return SLATE
    return RED


def _table(rows, col_widths=None, header=True, zebra=True, font=8):
    """Uniform table styling. `rows` are lists of str/Paragraph."""
    data = [[c if not isinstance(c, str) else Paragraph(_esc(c), ParagraphStyle("c", parent=BODY, fontSize=font, leading=font + 3)) for c in r] for r in rows]
    t = Table(data, colWidths=col_widths, repeatRows=1 if header else 0, hAlign="LEFT")
    style = [
        ("GRID", (0, 0), (-1, -1), 0.4, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
        data[0] = [Paragraph(f"<font color='white'><b>{_esc(c if isinstance(c, str) else '')}</b></font>", BODY) if isinstance(c, str) else c for c in rows[0]]
        t = Table(data, colWidths=col_widths, repeatRows=1, hAlign="LEFT")
    if zebra:
        for i in range(1 if header else 0, len(rows)):
            if i % 2 == 0:
                style.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
    t.setStyle(TableStyle(style))
    return t


def _status_cell(text):
    return Paragraph(f"<font color='{_status_color(text).hexval()}'><b>{_esc(text)}</b></font>", BODY)


def _line_chart(title: str, series: list, width=170 * mm, height=55 * mm, y_label="", x_label="oldest → newest"):
    """series: [(label, [(x, y), ...], color)]. x is a float (hours ago,
    negative to the left) or an index."""
    d = Drawing(width, height + 26)
    d.add(String(0, height + 16, title, fontName="Helvetica-Bold", fontSize=9, fillColor=NAVY))
    lp = LinePlot()
    lp.x, lp.y = 38, 26
    lp.width, lp.height = width - 50, height - 20
    lp.data = [pts for _, pts, _ in series if pts]
    for i, (_, pts, colr) in enumerate([s for s in series if s[1]]):
        lp.lines[i].strokeColor = colr
        lp.lines[i].strokeWidth = 1.2
        lp.lines[i].symbol = makeMarker("FilledCircle")
        lp.lines[i].symbol.size = 2.5
        lp.lines[i].symbol.fillColor = colr
        lp.lines[i].symbol.strokeColor = colr
    lp.xValueAxis.labelTextFormat = "%.0f"
    lp.xValueAxis.labels.fontSize = 6.5
    lp.yValueAxis.labels.fontSize = 6.5
    lp.yValueAxis.valueMin = 0
    lp.xValueAxis.visibleGrid = True
    lp.yValueAxis.visibleGrid = True
    lp.xValueAxis.gridStrokeColor = LINE
    lp.yValueAxis.gridStrokeColor = LINE
    d.add(lp)
    d.add(String(lp.x, 2, x_label, fontName="Helvetica", fontSize=6.5, fillColor=SLATE))
    if y_label:
        d.add(String(0, height + 4, y_label, fontName="Helvetica", fontSize=6.5, fillColor=SLATE))
    # legend, right-aligned on the title line
    legend = [(label, colr) for label, pts, colr in series if pts]
    x = width
    for label, colr in reversed(legend):
        w = 4.2 * len(label) + 18
        x -= w
        d.add(String(x, height + 16, "■ " + label, fontName="Helvetica", fontSize=7, fillColor=colr))
    return d


def _hours_ago_series(rows, key, now, ts_key="timestamp"):
    pts = []
    for r in rows:
        try:
            t = _parse_ts(r.get(ts_key))
            v = r.get(key)
            if v is None:
                continue
            pts.append((round(-(now - t).total_seconds() / 3600.0, 3), float(v)))
        except Exception:
            continue
    pts.sort()
    return pts


# --- Document ---------------------------------------------------------------

def _footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Helvetica", 7)
    canvas.setFillColor(SLATE)
    canvas.drawString(15 * mm, 10 * mm, "NetSentinel — Network Diagnostic & Observability Report")
    canvas.drawRightString(A4[0] - 15 * mm, 10 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(LINE)
    canvas.line(15 * mm, 13 * mm, A4[0] - 15 * mm, 13 * mm)
    canvas.restoreState()


def build_pdf_report(report: dict, history: list = None, telemetry_history: dict = None) -> bytes:
    """report: the GET /api/report document. history: hosted-scan history rows
    (newest first, from the history table). telemetry_history: {device_id:
    [telemetry rows]} for the trend graphs."""
    history = history or []
    telemetry_history = telemetry_history or {}
    now = datetime.now(timezone.utc)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                            topMargin=16 * mm, bottomMargin=18 * mm,
                            title=report.get("title", "NetSentinel Report"), author="NetSentinel")
    W = A4[0] - 30 * mm
    story = []

    # ---- Title block
    story.append(Paragraph(_esc(report.get("title", "NetSentinel Report")), H1))
    story.append(Paragraph(f"Prepared by NetSentinel · {_fmt_ts(report.get('generated_at'))}", ParagraphStyle("sub", parent=BODY, fontSize=10, textColor=CYAN, spaceAfter=8)))
    meta = [
        ["Generated", _fmt_ts(report.get("generated_at"))],
        ["Report version", str(report.get("report_version", ""))],
        ["Account", "Signed in" if report.get("account", {}).get("signed_in") else "Guest (hosted scan only)"],
        ["Scope", "Hosted server scan" + (" + managed devices + backup readiness" if report.get("devices") else "")],
    ]
    story.append(_table([["Field", "Value"]] + meta, col_widths=[35 * mm, W - 35 * mm]))
    story.append(Spacer(1, 6))

    # ---- Executive summary
    sm = report.get("summary", {})
    story.append(Paragraph("1. Executive Summary", H2))
    kpis = [
        ["Indicator", "Value", "Assessment"],
        ["Hosted scan", f"{sm.get('hosted_scan_status')}" + (f" ({sm.get('hosted_scan_health_score')}/100)" if sm.get("hosted_scan_health_score") is not None else ""),
         _status_cell(sm.get("hosted_scan_status") or "NOT RUN")],
        ["Managed devices", f"{sm.get('devices', 0)} registered, {sm.get('devices_reporting', 0)} reporting live",
         _status_cell("OK" if sm.get("devices", 0) == sm.get("devices_reporting", 0) else "WARNING")],
        ["Backup targets", f"{sm.get('backup_targets', 0)}" + (f", readiness {sm.get('backup_readiness_score')}/100" if sm.get("backup_readiness_score") is not None else ""),
         _status_cell("READY" if (sm.get("backup_readiness_score") or 0) >= READY_THRESHOLD else ("AT-RISK" if (sm.get("backup_readiness_score") or 0) >= AT_RISK_THRESHOLD else ("N/A" if not sm.get("backup_targets") else "NOT-READY")))],
        ["Open warnings", str(sm.get("open_warnings", 0)), _status_cell("OK" if not sm.get("open_warnings") else "WARNING")],
    ]
    story.append(_table(kpis, col_widths=[40 * mm, W - 40 * mm - 32 * mm, 32 * mm]))
    verdict = []
    if report.get("hosted_scan"):
        h = report["hosted_scan"]
        verdict.append(f"The NetSentinel server's own network scored {h['health_score']}/100 ({h['status']}).")
    if report.get("devices"):
        live = sm.get("devices_reporting", 0)
        verdict.append(f"{live} of {sm.get('devices')} managed device(s) reported within the last {AGENT_STALE_SECONDS // 60} minutes.")
    if report.get("backup_readiness"):
        b = report["backup_readiness"]
        verdict.append(f"Backup readiness across {len(b['targets'])} target(s) is {b['backupReadinessScore']}/100 with {len(b['diagnostics'])} finding(s).")
    if not verdict:
        verdict.append("No diagnostic data was available at generation time.")
    story.append(Spacer(1, 4))
    story.append(Paragraph(" ".join(_esc(v) for v in verdict), BODY))

    # ---- Hosted scan
    h = report.get("hosted_scan")
    story.append(Paragraph("2. Hosted Server Scan", H2))
    if not h:
        story.append(Paragraph("No hosted scan has been run in this session.", BODY))
    else:
        story.append(Paragraph(_esc(h.get("note", "")), NOTE))
        m = h.get("metrics", {})
        story.append(_table([
            ["Field", "Value"],
            ["Run at", _fmt_ts(h.get("run_at")) + ("  (demo scenario)" if h.get("is_demo") else "")],
            ["Server", f"{h.get('server_hostname')}  ({h.get('server_ip')})"],
            ["Health score", f"{h.get('health_score')}/100 — {h.get('status')}"],
            ["Scan duration", f"{h.get('duration_ms', 0)} ms"],
        ], col_widths=[35 * mm, W - 35 * mm]))
        story.append(Paragraph("2.1 Layer-by-layer results", H3))
        story.append(_table([
            ["Layer", "Result", "Measured"],
            ["Default gateway", _status_cell(m.get("gateway")), "ICMP echo to the default route" + (" — filtered but forwarding" if "ICMP" in str(m.get("gateway")) else "")],
            ["Internet reachability", _status_cell(m.get("internet")), f"{_num(m.get('latency_ms'), ' ms')} average latency, {_num(m.get('packet_loss_pct'), '%')} packet loss to 8.8.8.8"],
            ["DNS resolution", _status_cell(m.get("dns")), "Resolution of the configured test domains"],
            ["TCP services", _status_cell(m.get("tcp")), "TCP connect to the configured host/ports (2 s timeout)"],
        ], col_widths=[38 * mm, 28 * mm, W - 66 * mm]))
        story.append(Paragraph("2.2 Findings", H3))
        diags = h.get("diagnostics") or []
        if not diags:
            story.append(Paragraph("No findings.", BODY))
        for d in diags:
            block = [
                Paragraph(f"<font color='{_sev_color(d.get('severity')).hexval()}'><b>[{_esc(str(d.get('severity', '')).upper())}]</b></font> <b>{_esc(d.get('title'))}</b>  <font color='#475569'>(confidence: {_esc(d.get('confidence'))})</font>", BODY),
                Paragraph(f"<b>Likely cause:</b> {_esc(d.get('likely_cause'))}", BODY),
            ]
            if d.get("evidence"):
                block.append(Paragraph("<b>Evidence:</b>", BODY))
                for e in d["evidence"]:
                    block.append(Paragraph(f"• {_esc(e)}", SMALL))
            if d.get("recommended_checks"):
                block.append(Paragraph("<b>Recommended checks:</b>", BODY))
                for c in d["recommended_checks"]:
                    block.append(Paragraph(f"• {_esc(c)}", SMALL))
            block.append(Spacer(1, 5))
            story.append(KeepTogether(block))

    # ---- Devices
    devices = report.get("devices") or []
    story.append(Paragraph("3. Managed Devices (agent telemetry)", H2))
    if not devices:
        story.append(Paragraph("No devices are registered to this account. Install the agent from the Getting Started page to include your own machines.", BODY))
    else:
        rows = [["Device", "Platform", "IP", "Gateway", "Latency", "Loss", "DNS", "Last report"]]
        for d in devices:
            t = d.get("latest_telemetry") or {}
            last = "never"
            if t:
                last = f"{t.get('age_seconds', 0) // 60} min ago" + (" — STALE" if t.get("stale") else "")
            rows.append([d.get("name"), d.get("platform"), d.get("ip_address"), t.get("gateway_ip") or "—",
                         _num(t.get('latency_ms'), ' ms') if t else "—", _num(t.get('packet_loss_pct'), '%') if t else "—",
                         _status_cell("OK" if t.get("dns_healthy") else "FAIL") if t else "—", last])
        story.append(_table(rows, col_widths=[34 * mm, 20 * mm, 27 * mm, 27 * mm, 18 * mm, 14 * mm, 14 * mm, W - 154 * mm], font=7.5))
        for d in devices:
            t = d.get("latest_telemetry") or {}
            block = [Paragraph(f"3.{devices.index(d) + 1} {_esc(d.get('name'))}", H3)]
            block.append(_table([
                ["Field", "Value"],
                ["Hostname / platform", f"{d.get('hostname')} / {d.get('platform')}"],
                ["IP address", d.get("ip_address")],
                ["Agent version", d.get("agent_version")],
                ["Registered", _fmt_ts(d.get("last_seen"))],
                ["Latest report", _fmt_ts(t.get("reported_at")) if t else "never"],
                ["Backup target", ("Yes — serves " + ", ".join(d.get("backup_protocols") or [])) if d.get("is_backup_target") else "No"],
            ], col_widths=[38 * mm, W - 38 * mm]))
            if t.get("backup_ports"):
                block.append(Spacer(1, 4))
                block.append(Paragraph("Backup-protocol ports as checked by the agent on the host itself (127.0.0.1):", SMALL))
                prow = [["Service", "Port", "Listening", "Scored for this target"]]
                wanted = set(d.get("backup_protocols") or get_backup_port_map().values())
                for pinfo in t["backup_ports"]:
                    prow.append([pinfo.get("service"), str(pinfo.get("port")), _status_cell("OPEN" if pinfo.get("open") else "CLOSED"),
                                 "yes" if pinfo.get("service") in wanted else "no (not selected)"])
                block.append(_table(prow, col_widths=[30 * mm, 20 * mm, 28 * mm, W - 78 * mm]))
            story.append(KeepTogether(block))

    # ---- Trends
    story.append(Paragraph("4. Trends", H2))
    any_chart = False
    section = 0
    if history:
        pts_h = _hours_ago_series(history, "score", now)
        pts_l = _hours_ago_series(history, "latency", now)
        pts_p = _hours_ago_series(history, "packet_loss", now)
        if len(pts_h) >= 2:
            any_chart = True
            section += 1
            story.append(Paragraph(f"4.{section} Hosted scan history ({len(history)} saved diagnostic runs)", H3))
            story.append(_line_chart("Health score (0–100)", [("health", pts_h, CYAN)], y_label="score", x_label="hours before report generation"))
            story.append(_line_chart("Latency (ms) and packet loss (%)", [("latency ms", pts_l, NAVY), ("loss %", pts_p, RED)], x_label="hours before report generation"))
    for d in devices:
        rows = telemetry_history.get(d["id"]) or []
        pts_l = _hours_ago_series(rows, "latency_ms", now)
        pts_p = _hours_ago_series(rows, "packet_loss", now)
        if len(pts_l) >= 2:
            any_chart = True
            section += 1
            story.append(Paragraph(f"4.{section} Agent telemetry — {_esc(d.get('name'))} (last 24 h, {len(rows)} reports)", H3))
            story.append(_line_chart("Latency to 8.8.8.8 (ms)", [("latency ms", pts_l, NAVY)], x_label="hours before report generation"))
            story.append(_line_chart("Packet loss (%)", [("loss %", pts_p, RED)], x_label="hours before report generation"))
    if not any_chart:
        story.append(Paragraph("Not enough history yet for trend graphs. Saved diagnostic runs (signed in) and agent reports accumulate over time; graphs appear once at least two data points exist.", BODY))

    # ---- Backup readiness
    b = report.get("backup_readiness")
    story.append(Paragraph("5. Backup Readiness", H2))
    if not b:
        story.append(Paragraph("No devices are tagged as backup targets.", BODY))
    else:
        story.append(Paragraph(f"Overall readiness: <b>{b.get('backupReadinessScore')}/100</b> across {len(b.get('targets', []))} target(s).", BODY))
        for i, t in enumerate(b.get("targets", []), 1):
            br = t["backupReadiness"]
            block = [Paragraph(f"5.{i} {_esc(t.get('name'))} — {_esc(br.get('verdict', '').upper())} ({br.get('score')}/100)", H3)]
            block.append(_table([
                ["Check", "Result", "Detail"],
                ["Reachability", _status_cell(t.get("reachability", "").upper()), "Agent heartbeat within the last 3 minutes" if t.get("reachability") != "down" else "No recent agent report / host unreachable"],
                ["DNS", _status_cell("RESOLVED" if t.get("dnsResolved") else "FAILED"), "Name resolution as seen by the agent"],
                ["Link quality", _status_cell("OK" if (t.get("packetLossPct") or 0) == 0 else "WARNING"), f"{_num(t.get('latencyMs'), ' ms')} latency, {_num(t.get('packetLossPct'), '%')} packet loss"],
                ["Protocols",
                 _status_cell("N/A" if not t.get("ports") else ("OPEN" if all(p.get("open") for p in t["ports"]) else "CLOSED")),
                 (", ".join(f"{p['service']} {'open' if p['open'] else 'closed'}" for p in t["ports"]) if t.get("ports")
                  else ("not evaluated — host down" if t.get("reachability") == "down" else "none selected"))],
                ["SLA fit", _status_cell("MEETS" if br.get("willMeetSla") else "MISSES"), f"estimated {br.get('estimatedTransferHours')} h against a {br.get('slaWindowHours')} h window"],
            ], col_widths=[30 * mm, 26 * mm, W - 56 * mm]))
            # Proof of the SLA estimate — recompute with the documented formula.
            dataset_gb = float((report.get("parameters") or {}).get("dataset_size_gb", 500.0))
            sla_h = br.get("slaWindowHours", 4.0)
            lat, loss = float(t.get("latencyMs") or 0), float(t.get("packetLossPct") or 0)
            est = estimate_sla(lat, loss, dataset_gb, sla_h)
            block.append(Spacer(1, 4))
            block.append(Paragraph("SLA estimate — how it was computed", SMALL))
            block.append(_table([
                ["Step", "Value"],
                ["Assumed link capacity", f"{DEFAULT_NOMINAL_LINK_MBPS:.0f} Mbps (placeholder; override with the real link speed if known)"],
                ["Latency factor", f"{_latency_factor(lat):.2f}  (from {_num(lat, ' ms')})"],
                ["Packet-loss factor", f"{_loss_factor(loss):.3f}  (1 / (1 + 12·√(loss/100)), loss = {_num(loss, '%')})"],
                ["Effective throughput", f"{est['effective_mbps']} Mbps"],
                ["Dataset", f"{dataset_gb:g} GB = {dataset_gb * 8192:,.0f} Mb"],
                ["Estimated transfer", f"{est['estimated_transfer_hours']} h  →  {'meets' if est['will_meet_sla'] else 'misses'} the {sla_h} h window"],
            ], col_widths=[40 * mm, W - 40 * mm], font=7.5))
            story.append(KeepTogether(block))
        diags = b.get("diagnostics") or []
        story.append(Paragraph(f"5.{len(b.get('targets', [])) + 1} Findings", H3))
        if not diags:
            story.append(Paragraph("No findings — every selected protocol is listening and the SLA window is comfortably met.", BODY))
        for d in diags:
            story.append(KeepTogether([
                Paragraph(f"<font color='{_sev_color(d.get('severity')).hexval()}'><b>[{_esc(str(d.get('severity')).upper())} · {_esc(d.get('category'))}]</b></font> {_esc(d.get('message'))}", BODY),
                Paragraph(f"<b>Recommendation:</b> {_esc(d.get('recommendation'))}", SMALL),
                Spacer(1, 4),
            ]))

    # ---- Methodology appendix
    story.append(PageBreak())
    story.append(Paragraph("Appendix A. Methodology", H2))
    story.append(Paragraph("How each result in this report is produced, so it can be reproduced or challenged.", SMALL))
    story.append(Paragraph("A.1 Checks", H3))
    story.append(_table([
        ["Check", "Method"],
        ["Default gateway", "Lowest-metric default route from the OS routing table; 4 ICMP echo requests, 2 s timeout. A gateway that drops ICMP while the internet is reachable through it is reported as forwarding (ICMP filtered), not failed."],
        ["Internet", "4 ICMP echo requests to 8.8.8.8; average latency and packet loss are taken from the ping summary."],
        ["DNS", "getaddrinfo() for each configured domain; success if an address is returned."],
        ["TCP services", "TCP connect() to each configured host:port with a 2 s timeout."],
        ["Backup ports", "TCP connect() to NFS 2049, SMB 445, iSCSI 3260 and the replication port (default 10000). Agent-managed hosts check their own loopback; other targets are probed from the server. Only protocols selected for the target are scored."],
        ["Agent liveness", f"A device is 'up' if its agent reported within {AGENT_STALE_SECONDS} s; 'degraded' with packet loss; 'down' if stale."],
    ], col_widths=[35 * mm, W - 35 * mm], font=7.5))
    story.append(Paragraph("A.2 Health score (hosted scan)", H3))
    story.append(_table([
        ["Condition", "Deduction"],
        ["Gateway unreachable and internet unreachable", "−40"],
        ["Gateway silent to ICMP but internet reachable", "−3"],
        ["Internet unreachable", "−30"],
        ["All DNS lookups failed", "−15"],
        ["All TCP checks failed", "−15"],
        ["Packet loss", "−1 per % (max −20)"],
        ["Latency > 100 ms / > 300 ms", "−5 / −10"],
        ["Status", "HEALTHY ≥ 90 with no warning/critical findings; HEALTHY (WITH WARNINGS) ≥ 80; WARNING ≥ 60; CRITICAL below"],
    ], col_widths=[W - 60 * mm, 60 * mm], font=7.5))
    story.append(Paragraph("A.3 Backup readiness score", H3))
    story.append(_table([
        ["Condition", "Deduction"],
        ["Host down (no recent agent report / unreachable)", "−50"],
        ["DNS unresolved", "−25"],
        ["All selected backup ports closed", "−30"],
        ["Some selected backup ports closed", "−22"],
        ["Estimated transfer ≤ 70% of SLA window", "0"],
        ["Estimated transfer ≤ 100% of window (tight)", "−18"],
        ["Estimated transfer exceeds window", "−35"],
        ["Verdict", f"ready ≥ {READY_THRESHOLD}; at-risk ≥ {AT_RISK_THRESHOLD}; not-ready below"],
    ], col_widths=[W - 60 * mm, 60 * mm], font=7.5))
    story.append(Paragraph("A.4 SLA estimate", H3))
    story.append(Paragraph(
        "effective_mbps = nominal_link_mbps × latency_factor × loss_factor; "
        "hours = dataset_gb × 8192 / (effective_mbps × 3600). Latency factor: &lt;50 ms 1.00, ≤100 ms 0.85, ≤200 ms 0.55, &gt;200 ms 0.25. "
        "Loss factor: 1 / (1 + 12·√(loss/100)), floor 0.05. The estimate is directional — it is derived from passively measured latency and loss, "
        "not an active bandwidth test — and is capped at 999 h.", SMALL))
    story.append(Spacer(1, 8))
    story.append(Paragraph("Appendix B. Data provenance", H2))
    story.append(Paragraph(
        "This PDF is rendered from the same JSON document available via 'JSON Export' (report_version "
        f"{_esc(report.get('report_version'))}); the two never diverge. Hosted-scan values come from the server's most recent "
        "diagnostic run in this session. Device values come from the latest telemetry each agent posted. Backup readiness is "
        "evaluated from that telemetry for agent-managed devices and from live server-side probes for any other target.", SMALL))

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
