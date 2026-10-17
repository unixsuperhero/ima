"""Persistent interactive agents owned by tuios, not the Now process."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

from .store import Run, Store, atomic_write

_NATIVE_STATES = {"starting", "idle", "working", "done", "needs_input", "errored"}
_TIMEOUT = 5


def _run(store: Store, run_id: str) -> Run:
    run = next((r for r in store.runs() if r.id == run_id and r.kind == "agent"), None)
    if run is None:
        raise ValueError(f"Not a persistent agent run: {run_id}")
    return run


def _target(session: str, server: str | None) -> list[str]:
    return ["--session", session] + (["--server", server] if server else [])


def _env(server: str | None) -> dict[str, str]:
    env = os.environ.copy()
    # A recorded default-server run must not follow a later caller's named server.
    env.pop("TUIOS_SERVER", None)
    env.pop("TUIOS_SOCKET", None)
    if server:
        env["TUIOS_SERVER"] = server
    return env


def _call(verb: str, session: str, server: str | None, *args: str) -> str:
    try:
        result = subprocess.run(["tuios", verb, *_target(session, server), *args],
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                timeout=_TIMEOUT, env=_env(server))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"tuios {verb} unavailable for session {session}: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"tuios {verb}: {result.stderr.strip() or result.stdout.strip()}")
    if "--json" in args:
        try:
            data = json.loads(result.stdout)
        except ValueError as exc:
            raise RuntimeError(f"Invalid tuios {verb} response; inspect the pane before retrying") from exc
        if not isinstance(data, dict) or data.get("success") is not True:
            raise RuntimeError(f"tuios {verb} did not confirm success: {result.stdout.strip()}")
    return result.stdout


def _windows(session: str, server: str | None) -> list[dict]:
    try:
        data = json.loads(_call("list-windows", session, server, "--json"))
        windows = data["windows"]
        if data.get("success") is not True or not isinstance(windows, list):
            raise ValueError("unsuccessful window listing")
        if any(not isinstance(w, dict) or not isinstance(w.get("window_id"), str) for w in windows):
            raise ValueError("invalid window entry")
        return windows
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"Invalid tuios window listing for {session}: {exc}") from exc


def _match(run: Run, windows: list[dict]) -> dict | None:
    # Only use the generated name until the full id has been learned. Never match
    # a renamed/replacement pane after its original id disappears.
    matches = [w for w in windows if w["window_id"] == run.agent_window or (
        run.agent_window == f"now-agent-{run.id}"
        and w.get("custom_name") == run.agent_window)]
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous owned pane for run {run.id}; inspect tuios manually")
    return matches[0] if matches else None


def _receipt_path(store: Store, run_id: str, suffix: str) -> Path:
    return store.run_log(run_id).with_suffix(suffix)


def _tail(path: Path) -> str:
    try:
        with path.open("rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            return f.read().decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""


def _receipt(store: Store, run: Run) -> dict:
    raw = _tail(_receipt_path(store, run.id, ".startup.json"))
    try:
        # A newly started daemon can print a banner before its JSON result.
        data = json.loads(raw[raw.index("{"):])
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _prompt_status(store: Store, run: Run) -> str | None:
    receipt = _receipt(store, run)
    if receipt:
        outcome = receipt.get("outcome")
        reason = receipt.get("reason") or receipt.get("error")
        if outcome in {"timeout", "blocked", "needs_input", "errored", "error"} or receipt.get("success") is False:
            return f"{outcome or 'error'}: {reason or 'Prompt delivery not confirmed'}. Open pane to inspect; prompt was not retried."
        status = receipt.get("prompt_status")
        if isinstance(status, str):
            return status
        if receipt.get("ready") is True:
            return f"{outcome or 'ready'}: inspect pane to confirm prompt delivery"
    error = _tail(_receipt_path(store, run.id, ".startup.err")).strip()
    if error:
        return f"Startup: {error[-2000:]}. Open pane to inspect; prompt was not retried."
    return run.prompt_status


def launch(store: Store, task_id: str, command: str, prompt: str, cwd: str) -> Run:
    if task_id not in store.tasks():
        raise ValueError(f"Task not found: {task_id}")
    if not prompt.strip() or "\0" in prompt:
        raise ValueError("Enter a nonempty prompt without NUL characters")
    argv = shlex.split(command)
    if not argv or any("\0" in arg for arg in argv):
        raise ValueError("Enter an agent command without NUL characters")
    directory = Path(cwd).expanduser().resolve()
    if not cwd.strip() or not directory.is_dir():
        raise ValueError(f"Working directory does not exist: {cwd}")
    executable = argv[0]
    if "/" in executable:
        executable = os.path.abspath(directory / executable)
    executable = shutil.which(executable)
    if not executable:
        raise ValueError(f"Agent executable not found: {argv[0]}")
    native = shutil.which("tuios")
    if not native:
        raise ValueError("Install tuios and put it on PATH before launching an agent")
    session = os.environ.get("TUIOS_SESSION") or "now-agents"
    server = os.environ.get("TUIOS_SERVER") or None
    if ":" in session:
        raise ValueError("Persistent agents require a local tuios session for NOW_HOME forwarding")
    run = store.run_start(task_id, "agent", prompt, str(directory))
    store.agent_update(run.id, agent_command=command, agent_session=session,
                       agent_server=server, agent_window=f"now-agent-{run.id}",
                       agent_state="starting", prompt_status="pending")
    args = [native, "start-agent", *_target(session, server), "--json",
            "--name", f"now-agent-{run.id}", "--cwd", str(directory),
            "--prompt", prompt, "--ready-timeout", "120000",
            "--env", f"NOW_HOME={store.root.resolve()}", "--env", f"NOW_TASK={task_id}",
            "--env", f"NOW_RUN={run.id}", shlex.quote(executable), "--", *argv[1:]]
    try:
        with _receipt_path(store, run.id, ".startup.json").open("w") as out, \
                _receipt_path(store, run.id, ".startup.err").open("w") as err:
            subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                             start_new_session=True, env=_env(server))
    except OSError as exc:
        store.agent_update(run.id, agent_state="errored", prompt_status=f"Launch failed: {exc}")
        raise RuntimeError(f"Could not start tuios: {exc}") from exc
    return _run(store, run.id)


def refresh(store: Store, task_id: str | None = None) -> list[Run]:
    runs = [r for r in store.runs(task_id) if r.kind == "agent"]
    groups: dict[tuple[str, str | None], list[Run]] = {}
    for run in runs:
        if run.agent_session and run.agent_window and run.agent_state != "stopped":
            groups.setdefault((run.agent_session, run.agent_server), []).append(run)
    for (session, server), members in groups.items():
        try:
            windows = _windows(session, server)
        except RuntimeError:
            windows = None
        for run in members:
            fields = {"prompt_status": _prompt_status(store, run)}
            receipt = _receipt(store, run)
            if (run.agent_window == f"now-agent-{run.id}"
                    and receipt.get("name") == run.agent_window
                    and receipt.get("session") == session
                    and isinstance(receipt.get("window_id"), str)):
                run.agent_window = receipt["window_id"]
                fields["agent_window"] = run.agent_window
            if windows is None:
                fields["agent_state"] = "unknown"
            else:
                try:
                    pane = _match(run, windows)
                except RuntimeError:
                    pane = None
                    windows = None
                if pane:
                    fields["agent_window"] = pane["window_id"]
                    state = pane.get("agent_state")
                    fields["agent_state"] = state if state in _NATIVE_STATES else "unknown"
                else:
                    # Native startup is detached; absence before it creates its pane
                    # is not proof that a previously existing pane has closed.
                    pending = run.agent_window == f"now-agent-{run.id}" and not _receipt(store, run)
                    fields["agent_state"] = "unknown" if windows is None else ("starting" if pending else "closed")
            store.agent_update(run.id, **fields)
    return [r for r in store.runs(task_id) if r.kind == "agent"]


def _owned_pane(store: Store, run_id: str) -> Run:
    run = _run(store, run_id)
    if not run.agent_session or not run.agent_window or run.agent_state == "stopped":
        raise RuntimeError(f"Run {run_id} has no available owned pane")
    try:
        pane = _match(run, _windows(run.agent_session, run.agent_server))
    except RuntimeError:
        store.agent_update(run_id, agent_state="unknown")
        raise
    if pane is None:
        pending = run.agent_window == f"now-agent-{run.id}" and not _receipt(store, run)
        store.agent_update(run_id, agent_state="starting" if pending else "closed")
        raise RuntimeError(f"Agent pane not available for run {run_id}; inspect its startup status or saved transcript")
    store.agent_update(run_id, agent_window=pane["window_id"])
    return _run(store, run_id)


def transcript(store: Store, run_id: str) -> str:
    run = _run(store, run_id)
    saved = _tail(store.run_log(run_id))
    if run.agent_state in {"closed", "stopped"}:
        return saved
    try:
        run = _owned_pane(store, run_id)
        text = _call("capture-pane", run.agent_session, run.agent_server,
                     "--window", run.agent_window, "--scrollback", "--lines", "200")
    except RuntimeError:
        if _run(store, run_id).agent_state not in {"closed", "stopped"}:
            store.agent_update(run_id, agent_state="unknown")
        return saved
    if text != saved:
        atomic_write(store.run_log(run_id), text)
    return text


def open_pane(store: Store, run_id: str) -> None:
    run = _owned_pane(store, run_id)
    _call("focus-window", run.agent_session, run.agent_server, run.agent_window, "--json")


def stop(store: Store, run_id: str) -> None:
    run = _owned_pane(store, run_id)
    transcript(store, run_id)
    _call("close-window", run.agent_session, run.agent_server, run.agent_window, "--json")
    store.agent_update(run_id, agent_state="stopped")


def observe(store: Store, session: str, window: str, state: str,
            server: str | None = None) -> bool | None:
    runs = [r for r in store.runs() if r.kind == "agent" and r.agent_session == session
            and r.agent_server == server]
    run = next((r for r in runs if r.agent_window == window), None)
    if run is None:
        pending = [r for r in runs if r.agent_window == f"now-agent-{r.id}"]
        if pending:
            try:
                windows = _windows(session, server)
                run = next((r for r in pending if (pane := _match(r, windows))
                            and pane["window_id"] == window), None)
            except RuntimeError:
                return None
    if run is None:
        return None
    return store.agent_update(run.id, agent_window=window,
                              agent_state=state if state in _NATIVE_STATES else "unknown")
