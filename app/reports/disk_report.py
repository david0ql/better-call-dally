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

        rows: list[str] = []
        for server in servers:
            if not server.enabled:
                continue
            try:
                stats = await asyncio.to_thread(
                    SSHClientPool.get().collect_with_disks,
                    server,
                )
                if stats.error:
                    rows.append(f"<tr><td><b>{server.name or server.host}</b></td><td colspan='4' style='color:red'>{stats.error}</td></tr>")
                else:
                    disks = stats.disks.disks or []
                    if not disks:
                        rows.append(f"<tr><td><b>{server.name or server.host}</b></td><td colspan='4'>Sin datos de discos</td></tr>")
                    else:
                        for disk in disks:
                            usage_pct = 0.0
                            if disk.total_bytes and disk.used_bytes:
                                usage_pct = (disk.used_bytes / disk.total_bytes) * 100
                            color = "#d32f2f" if usage_pct >= ALERT_DISK_PERCENT else "#f57c00" if usage_pct >= WARN_DISK_PERCENT else "green"
                            rows.append(
                                f"<tr>"
                                f"<td>{server.name or server.host}</td>"
                                f"<td>{disk.device}</td>"
                                f"<td>{disk.mount}</td>"
                                f"<td>{format_bytes(disk.total_bytes)}</td>"
                                f"<td>{format_bytes(disk.used_bytes)}</td>"
                                f"<td style='color:{color};font-weight:bold'>{usage_pct:.1f}%</td>"
                                f"</tr>"
                            )
            except Exception as exc:
                rows.append(f"<tr><td><b>{server.name or server.host}</b></td><td colspan='4' style='color:red'>Error: {exc}</td></tr>")

        table = (
            "<table border='1' cellpadding='8' cellspacing='0' style='border-collapse:collapse'>"
            "<thead><tr style='background:#1976d2;color:white'>"
            "<th>Servidor</th><th>Dispositivo</th><th>Punto Montaje</th>"
            "<th>Total</th><th>Usado</th><th>Uso %</th>"
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table>"
        )

        body = f"""
        <h2>Reporte de Discos - Better Call Dally</h2>
        <p><em>Generado: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')} - Colombia</em></p>
        <p>Umbral de alerta: {ALERT_DISK_PERCENT}% | Umbral de advertencia: {WARN_DISK_PERCENT}%</p>
        {table}
        <p style='margin-top:20px;font-size:12px;color:#666'>
        Este es un reporte automático de monitoreo de servidores.
        </p>
        """

        subject = f"📊 Reporte de Discos - {datetime.datetime.now().strftime('%Y-%m-%d')} - Better Call Dally"

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
            async with httpx.AsyncClient(timeout=30.0) as client:
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


_report_service: DiskReportService | None = None


def get_report_service() -> DiskReportService:
    global _report_service
    if _report_service is None:
        _report_service = DiskReportService()
    return _report_service