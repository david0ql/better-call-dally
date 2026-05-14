from __future__ import annotations

import asyncio
import datetime
import threading

import httpx

from app.core.config import EMAIL_ACCESS_TOKEN, EMAIL_API_URL, EMAIL_ENABLED, EMAIL_TO, REPORT_HOUR, REPORT_MINUTE
from app.infra.ssh_pool import SSHClientPool
from app.servers.models import Server

ALERT_DISK_PERCENT = 90
WARN_DISK_PERCENT = 80


def format_bytes(value: int | None) -> str:
    if value is None:
        return "n/a"
    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    size = float(value)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"


def parse_email_addresses(value: str) -> list[str]:
    if not value:
        return []
    return [addr.strip() for addr in value.split(",") if addr.strip()]


class DiskReportService:
    def __init__(self) -> None:
        self._timer: threading.Timer | None = None
        self._shutdown = threading.Event()

    def start(self) -> None:
        if not EMAIL_ENABLED:
            print("[DiskReport] Email reporting disabled")
            return
        if not EMAIL_API_URL or not EMAIL_ACCESS_TOKEN or not EMAIL_TO:
            print("[DiskReport] Email not configured (missing BCD_EMAIL_API_URL, BCD_EMAIL_ACCESS_TOKEN or BCD_EMAIL_TO)")
            return
        print("[DiskReport] Email reporting enabled")
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None
        self._schedule_next()

    def stop(self) -> None:
        self._shutdown.set()
        if self._timer:
            self._timer.cancel()

    def _schedule_next(self) -> None:
        def target() -> None:
            if self._shutdown.is_set():
                return
            if self._loop is not None and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(self._send_report_if_needed(), self._loop)
            else:
                print("[DiskReport] No event loop available for scheduled report")
            self._schedule_next()

        now = datetime.datetime.now()
        scheduled_time = datetime.datetime(now.year, now.month, now.day, REPORT_HOUR, REPORT_MINUTE)
        if scheduled_time <= now:
            scheduled_time += datetime.timedelta(days=1)
        while scheduled_time.weekday() >= 5:
            scheduled_time += datetime.timedelta(days=1)
        delay_seconds = (scheduled_time - now).total_seconds()
        self._timer = threading.Timer(delay_seconds, target)
        self._timer.daemon = True
        self._timer.start()

    async def _send_report_if_needed(self) -> None:
        now = datetime.datetime.now()
        if now.weekday() >= 5:
            return
        if now.hour != REPORT_HOUR or now.minute != REPORT_MINUTE:
            return
        await self.send_disk_report()

    async def send_disk_report(self) -> bool:
        if not EMAIL_API_URL or not EMAIL_ACCESS_TOKEN or not EMAIL_TO:
            print("[DiskReport] Email not configured, skipping")
            return False

        servers = SSHClientPool.get().get_all_servers()
        if not servers:
            return False

        cards: list[str] = []
        for server in servers:
            if not server.enabled:
                continue
            try:
                stats = await asyncio.to_thread(
                    SSHClientPool.get().collect_with_disks,
                    server,
                )
                if stats.error:
                    cards.append(self._error_card(server, stats.error))
                else:
                    cards.append(self._server_card(server, stats))
            except Exception as exc:
                cards.append(self._error_card(server, str(exc)))

        body = f"""<!DOCTYPE html>
<html lang="es">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:'Segoe UI',Arial,sans-serif">
<div style="max-width:700px;margin:0 auto;padding:20px">

<div style="background:linear-gradient(135deg,#1a237e,#283593);color:white;border-radius:12px 12px 0 0;padding:24px 28px">
<div style="font-size:28px;font-weight:700;letter-spacing:-0.5px">Better Call Dally</div>
<div style="font-size:14px;opacity:0.85;margin-top:4px">Reporte de Monitoreo - {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} COT</div>
</div>

{''.join(cards)}

<div style="text-align:center;padding:16px;font-size:11px;color:#999;border-top:1px solid #e0e0e0">
Better Call Dally · Monitoreo Automatizado de Servidores
</div>

</div>
</body>
</html>"""

        subject = f"Reporte de Monitoreo - {datetime.datetime.now().strftime('%Y-%m-%d')} - Better Call Dally"

        destination = parse_email_addresses(EMAIL_TO)

        payload = {
            "destination": destination,
            "subject": subject,
            "isHtml": True,
            "body": body,
            "cc": [],
            "files": [],
            "nbTipo": 1,
            "nbEmpresa": 1,
        }

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    EMAIL_API_URL,
                    json=payload,
                    headers={
                        "Content-Type": "application/json",
                        "x-access-token": EMAIL_ACCESS_TOKEN,
                    },
                )
                print(f"[DiskReport] Email sent: {response.status_code}")
                return True
        except Exception as exc:
            print(f"[DiskReport] Email failed: {exc}")
            return False

    def _bar(self, pct: float, width: str = "100%") -> str:
        color = "#d32f2f" if pct >= ALERT_DISK_PERCENT else "#f57c00" if pct >= WARN_DISK_PERCENT else "#43a047"
        return f"""<div style="background:#e0e0e0;border-radius:6px;height:10px;width:{width}">
<div style="background:{color};border-radius:6px;height:10px;width:{min(pct,100):.1f}%"></div></div>"""

    def _value_pct(self, pct: float) -> str:
        color = "#d32f2f" if pct >= ALERT_DISK_PERCENT else "#f57c00" if pct >= WARN_DISK_PERCENT else "#43a047"
        return f"""<span style="font-weight:700;color:{color}">{pct:.1f}%</span>"""

    def _server_card(self, server: Server, stats) -> str:
        cpu_pct = stats.cpu.usage_percent or 0.0
        mem_total = stats.memory.total_bytes or 0
        mem_used = stats.memory.used_bytes or 0
        mem_pct = (mem_used / mem_total * 100) if mem_total else 0.0

        cpu_info = f"{cpu_pct:.1f}%" if stats.cpu.usage_percent is not None else "n/a"
        cores = f"{stats.cpu.cores}" if stats.cpu.cores else "?"

        disk_rows = ""
        disks = stats.disks.disks or []
        for d in disks:
            pct = (d.used_bytes / d.total_bytes * 100) if d.total_bytes and d.used_bytes else 0.0
            disk_rows += f"""<tr>
<td style="padding:6px 10px;border-bottom:1px solid #eee;font-family:monospace;font-size:13px">{d.device}</td>
<td style="padding:6px 10px;border-bottom:1px solid #eee;font-size:13px">{d.mount}</td>
<td style="padding:6px 10px;border-bottom:1px solid #eee;font-size:13px">{format_bytes(d.total_bytes)}</td>
<td style="padding:6px 10px;border-bottom:1px solid #eee;font-size:13px">{format_bytes(d.used_bytes)}</td>
<td style="padding:6px 10px;border-bottom:1px solid #eee;font-size:13px">{self._value_pct(pct)}</td>
<td style="padding:6px 10px;border-bottom:1px solid #eee;width:120px">{self._bar(pct)}</td>
</tr>"""

        return f"""<div style="background:white;border-radius:8px;margin:16px 0;box-shadow:0 1px 4px rgba(0,0,0,0.08);overflow:hidden">

<div style="padding:16px 20px;border-bottom:2px solid #1a237e">
<div style="font-size:17px;font-weight:700;color:#1a237e">{server.name or server.host}</div>
<div style="font-size:12px;color:#888;margin-top:2px">{server.host}</div>
</div>

<div style="display:flex;gap:12px;padding:12px 20px;background:#fafafa;border-bottom:1px solid #eee">
<div style="flex:1;text-align:center">
<div style="font-size:11px;color:#888;text-transform:uppercase">CPU ({cores} cores)</div>
<div style="font-size:20px;font-weight:700;margin:2px 0">{cpu_info}</div>
{self._bar(cpu_pct, "80%")}
</div>
<div style="flex:1;text-align:center">
<div style="font-size:11px;color:#888;text-transform:uppercase">RAM</div>
<div style="font-size:20px;font-weight:700;margin:2px 0">{format_bytes(mem_used)}</div>
{self._bar(mem_pct, "80%")}
</div>
<div style="flex:1;text-align:center">
<div style="font-size:11px;color:#888;text-transform:uppercase">Uptime</div>
<div style="font-size:16px;font-weight:600;margin:2px 0;color:#555">{stats.uptime.human}</div>
</div>
</div>

<table style="width:100%;border-collapse:collapse;font-size:13px">
<thead>
<tr style="background:#f5f5f5;border-bottom:2px solid #e0e0e0">
<th style="padding:8px 10px;text-align:left;font-weight:600;color:#555">Dispositivo</th>
<th style="padding:8px 10px;text-align:left;font-weight:600;color:#555">Montaje</th>
<th style="padding:8px 10px;text-align:left;font-weight:600;color:#555">Total</th>
<th style="padding:8px 10px;text-align:left;font-weight:600;color:#555">Usado</th>
<th style="padding:8px 10px;text-align:left;font-weight:600;color:#555">Uso</th>
<th style="padding:8px 10px;text-align:left;font-weight:600;color:#555">Barra</th>
</tr>
</thead>
<tbody>{disk_rows}</tbody>
</table>

</div>"""

    def _error_card(self, server: Server, error: str) -> str:
        return f"""<div style="background:white;border-radius:8px;margin:16px 0;box-shadow:0 1px 4px rgba(0,0,0,0.08);overflow:hidden;border-left:4px solid #d32f2f">
<div style="padding:16px 20px">
<div style="font-size:17px;font-weight:700;color:#d32f2f">{server.name or server.host}</div>
<div style="font-size:12px;color:#888;margin-top:2px">{server.host}</div>
<div style="color:#d32f2f;font-size:13px;margin-top:8px">{error}</div>
</div>
</div>"""


_report_service: DiskReportService | None = None


def get_report_service() -> DiskReportService:
    global _report_service
    if _report_service is None:
        _report_service = DiskReportService()
    return _report_service