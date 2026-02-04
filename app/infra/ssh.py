from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
import base64
from pathlib import Path

import paramiko

import socket

from app.core.config import ROOT_DIR, SSH_COMMAND_TIMEOUT, SSH_TIMEOUT
from app.servers.models import Server
from app.stats.models import (
    CpuInfo,
    DiskInfo,
    HostStats,
    MemoryInfo,
    Pm2Info,
    Pm2Process,
    SupervisorInfo,
    SupervisorProcess,
    UptimeInfo,
)


@dataclass
class SshResult:
    stdout: str
    stderr: str
    exit_code: int


def redact_output(text: str, secret: str | None) -> str:
    if not text or not secret:
        return text
    lines = text.splitlines()
    filtered: list[str] = []
    for line in lines:
        if line.strip() == secret:
            continue
        filtered.append(line.replace(secret, "[redacted]"))
    result = "\n".join(filtered)
    if text.endswith("\n"):
        result += "\n"
    return result


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


def format_seconds(seconds: float | None) -> str:
    if seconds is None:
        return "n/a"
    remaining = int(seconds)
    days, remaining = divmod(remaining, 86400)
    hours, remaining = divmod(remaining, 3600)
    minutes, seconds = divmod(remaining, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def run_command(
    client: paramiko.SSHClient,
    command: str,
    *,
    use_pty: bool = False,
    login_shell: bool = False,
) -> SshResult:
    if login_shell:
        command = f"bash -lc {shlex.quote(command)}"
    stdin, stdout, stderr = client.exec_command(command, get_pty=use_pty)
    stdout.channel.settimeout(SSH_COMMAND_TIMEOUT)
    stderr.channel.settimeout(SSH_COMMAND_TIMEOUT)
    try:
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
    except socket.timeout:
        return SshResult(stdout="", stderr="command timeout", exit_code=124)
    return SshResult(stdout=out, stderr=err, exit_code=exit_status)


def run_sudo_command(
    client: paramiko.SSHClient,
    command: str,
    *,
    password: str,
    user: str = "root",
    login_shell: bool = False,
) -> SshResult:
    if login_shell:
        command = f"bash -lc {shlex.quote(command)}"
    sudo_inner = f"sudo -S -p '' -u {shlex.quote(user)} /bin/sh -c {shlex.quote(command)}"
    sudo_command = (
        "stty -echo >/dev/null 2>&1 || true; "
        f"{sudo_inner}; "
        "status=$?; "
        "stty echo >/dev/null 2>&1 || true; "
        "exit $status"
    )
    stdin, stdout, stderr = client.exec_command(sudo_command, get_pty=True)
    stdout.channel.settimeout(SSH_COMMAND_TIMEOUT)
    stderr.channel.settimeout(SSH_COMMAND_TIMEOUT)
    stdin.write(password + "\n")
    stdin.flush()
    try:
        exit_status = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
    except socket.timeout:
        return SshResult(stdout="", stderr="command timeout", exit_code=124)
    return SshResult(
        stdout=redact_output(out, password),
        stderr=redact_output(err, password),
        exit_code=exit_status,
    )


def read_public_key(path: Path) -> str:
    try:
        key_text = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Public key not found: {path}") from exc
    if not key_text:
        raise RuntimeError(f"Public key is empty: {path}")
    return key_text


def install_root_key(
    client: paramiko.SSHClient,
    key_text: str,
    *,
    ssh_user: str,
    password: str | None,
) -> None:
    key_b64 = base64.b64encode(key_text.encode("utf-8")).decode("ascii")
    script = (
        "umask 077; "
        "mkdir -p /root/.ssh; "
        "touch /root/.ssh/authorized_keys; "
        f"key=$(printf '%s' {shlex.quote(key_b64)} | base64 -d); "
        "grep -qxF \"$key\" /root/.ssh/authorized_keys || "
        "printf '%s\\n' \"$key\" >> /root/.ssh/authorized_keys; "
        "grep -qxF \"$key\" /root/.ssh/authorized_keys"
    )
    if ssh_user == "root":
        result = run_command(client, script, login_shell=True)
    else:
        if not password:
            raise RuntimeError("Password required to sudo to root for key install.")
        result = run_sudo_command(
            client,
            script,
            password=password,
            user="root",
            login_shell=True,
        )
    if result.exit_code != 0:
        message = (result.stderr or result.stdout).strip()
        raise RuntimeError(message or "Failed to install root key")


def fetch_memory(client: paramiko.SSHClient) -> tuple[int | None, int | None]:
    result = run_command(client, "cat /proc/meminfo")
    if result.exit_code != 0:
        return None, None
    total = None
    available = None
    for line in result.stdout.splitlines():
        if line.startswith("MemTotal:"):
            total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            available = int(line.split()[1]) * 1024
    if total is None or available is None:
        return None, None
    used = total - available
    return total, used


def fetch_uptime(client: paramiko.SSHClient) -> float | None:
    result = run_command(client, "cat /proc/uptime")
    if result.exit_code != 0:
        return None
    try:
        return float(result.stdout.split()[0])
    except (ValueError, IndexError):
        return None


def fetch_disk(client: paramiko.SSHClient) -> tuple[int | None, int | None]:
    result = run_command(client, "df -B1 /")
    if result.exit_code != 0:
        return None, None
    lines = result.stdout.splitlines()
    if len(lines) < 2:
        return None, None
    parts = lines[1].split()
    if len(parts) < 6:
        return None, None
    try:
        total = int(parts[1])
        used = int(parts[2])
    except ValueError:
        return None, None
    return total, used


def fetch_cpu(client: paramiko.SSHClient) -> tuple[int | None, float | None]:
    cores_result = run_command(client, "command -v nproc >/dev/null 2>&1 && nproc || getconf _NPROCESSORS_ONLN")
    if cores_result.exit_code == 0:
        try:
            cores = int(cores_result.stdout.strip())
        except (TypeError, ValueError):
            cores = None
    else:
        cores = None

    usage_script = (
        "read cpu user nice system idle iowait irq softirq steal guest guest_nice < /proc/stat; "
        "total1=$((user+nice+system+idle+iowait+irq+softirq+steal)); "
        "idle1=$((idle+iowait)); "
        "sleep 0.5; "
        "read cpu user nice system idle iowait irq softirq steal guest guest_nice < /proc/stat; "
        "total2=$((user+nice+system+idle+iowait+irq+softirq+steal)); "
        "idle2=$((idle+iowait)); "
        "total=$((total2-total1)); "
        "idle=$((idle2-idle1)); "
        "if [ \"$total\" -gt 0 ]; then "
        "usage=$(awk \"BEGIN {print (1-($idle/$total))*100}\"); "
        "printf \"%.2f\" \"$usage\"; "
        "else echo \"0.00\"; "
        "fi"
    )
    usage_result = run_command(client, usage_script, login_shell=True)
    if usage_result.exit_code == 0:
        try:
            usage_percent = float(usage_result.stdout.strip())
        except (TypeError, ValueError):
            usage_percent = None
    else:
        usage_percent = None
    return cores, usage_percent


def extract_json_array(text: str) -> list[dict] | None:
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "[":
            continue
        try:
            data, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data
    return None


def build_pm2_script(pm2_home: str | None) -> str:
    env_parts: list[str] = []
    if pm2_home:
        env_parts.append(f"PM2_HOME={shlex.quote(pm2_home)}")
    env_prefix = " ".join(env_parts)
    bootstrap = (
        "set -o pipefail; "
        "[ -f ~/.bashrc ] && . ~/.bashrc >/dev/null 2>&1; "
        "[ -f ~/.profile ] && . ~/.profile >/dev/null 2>&1; "
        "[ -s ~/.nvm/nvm.sh ] && . ~/.nvm/nvm.sh >/dev/null 2>&1; "
    )
    return f"{bootstrap}{env_prefix} pm2 jlist".strip()


def fetch_pm2_details(
    client: paramiko.SSHClient,
    *,
    pm2_user: str | None,
    pm2_home: str | None,
    sudo_password: str | None,
) -> tuple[Pm2Info, str | None]:
    script = build_pm2_script(pm2_home)
    if pm2_user:
        command = f"sudo -n -u {shlex.quote(pm2_user)} -H bash -lc {shlex.quote(script)}"
        if sudo_password:
            result = run_sudo_command(client, script, password=sudo_password, user=pm2_user, login_shell=True)
        else:
            result = run_command(client, command, use_pty=True)
    else:
        command = f"bash -lc {shlex.quote(script)}"
        result = run_command(client, command, use_pty=True)
    payload = extract_json_array(result.stdout) or extract_json_array(result.stderr)
    if payload is None:
        message = (result.stderr or result.stdout).strip()
        if not message and result.exit_code != 0:
            message = f"pm2 exit code {result.exit_code}"
        if not message:
            message = "pm2 returned no output"
        return Pm2Info(error=message), message

    details: list[Pm2Process] = []
    total_bytes = 0
    for proc in payload:
        pm2_env = proc.get("pm2_env", {}) if isinstance(proc, dict) else {}
        monit = proc.get("monit", {}) if isinstance(proc, dict) else {}
        memory = monit.get("memory")
        try:
            mem_bytes = int(memory) if memory is not None else None
        except (TypeError, ValueError):
            mem_bytes = None
        if mem_bytes is not None:
            total_bytes += mem_bytes
        details.append(
            Pm2Process(
                id=proc.get("pm_id"),
                name=proc.get("name"),
                namespace=pm2_env.get("namespace"),
                version=pm2_env.get("version"),
                mode=pm2_env.get("exec_mode"),
                pid=proc.get("pid"),
                uptime=pm2_env.get("pm_uptime"),
                restarts=pm2_env.get("restart_time"),
                status=pm2_env.get("status"),
                cpu=monit.get("cpu"),
                memory_bytes=mem_bytes,
                user=pm2_env.get("username"),
                watching=pm2_env.get("watching"),
            )
        )
    return (
        Pm2Info(
            total_memory_bytes=total_bytes,
            processes=len(details),
            details=details,
            error=None,
        ),
        None,
    )


SUPERVISOR_LINE = re.compile(
    r"^(?P<name>\S+)\s+"
    r"(?P<state>\S+)"
    r"(?:\s+pid\s+(?P<pid>\d+),\s+uptime\s+(?P<uptime>.+))?"
    r"(?:\s+-\s+(?P<message>.+))?$"
)


def parse_supervisor(lines: list[str]) -> SupervisorInfo:
    if not lines:
        return SupervisorInfo(total=None, running=None, details=None)
    total = 0
    running = 0
    details: list[SupervisorProcess] = []
    for line in lines:
        total += 1
        match = SUPERVISOR_LINE.match(line)
        if match:
            info = match.groupdict()
            state = info.get("state")
            if state == "RUNNING":
                running += 1
            pid = info.get("pid")
            details.append(
                SupervisorProcess(
                    name=info.get("name"),
                    state=state,
                    pid=int(pid) if pid and pid.isdigit() else None,
                    uptime=info.get("uptime"),
                    message=info.get("message"),
                    raw=line,
                )
            )
        else:
            details.append(SupervisorProcess(raw=line))
    return SupervisorInfo(total=total, running=running, details=details)


def fetch_supervisor(
    client: paramiko.SSHClient,
    *,
    sudo_password: str | None,
    ssh_user: str,
) -> SupervisorInfo:
    command = "command -v supervisorctl >/dev/null 2>&1 && supervisorctl status || true"
    result = run_command(client, command, login_shell=True)
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    joined = "\n".join(lines + [result.stderr.strip()]).lower()
    if (
        ("permission denied" in joined or "error:" in joined)
        and sudo_password
        and ssh_user != "root"
    ):
        result = run_sudo_command(
            client,
            "supervisorctl status || true",
            password=sudo_password,
            user="root",
            login_shell=True,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    return parse_supervisor(lines)


def resolve_key_path(server: Server) -> Path | None:
    key_path = Path(server.key_path) if server.key_path else None
    if key_path and not key_path.is_absolute():
        key_path = ROOT_DIR / key_path
    return key_path


def build_error_stats(server: Server, message: str) -> HostStats:
    return HostStats(
        server_id=server.id,
        server_name=server.name,
        host=server.host,
        user=server.user,
        port=server.port,
        tags=server.tags,
        error=message,
        cpu=CpuInfo(usage_human="n/a"),
        memory=MemoryInfo(total_human="n/a", used_human="n/a"),
        uptime=UptimeInfo(human="n/a"),
        disk=DiskInfo(total_human="n/a", used_human="n/a"),
        pm2=Pm2Info(error=message),
        supervisor=SupervisorInfo(),
    )


def collect_stats(client: paramiko.SSHClient, server: Server) -> HostStats:
    cpu_cores, cpu_usage = fetch_cpu(client)
    mem_total, mem_used = fetch_memory(client)
    uptime_seconds = fetch_uptime(client)
    disk_total, disk_used = fetch_disk(client)
    pm2_info, _ = fetch_pm2_details(
        client,
        pm2_user=server.pm2_user,
        pm2_home=server.pm2_home,
        sudo_password=server.password if server.user != "root" else None,
    )
    if pm2_info.error and server.password and server.user != "root":
        pm2_info, _ = fetch_pm2_details(
            client,
            pm2_user="root",
            pm2_home=server.pm2_home or "/root/.pm2",
            sudo_password=server.password,
        )
    supervisor_info = fetch_supervisor(
        client,
        sudo_password=server.password if server.user != "root" else None,
        ssh_user=server.user,
    )

    return HostStats(
        server_id=server.id,
        server_name=server.name,
        host=server.host,
        user=server.user,
        port=server.port,
        tags=server.tags,
        error=None,
        cpu=CpuInfo(
            cores=cpu_cores,
            usage_percent=cpu_usage,
            usage_human="n/a" if cpu_usage is None else f"{cpu_usage:.2f}%",
        ),
        memory=MemoryInfo(
            total_bytes=mem_total,
            used_bytes=mem_used,
            total_human=format_bytes(mem_total),
            used_human=format_bytes(mem_used),
        ),
        uptime=UptimeInfo(seconds=uptime_seconds, human=format_seconds(uptime_seconds)),
        disk=DiskInfo(
            mount="/",
            total_bytes=disk_total,
            used_bytes=disk_used,
            total_human=format_bytes(disk_total),
            used_human=format_bytes(disk_used),
        ),
        pm2=pm2_info,
        supervisor=supervisor_info,
    )


def gather_stats(server: Server) -> HostStats:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    key_path = resolve_key_path(server)
    try:
        client.connect(
            hostname=server.host,
            port=server.port,
            username=server.user,
            password=server.password,
            key_filename=str(key_path) if key_path else None,
            allow_agent=False,
            look_for_keys=False,
            timeout=SSH_TIMEOUT,
        )
        return collect_stats(client, server)
    except Exception as exc:
        return build_error_stats(server, str(exc))
    finally:
        client.close()


def provision_root_access(server: Server, public_key_path: Path) -> None:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    key_path = resolve_key_path(server)

    try:
        client.connect(
            hostname=server.host,
            port=server.port,
            username=server.user,
            password=server.password,
            key_filename=str(key_path) if key_path else None,
            allow_agent=False,
            look_for_keys=False,
            timeout=SSH_TIMEOUT,
        )
        key_text = read_public_key(public_key_path)
        install_root_key(
            client,
            key_text,
            ssh_user=server.user,
            password=server.password,
        )
    finally:
        client.close()
