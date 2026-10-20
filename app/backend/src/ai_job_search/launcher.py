from __future__ import annotations

import argparse
import json
import multiprocessing
import queue
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import uvicorn

from .api import NativeDialogs, create_app
from .config import Config, data_home
from .errors import AppError


def selected_workspace() -> Path:
    path = data_home() / "workspace.json"
    if not path.exists():
        return data_home() / "workspace"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        return Path(settings["workspace"])
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError("invalid_launcher_settings", "The saved workspace selection is damaged. Preserve your workspace and repair the launcher settings.") from exc


def available_socket(port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if sys.platform == "win32" else socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(128)
    except BaseException:
        listener.close()
        raise
    return listener


class DialogBridge(NativeDialogs):
    def __init__(self):
        self.requests: queue.Queue = queue.Queue()

    def choose_directory(self) -> Path | None:
        result: queue.Queue = queue.Queue(maxsize=1)
        self.requests.put(result)
        try:
            value = result.get(timeout=600)
        except queue.Empty as exc:
            raise AppError("directory_dialog_timeout", "The folder picker timed out. Open it again.", 409) from exc
        return Path(value) if value else None


def start_server(config: Config, dialogs: NativeDialogs | None = None, on_shutdown=None):
    listener = available_socket(int(config.origin.rsplit(":", 1)[1]))
    config.origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
    try:
        app = create_app(config, dialogs=dialogs, on_shutdown=on_shutdown)
    except BaseException:
        listener.close()
        raise
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=listener.getsockname()[1],
                                           log_level="warning", access_log=False, log_config=None,
                                           timeout_graceful_shutdown=3))
    errors: list[BaseException] = []

    def serve() -> None:
        try:
            server.run(sockets=[listener])
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=serve, daemon=False, name="local-api")
    thread.start()
    deadline = time.monotonic() + 15
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if errors or not thread.is_alive():
            listener.close()
            raise AppError("startup_failed", "The local service could not start. Another instance may already own this workspace.")
        try:
            with opener.open(config.origin + "/healthz", timeout=0.4) as response:
                if response.status == 200:
                    return app, server, thread, listener
        except (OSError, urllib.error.URLError):
            time.sleep(0.05)
    server.should_exit = True
    thread.join(timeout=10)
    listener.close()
    raise AppError("startup_timeout", "Startup took too long. Check available disk space and whether another app instance is running.")


def smoke_test(config: Config) -> dict:
    import io
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    import claude_agent_sdk
    import httpx
    import keyring
    from pypdf import PdfWriter

    from . import __version__
    from .documents import DocumentService
    from .models import Profile
    from .processes import command

    with TemporaryDirectory(prefix="AI Job Search smoke \u00e9 ") as directory:
        config.workspace = Path(directory) / "Workspace"
        config.origin = "http://127.0.0.1:0"
        config.start_worker = True
        with patch("ai_job_search.runtime.Credentials.connected", return_value=False):
            started = time.monotonic()
            app, server, thread, listener = start_server(config)
            try:
                with httpx.Client(base_url=config.origin, trust_env=False, timeout=10) as client:
                    response = client.get("/")
                    if response.status_code != 200 or 'id="root"' not in response.text:
                        raise AppError("ui_not_bundled", "The built interface was not included in this package.")
                    startup_seconds = round(time.monotonic() - started, 3)
                    response = client.post("/api/v1/session/bootstrap", json={"token": app.state.sessions.mint()},
                                           headers={"Origin": config.origin})
                    response.raise_for_status()
                    if "csrf_token" not in response.json():
                        raise AppError("bootstrap_failed", "The local session handshake failed.")
                    client.headers.update({
                        "Origin": config.origin, "X-CSRF-Token": response.json()["csrf_token"],
                        "Idempotency-Key": "local-package-smoke",
                    })
                    response = client.get("/api/v1/profile")
                    response.raise_for_status()
                    profile = response.json()
                    response = client.post("/api/v1/profile/changes", json={
                        "profile": Profile(name="Synthetic Package Check").model_dump(mode="json"),
                        "expected_revision": profile["revision"],
                    })
                    response.raise_for_status()
                    run_id = response.json()["run_id"]
                    deadline = time.monotonic() + 30
                    while time.monotonic() < deadline:
                        response = client.get("/api/v1/runs/" + run_id)
                        response.raise_for_status()
                        run = response.json()
                        if run["status"] == "awaiting_user":
                            break
                        if run["status"] in ("failed", "interrupted", "cancelled"):
                            raise AppError("packaged_worker_failed", run["error"] or "The worker did not preserve its approval.")
                        time.sleep(0.1)
                    else:
                        raise AppError("packaged_worker_timeout", "The packaged worker did not complete its local checkpoint.")
                    if client.get("/api/v1/profile").json()["data"]["name"]:
                        raise AppError("approval_not_enforced", "An unapproved profile change was incorrectly saved.")
                    client.get("/api/v1/dashboard").raise_for_status()
                pdf = Path(directory) / "blank.pdf"
                writer = PdfWriter()
                writer.add_blank_page(595, 842)
                with io.BytesIO() as buffer:
                    writer.write(buffer)
                    pdf.write_bytes(buffer.getvalue())
                if not DocumentService.render_pages(pdf)[0].startswith(b"\x89PNG"):
                    raise AppError("pdf_renderer_unavailable", "The packaged PDF renderer did not produce a page image.")
                native = Path(claude_agent_sdk.__file__).parent / "_bundled" / ("claude.exe" if sys.platform == "win32" else "claude")
                sdk_version = command([str(native), "--version"], Path(directory), timeout=30).strip()
                if keyring.get_keyring().priority <= 0:
                    raise AppError("credential_backend_unavailable", "The operating system credential backend is unavailable.")
                return {
                    "passed": True, "version": __version__, "startup_seconds": startup_seconds,
                    "browser_assets": True, "bootstrap": True, "worker_approval": True,
                    "pdf_renderer": True, "credential_backend": True, "overview_monitor": True, "sdk_runtime": sdk_version,
                    "live_ai": False, "real_tex": False,
                }
            finally:
                app.state.stopping = True
                server.should_exit = True
                thread.join(timeout=15)
                listener.close()
                if thread.is_alive():
                    raise AppError("smoke_shutdown_timeout", "The local smoke-check service did not stop.")


def main() -> None:
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser(description="Launch the local AI Job Search interface.")
    parser.add_argument("--workspace", type=Path, help="Developer override for the writable workspace.")
    parser.add_argument("--port", type=int, default=0, help="Developer override; zero chooses an available loopback port.")
    parser.add_argument("--smoke-test", action="store_true", help="Check the packaged UI and handshake without opening a window.")
    parser.add_argument("--smoke-report", type=Path, help="Write local diagnostic evidence for --smoke-test.")
    args = parser.parse_args()
    if args.smoke_test:
        try:
            result = smoke_test(Config(origin="http://127.0.0.1:0"))
        except Exception as exc:
            import traceback
            result = {"passed": False, "error_code": exc.code if isinstance(exc, AppError) else type(exc).__name__,
                      "diagnostic": traceback.format_exc()}
        if args.smoke_report:
            args.smoke_report.parent.mkdir(parents=True, exist_ok=True)
            args.smoke_report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        if sys.stdout:
            print(json.dumps(result, indent=2))
        if not result["passed"]:
            raise SystemExit(1)
        return
    if args.smoke_report:
        parser.error("--smoke-report requires --smoke-test")
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("AI Job Search")
    root.geometry("430x250")
    root.resizable(False, False)
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background="#f4f6f2")
    style.configure("TLabel", background="#f4f6f2", foreground="#18392e", font=("Segoe UI", 10))
    style.configure("Title.TLabel", font=("Segoe UI", 19, "bold"))
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="AI Job Search", style="Title.TLabel").pack(anchor="w")
    status = tk.StringVar(value="Starting your private local workspace...")
    ttk.Label(frame, textvariable=status, wraplength=380).pack(anchor="w", pady=(12, 18))
    bridge = DialogBridge()
    shutdown = threading.Event()
    startup_done = threading.Event()
    messages: queue.Queue = queue.Queue()
    running: dict = {}

    def quit_app() -> None:
        if shutdown.is_set():
            return
        app = running.get("app")
        if app:
            with app.state.workspace.store.connect() as connection:
                count = connection.execute("SELECT COUNT(*) FROM runs WHERE status IN ('queued','running')").fetchone()[0]
            if count and not messagebox.askokcancel(
                "Stop active work?",
                "Work is still active. Quitting stops owned processes and preserves saved checkpoints. An interrupted AI call may still incur API charges.",
                parent=root,
            ):
                return
        shutdown.set()
        if app:
            app.state.stopping = True
        status.set("Saving checkpoints and stopping...")
        button_reopen.configure(state="disabled")
        button_quit.configure(state="disabled")

        def finish() -> None:
            startup_done.wait()
            server = running.get("server")
            if server:
                running["app"].state.stopping = True
                server.should_exit = True
                running["thread"].join(timeout=15)
                if running["thread"].is_alive():
                    messages.put(("shutdown_failed", "Shutdown has not finished. Keep this window open and try Quit again; saved files were retained."))
                    return
                running["listener"].close()
            messages.put(("stopped", None))
        threading.Thread(target=finish, daemon=False, name="local-shutdown").start()

    def reopen() -> None:
        app = running.get("app")
        if not app:
            return
        token = app.state.sessions.mint()
        if not webbrowser.open(running["config"].origin + "/#token=" + token):
            messagebox.showerror("Browser unavailable", "Your default browser could not be opened. Set a default browser in Windows Settings and try Reopen.", parent=root)

    button_reopen = ttk.Button(frame, text="Reopen interface", command=reopen, state="disabled")
    button_reopen.pack(fill="x", pady=3)
    button_quit = ttk.Button(frame, text="Quit", command=quit_app)
    button_quit.pack(fill="x", pady=3)
    root.protocol("WM_DELETE_WINDOW", quit_app)

    def handle_dialogs() -> None:
        while not messages.empty():
            kind, value = messages.get_nowait()
            if kind == "stopped":
                root.destroy()
                return
            if kind == "shutdown_failed":
                shutdown.clear()
                status.set(value)
                button_quit.configure(state="normal")
                messagebox.showerror("AI Job Search is still stopping", value, parent=root)
            elif kind == "quit":
                quit_app()
            elif kind == "ready" and not shutdown.is_set():
                status.set("Running only on this computer. Closing your browser does not stop work; use this window to reopen or quit.")
                button_reopen.configure(state="normal")
                reopen()
            elif kind == "error" and not shutdown.is_set():
                status.set("Startup failed. Your career files were not intentionally removed.")
                messagebox.showerror("AI Job Search could not start", value, parent=root)
        try:
            result = bridge.requests.get_nowait()
        except queue.Empty:
            pass
        else:
            result.put("" if shutdown.is_set() else filedialog.askdirectory(parent=root, title="Choose a local workspace folder", mustexist=False))
        root.after(100, handle_dialogs)

    def startup() -> None:
        try:
            config = Config(workspace=args.workspace or selected_workspace(), origin=f"http://127.0.0.1:{args.port}")
            app, server, thread, listener = start_server(config, bridge, lambda: messages.put(("quit", None)))
            running.update(app=app, server=server, thread=thread, listener=listener, config=config)
            messages.put(("ready", None))
        except Exception as exc:
            message = exc.message if isinstance(exc, AppError) else "The application could not start. Check that the installation and workspace are on a writable local disk."
            messages.put(("error", message))
        finally:
            startup_done.set()
    root.after(100, handle_dialogs)
    threading.Thread(target=startup, daemon=False, name="local-startup").start()
    root.mainloop()
