from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn

from .api import create_app
from .config import Config, data_home
from .errors import AppError


def selected_workspace() -> Path:
    path = data_home() / "workspace.json"
    if not path.exists():
        return data_home() / "workspace"
    try:
        settings = json.loads(path.read_text(encoding="utf-8"))
        workspace = Path(settings["workspace"])
        if not workspace.is_absolute():
            raise ValueError("The saved workspace path must be absolute.")
        return workspace
    except (ValueError, KeyError, TypeError) as exc:
        raise AppError("invalid_workspace_settings", "The saved workspace selection is damaged. Preserve your workspace and use --workspace to select it explicitly.") from exc


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


def start_server(config: Config):
    listener = available_socket(int(config.origin.rsplit(":", 1)[1]))
    config.origin = f"http://127.0.0.1:{listener.getsockname()[1]}"

    def shutdown() -> None:
        server.should_exit = True

    try:
        app = create_app(config, on_shutdown=shutdown)
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
                        raise AppError("frontend_missing", "Build the frontend before running this smoke check.")
                    startup_seconds = round(time.monotonic() - started, 3)
                    response = client.post("/api/v1/session/bootstrap", json={"token": app.state.sessions.mint()},
                                           headers={"Origin": config.origin})
                    response.raise_for_status()
                    if "csrf_token" not in response.json():
                        raise AppError("bootstrap_failed", "The local session handshake failed.")
                    client.headers.update({
                        "Origin": config.origin, "X-CSRF-Token": response.json()["csrf_token"],
                        "Idempotency-Key": "local-server-smoke",
                    })
                    response = client.get("/api/v1/profile")
                    response.raise_for_status()
                    profile = response.json()
                    response = client.post("/api/v1/profile/changes", json={
                        "profile": Profile(name="Synthetic Server Check").model_dump(mode="json"),
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
                            raise AppError("worker_failed", run["error"] or "The worker did not preserve its approval.")
                        time.sleep(0.1)
                    else:
                        raise AppError("worker_timeout", "The worker did not complete its local checkpoint.")
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
                    raise AppError("pdf_renderer_unavailable", "The PDF renderer did not produce a page image.")
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
    parser = argparse.ArgumentParser(description="Run the local AI Job Search backend without a desktop launcher.")
    parser.add_argument("--workspace", type=Path, help="Use this local workspace instead of the saved selection.")
    parser.add_argument("--port", type=int, default=8765, help="Loopback backend port (default: 8765; zero chooses an available port).")
    parser.add_argument("--frontend-port", type=int, help="Print the login link for a separately running Vite frontend, usually 5173.")
    parser.add_argument("--smoke-test", action="store_true", help="Check the built UI, server, worker, and local runtimes without paid AI.")
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
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.frontend_port is not None and not 1 <= args.frontend_port <= 65535:
        parser.error("--frontend-port must be between 1 and 65535")
    try:
        config = Config(workspace=args.workspace or selected_workspace(), origin=f"http://127.0.0.1:{args.port}")
        app, server, thread, listener = start_server(config)
    except AppError as exc:
        parser.exit(1, f"Backend startup failed [{exc.code}]: {exc.message}\n")
    except OSError as exc:
        parser.exit(1, f"Backend startup failed: {exc}. Check the port and workspace permissions.\n")

    def stop(signum=None, frame=None) -> None:
        app.state.stopping = True
        server.should_exit = True

    handlers = {}
    try:
        for name in ("SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                handlers[sig] = signal.signal(sig, stop)
        frontend = f"http://127.0.0.1:{args.frontend_port}" if args.frontend_port else config.origin
        print(f"Backend ready: {config.origin}\nWorkspace: {app.state.workspace.root}", flush=True)
        print(f"Open this one-use link within 90 seconds:\n{frontend}/#token={app.state.sessions.mint()}", flush=True)
        print("Keep this terminal open. Press Ctrl+C to stop the backend.\n"
              "If the login link expires, restart the backend for a fresh link.", flush=True)
        while thread.is_alive():
            thread.join(timeout=0.5)
        if not server.should_exit:
            parser.exit(1, "The backend stopped unexpectedly. Review the terminal diagnostics before restarting.\n")
    except KeyboardInterrupt:
        pass
    finally:
        stop()
        thread.join(timeout=15)
        listener.close()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)
        if thread.is_alive():
            parser.exit(1, "Backend shutdown has not finished. Saved checkpoints were retained.\n")
