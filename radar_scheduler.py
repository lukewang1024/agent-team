"""Manage the optional systemd user timer for benchmark refreshes.

Keep the original unit names and marker so existing installations are updated.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid


MARKER = "# Managed by agent-team CodexRadar scheduler v1\n"
SERVICE = "agent-team-codexradar.service"
TIMER = "agent-team-codexradar.timer"
PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY")


def paths(env):
    config = Path(env.get("XDG_CONFIG_HOME") or Path(env.get("HOME", str(Path.home()))) / ".config")
    if not config.is_absolute():
        raise RuntimeError("XDG_CONFIG_HOME must be an absolute path")
    return (config / "systemd/user" / SERVICE, config / "systemd/user" / TIMER,
            config / "agent-team/codexradar-timer.env")


def systemd_quote(value, command=False):
    value = str(value)
    if any(char in value for char in "\x00\r\n"):
        raise RuntimeError("Scheduler paths and environment values cannot contain newlines")
    if command:
        value = value.replace("%", "%%").replace("$", "$$")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def scheduler_files(entrypoint, python, env):
    service, timer, env_file = paths(env)
    variables = {key: env[key] for key in ("PATH", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "CODEX_HOME")
                 if env.get(key)}
    for key in PROXY_KEYS:
        value = env.get(key) or env.get(key.lower())
        if value:
            variables[key] = env.get(key) or value
            variables[key.lower()] = env.get(key.lower()) or value
    environment = MARKER + "".join(key + "=" + systemd_quote(value) + "\n"
                                   for key, value in sorted(variables.items()))
    service_text = (MARKER + "[Unit]\nDescription=Refresh agent-team benchmark when due\n\n"
                    "[Service]\nType=oneshot\nEnvironmentFile=" + systemd_quote(env_file, command=True) + "\n"
                    "ExecStart=" + systemd_quote(python, command=True) + " "
                    + systemd_quote(entrypoint, command=True) + " recommendations sync\n")
    timer_text = (MARKER + "[Unit]\nDescription=Check agent-team benchmark refresh schedule\n\n"
                  "[Timer]\nOnCalendar=*:0/5\nPersistent=true\nUnit=" + SERVICE + "\n\n"
                  "[Install]\nWantedBy=timers.target\n")
    return {service: (service_text, 0o644), timer: (timer_text, 0o644), env_file: (environment, 0o600)}


def check_owned(paths_to_check):
    for path in paths_to_check:
        if path.is_symlink() or (path.exists() and (not path.is_file() or not path.read_text().startswith(MARKER))):
            raise RuntimeError("Refusing to replace unrelated scheduler file: " + str(path))


def atomic_write(path, text, mode):
    path.parent.mkdir(mode=0o700 if path.name.endswith(".env") else 0o755, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            output.write(text)
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def systemctl(*args, check=True):
    if not sys.platform.startswith("linux") or not shutil.which("systemctl"):
        raise RuntimeError("The optional scheduler requires a Linux systemd user manager")
    try:
        return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                              check=check, timeout=15)
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError("Could not operate the systemd user timer: " + type(error).__name__) from error


def enable(entrypoint, python, env):
    files = scheduler_files(entrypoint, python, env)
    check_owned(files)
    # Verify the manager before touching user files; do not print its environment.
    systemctl("show-environment")
    previously_enabled = systemctl("is-enabled", "--quiet", TIMER, check=False).returncode == 0
    previous = {path: (path.read_text(), path.stat().st_mode & 0o777) if path.exists() else None
                for path in files}
    try:
        for path, (contents, mode) in files.items():
            atomic_write(path, contents, mode)
        systemctl("daemon-reload")
        systemctl("enable", "--now", TIMER)
    except (OSError, RuntimeError):
        if not previously_enabled:
            try:
                systemctl("disable", "--now", TIMER, check=False)
            except RuntimeError:
                pass
        for path, old in previous.items():
            if old is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, *old)
        try:
            systemctl("daemon-reload", check=False)
        except RuntimeError:
            pass
        raise
    return {"status": "enabled", "timer": TIMER, "service": SERVICE}


def disable(env):
    owned = paths(env)
    check_owned(owned)
    if not any(path.exists() for path in owned):
        return {"status": "not_installed"}
    systemctl("disable", "--now", TIMER)
    for path in owned:
        path.unlink(missing_ok=True)
    systemctl("daemon-reload")
    return {"status": "disabled", "timer": TIMER}


def status(env):
    service, timer, _ = paths(env)
    check_owned((service, timer))
    if not service.exists() or not timer.exists():
        return {"installed": False, "enabled": False, "active": False, "timer": TIMER}
    systemctl("show-environment")
    enabled = systemctl("is-enabled", "--quiet", TIMER, check=False).returncode == 0
    active = systemctl("is-active", "--quiet", TIMER, check=False).returncode == 0
    return {"installed": True, "enabled": enabled, "active": active, "timer": TIMER}
