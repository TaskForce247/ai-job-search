from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import threading
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

from fastapi import FastAPI, File, Form, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

from . import __version__
from .ai_debug import trace_detail, trace_index
from .applications import FINAL, ApplicationService
from .backups import MAX_ARCHIVE, BackupService
from .config import Config
from .errors import AppError
from .jobs import JobService
from .models import (
    AcceptedInput,
    AIConnection,
    AITraceDetail,
    AITraceSummary,
    ApplicationInput,
    ApplicationView,
    BackupInput,
    BootstrapInput,
    Decision,
    DocumentEdit,
    DocumentPdfInput,
    DocumentRunInput,
    DocumentView,
    EvaluationInput,
    JobChange,
    JobView,
    OutcomeInput,
    Page,
    PortalChange,
    PostingInput,
    ProfileChange,
    ProfileView,
    Queued,
    RankingInput,
    ResetInput,
    RunView,
    SearchInput,
    SettingsChange,
    SettingsView,
    SubmissionInput,
    WorkspaceInput,
)
from .portals import PortalService
from .profile import ProfileService
from .readiness import readiness
from .runtime import Credentials
from .security import Sessions
from .storage import digest, encoded, now
from .worker import WorkerManager
from .workflows import WorkflowService
from .workspace import Workspace

PREFIX = "/api/v1"
MUTATIONS = {"POST", "PUT", "PATCH", "DELETE"}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; font-src 'self' data: blob:; connect-src 'self'; worker-src 'self' blob:; "
        "object-src 'none'; frame-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cache-Control": "no-store",
}


def error_response(error: AppError, correlation_id: str | None = None) -> JSONResponse:
    response = JSONResponse(
        status_code=error.status,
        content={"error": {"code": error.code, "message": error.message, "detail": error.detail,
                           "correlation_id": correlation_id or str(uuid4())}},
    )
    response.headers.update(SECURITY_HEADERS)
    return response


def paginate(items: list[dict], limit: int, cursor: str | None, scope: object) -> dict:
    offset = 0
    scope_hash = digest(scope)[:16]
    if cursor:
        try:
            if len(cursor) > 200:
                raise ValueError
            value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
            offset = value["offset"]
            if not isinstance(offset, int) or offset < 0 or value["scope"] != scope_hash:
                raise ValueError
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AppError("invalid_cursor", "Reload this list to start a fresh page.") from exc
    end = offset + limit
    next_cursor = base64.urlsafe_b64encode(encoded({"offset": end, "scope": scope_hash}).encode()).decode().rstrip("=")
    return {"items": items[offset:end], "next_cursor": next_cursor if end < len(items) else None, "total": len(items)}


def create_app(config: Config | None = None, *, on_shutdown: Callable[[], None] | None = None) -> FastAPI:
    config = config or Config()
    workspace = Workspace(config.workspace, config.resources, create=not config.workspace.exists())
    sessions = Sessions(config.origin, config.session_seconds)
    mutation_locks: dict[str, asyncio.Lock] = {}
    mutation_gate = asyncio.Lock()
    gate = threading.Lock()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.instance_lock = open_instance_lock(app.state.workspace)
        try:
            if config.start_worker:
                app.state.worker = WorkerManager(app.state.workspace)
                app.state.worker.start()
            yield
        finally:
            if app.state.worker:
                await asyncio.to_thread(app.state.worker.stop)
            app.state.workspace.close()
            close_instance_lock(app.state.instance_lock)

    app = FastAPI(title="AI Job Search Local API", version=__version__, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=PREFIX + "/openapi.json")
    app.state.workspace = workspace
    app.state.sessions = sessions
    app.state.worker = None
    app.state.config = config
    app.state.busy = False
    app.state.stopping = False

    def ws() -> Workspace:
        return app.state.workspace

    def workflows() -> WorkflowService:
        return WorkflowService(ws())

    def operation(request: Request, suffix: str = "") -> str:
        return str(uuid5(NAMESPACE_URL, f"ai-job-search:{ws().root}:{request.method}:{request.url.path}:{request.headers.get('idempotency-key')}:{suffix}"))

    @app.exception_handler(AppError)
    async def handle_error(request: Request, exc: AppError):
        return error_response(exc, getattr(request.state, "correlation_id", None))

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError):
        issues = [{"field": list(error["loc"]), "message": error["msg"], "type": error["type"]} for error in exc.errors()]
        return error_response(AppError("validation_error", "Check the highlighted fields and try again.", 422, detail=issues))

    @app.exception_handler(HTTPException)
    async def handle_http(request: Request, exc: HTTPException):
        return error_response(AppError("not_found" if exc.status_code == 404 else "http_error",
                                       "This page or file was not found." if exc.status_code == 404 else "The request could not be completed.",
                                       exc.status_code))

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        request.state.correlation_id = str(uuid4())
        try:
            if request.headers.get("host") != sessions.host:
                raise AppError("unexpected_host", "This application accepts its own loopback address only.", 403)
            origin = request.headers.get("origin")
            if origin is not None and origin != sessions.origin:
                raise AppError("unexpected_origin", "Requests from other websites are not allowed.", 403)
            if request.headers.get("sec-fetch-site") == "cross-site":
                raise AppError("cross_site_request", "Requests from other websites are not allowed.", 403)
            private = request.url.path.startswith(PREFIX)
            bootstrap = request.url.path == PREFIX + "/session/bootstrap"
            mutation = request.method in MUTATIONS
            if private and not bootstrap:
                sessions.verify(request.cookies.get(sessions.cookie_name), request.headers.get("x-csrf-token"),
                                mutation=mutation)
            if private and mutation and origin != sessions.origin:
                raise AppError("origin_required", "Use the local application interface to make changes.", 403)
            if app.state.busy and request.url.path not in (PREFIX + "/system/shutdown", "/healthz"):
                raise AppError("workspace_recovering", "The workspace is switching or recovering. Retry shortly.", 503)
            cached_mutation = private and mutation and not bootstrap
            if cached_mutation:
                key = request.headers.get("idempotency-key", "")
                if not 8 <= len(key) <= 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:" for c in key):
                    raise AppError("idempotency_key_required", "This change needs a fresh operation key. Refresh the page.", 400)
                max_bytes = MAX_ARCHIVE + 1024 * 1024 if request.url.path.endswith("/restores") else 21 * 1024 * 1024
                content_length = request.headers.get("content-length")
                if content_length and (not content_length.isdigit() or int(content_length) > max_bytes):
                    raise AppError("upload_too_large", "The uploaded request exceeds the supported size limit.", 413)
                body = bytearray()
                async for chunk in request.stream():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise AppError("upload_too_large", "The uploaded request exceeds the supported size limit.", 413)
                request._body = bytes(body)
                request_hash = digest(request.method + request.url.path + digest(bytes(body)))
                cache_key = digest(request.method + request.url.path + key)
                lock = mutation_locks.setdefault(cache_key, asyncio.Lock())
                original_workspace = ws()
                async with lock, mutation_gate:
                    if original_workspace is not ws():
                        raise AppError("workspace_changed", "The workspace changed while this request was waiting. Reload before making changes.", 409)
                    with original_workspace.store.connect() as connection:
                        cached = connection.execute("SELECT * FROM idempotency WHERE key=?", (cache_key,)).fetchone()
                    if cached:
                        if cached["request_hash"] != request_hash:
                            raise AppError("idempotency_conflict", "This operation key already belongs to a different request.", 409)
                        response = JSONResponse(json.loads(cached["response_json"]), status_code=cached["status"])
                    else:
                        response = await call_next(request)
                        if response.status_code < 500 and "application/json" in response.headers.get("content-type", ""):
                            output = b"".join([chunk async for chunk in response.body_iterator])
                            data = json.loads(output)
                            with original_workspace.store.connect(write=True) as connection:
                                connection.execute("INSERT OR REPLACE INTO idempotency VALUES (?,?,?,?,?)",
                                                   (cache_key, request_hash, response.status_code, encoded(data), now()))
                            if original_workspace is not ws() and request.url.path == PREFIX + "/workspace":
                                with ws().store.connect(write=True) as connection:
                                    connection.execute("INSERT OR REPLACE INTO idempotency VALUES (?,?,?,?,?)",
                                                       (cache_key, request_hash, response.status_code, encoded(data), now()))
                            response = JSONResponse(data, status_code=response.status_code,
                                                    headers=dict(response.headers))
                if len(mutation_locks) > 2000:
                    mutation_locks.pop(next((item for item, value in mutation_locks.items() if not value.locked()), cache_key), None)
            else:
                response = await call_next(request)
            response.headers.update(SECURITY_HEADERS)
            response.headers["X-Correlation-ID"] = request.state.correlation_id
            return response
        except AppError as exc:
            return error_response(exc, request.state.correlation_id)

    @app.get("/healthz", include_in_schema=False)
    def health():
        return {"ready": True, "version": __version__}

    @app.post(PREFIX + "/session/bootstrap")
    def bootstrap(data: BootstrapInput):
        session_id, csrf = sessions.bootstrap(data.token)
        response = JSONResponse({"csrf_token": csrf})
        response.set_cookie(sessions.cookie_name, session_id, httponly=True, samesite="strict", secure=False,
                            path=PREFIX, max_age=config.session_seconds)
        return response

    @app.get(PREFIX + "/session")
    def session(request: Request):
        return {"csrf_token": sessions.verify(request.cookies.get(sessions.cookie_name))}

    @app.get(PREFIX + "/system/readiness")
    def get_readiness():
        return readiness(ws())

    @app.post(PREFIX + "/system/dependencies/{dependency}/repair", status_code=202, response_model=Queued)
    def repair_dependency(dependency: str, data: AcceptedInput, request: Request):
        if not data.accepted or dependency not in ("portals", "tex", "pdf"):
            raise AppError("consent_required", "Approve the named dependency installation first.", 409)
        run_id = workflows().enqueue(operation(request), "dependency_repair", {"dependency": dependency})
        return {"run_id": run_id}

    @app.post(PREFIX + "/system/shutdown")
    def shutdown():
        if on_shutdown is None:
            raise AppError("shutdown_unavailable", "Stop the backend with Ctrl+C in its terminal.", 409)
        app.state.stopping = True
        threading.Timer(0.2, on_shutdown).start()
        return {"accepted": True}

    def workspace_view(selected: Workspace) -> dict:
        return {"path": str(selected.root), "revision": digest(selected.snapshot([
            str(Path(".claude", "skills", "job-application-assistant", "01-candidate-profile.md")),
            "job_search_tracker.csv",
        ])), "initialized": bool(ProfileService(selected).read().data.name),
                "public_repository_warning": (selected.root / ".git").exists()}

    @app.get(PREFIX + "/workspace")
    def get_workspace():
        return workspace_view(ws())

    @app.post(PREFIX + "/workspace")
    async def change_workspace(data: WorkspaceInput):
        path = Path(data.path)
        with gate:
            if app.state.busy:
                raise AppError("workspace_busy", "A workspace operation is already running.", 409)
            app.state.busy = True
        previous_worker = app.state.worker
        previous_workspace = ws()
        selected = None
        new_lock = None
        new_worker = None
        previous_stopped = False
        switched = False
        try:
            if path.absolute() == ws().root:
                return get_workspace()
            if previous_worker:
                await asyncio.to_thread(previous_worker.stop)
                previous_stopped = True
            selected = await asyncio.to_thread(Workspace, path, config.resources, create=data.create)
            new_lock = open_instance_lock(selected)
            result = workspace_view(selected)
            new_worker = WorkerManager(selected) if config.start_worker else None
            if new_worker:
                new_worker.start()
            from .config import data_home
            settings_path = data_home() / "workspace.json"
            Workspace._durable_write(settings_path, encoded({"workspace": str(path)}).encode())
            old_lock = app.state.instance_lock
            app.state.instance_lock = new_lock
            app.state.workspace = selected
            app.state.worker = new_worker
            config.workspace = path
            switched = True
            previous_workspace.close()
            close_instance_lock(old_lock)
            return result
        except BaseException as exc:
            if not switched:
                if new_worker:
                    await asyncio.to_thread(new_worker.stop)
                if new_lock:
                    close_instance_lock(new_lock)
                if selected is not None:
                    selected.close()
                if previous_worker and previous_stopped:
                    previous_worker.start()
            if isinstance(exc, OSError):
                raise AppError("workspace_unavailable", "The workspace folder could not be opened. Check its full path, permissions, and available disk space.", 409) from exc
            raise
        finally:
            app.state.busy = False

    @app.post(PREFIX + "/workspace/imports", status_code=202, response_model=Queued)
    def import_workspace(data: dict[str, str], request: Request):
        if data:
            raise AppError("select_workspace_first", "Select the workspace first, then inspect its existing records.", 409)
        return {"run_id": workflows().enqueue(operation(request), "workspace_import", {})}

    @app.get(PREFIX + "/profile", response_model=ProfileView)
    def get_profile():
        return ProfileService(ws()).read()

    @app.post(PREFIX + "/profile/changes", status_code=202, response_model=Queued)
    def profile_change(data: ProfileChange, request: Request):
        current = ProfileService(ws()).read()
        if current.revision != data.expected_revision:
            raise AppError("revision_conflict", "The profile changed. Reload before saving.", 409)
        return {"run_id": workflows().enqueue(operation(request), "profile_change", data.model_dump(mode="json"))}

    @app.post(PREFIX + "/documents/imports", status_code=202, response_model=Queued)
    async def import_document(request: Request, file: UploadFile = File(...)):
        extension = Path(file.filename or "").suffix.lower()
        if extension not in (".pdf", ".md", ".txt"):
            raise AppError("unsupported_document", "Upload a text PDF, Markdown, or UTF-8 text file.")
        content = await file.read(20 * 1024 * 1024 + 1)
        await file.close()
        if not content or len(content) > 20 * 1024 * 1024:
            raise AppError("invalid_document_size", "Choose a nonempty document smaller than 20 MiB.")
        if extension == ".pdf" and not content.startswith(b"%PDF-"):
            raise AppError("invalid_pdf", "This file is not a PDF.")
        run_id = operation(request)
        relative = str(Path("documents", "cv", "import_" + run_id + extension))
        ws().commit(run_id + ":upload", {relative: content}, ws().snapshot([relative]))
        return {"run_id": workflows().enqueue(run_id, "extract_profile", {"source": relative, "source_hash": digest(content)})}

    @app.get(PREFIX + "/settings", response_model=SettingsView)
    def get_settings():
        settings, revision = ws().store.settings()
        return {"settings": settings, "revision": revision, "ai_connected": Credentials.connected()}

    @app.patch(PREFIX + "/settings", response_model=SettingsView)
    def set_settings(data: SettingsChange):
        ws().store.save_setting("preferences", data.settings.model_dump(mode="json"), data.expected_revision)
        return get_settings()

    @app.post(PREFIX + "/settings/ai-connection")
    def ai_connection(data: AIConnection):
        Credentials.set(data.api_key)
        return {"connected": True, "storage": "os_credential_store"}

    @app.delete(PREFIX + "/settings/ai-connection")
    def disconnect_ai():
        import keyring

        from .runtime import CREDENTIAL_SERVICE, CREDENTIAL_USER
        try:
            keyring.delete_password(CREDENTIAL_SERVICE, CREDENTIAL_USER)
        except keyring.errors.PasswordDeleteError:
            raise AppError("credential_not_found", "No API key is stored.", 404)
        return {"connected": False}

    @app.post(PREFIX + "/settings/ai-connection/test", status_code=202, response_model=Queued)
    def test_connection(request: Request):
        return {"run_id": workflows().enqueue(operation(request), "connection_test", {})}

    @app.get(PREFIX + "/portals")
    def get_portals():
        return {"items": PortalService(ws()).all()}

    @app.patch(PREFIX + "/portals/{portal_id}/settings")
    def portal_settings(portal_id: str, data: PortalChange, request: Request):
        return PortalService(ws()).configure(operation(request), portal_id, data)

    @app.get(PREFIX + "/jobs", response_model=Page[JobView])
    def get_jobs(q: str = Query("", max_length=500), status: str = "", saved: bool | None = None,
                 limit: int = Query(50, ge=1, le=100), cursor: str | None = None):
        service = JobService(ws())
        _, records = service.state()
        items = [service.summary(key, record) for key, record in records.items()]
        if q:
            items = [item for item in items if q.casefold() in (item["title"] + " " + item["company"] + " " + item["location"]).casefold()]
        if status:
            items = [item for item in items if item["status"] == status]
        if saved is not None:
            items = [item for item in items if item["saved"] == saved]
        items.sort(key=lambda item: (item.get("first_seen") or "", item["id"]), reverse=True)
        page = paginate(items, limit, cursor, [q, status, saved])
        page["items"] = [service.view(item["id"], records[item["id"]]) for item in page["items"]]
        return page

    @app.get(PREFIX + "/jobs/{job_id}", response_model=JobView)
    def get_job(job_id: str):
        return JobService(ws()).get(job_id)

    @app.patch(PREFIX + "/jobs/{job_id}", response_model=JobView)
    def update_job(job_id: str, data: JobChange, request: Request):
        return JobService(ws()).update(operation(request), job_id, {"saved": data.saved}, data.expected_revision)

    @app.post(PREFIX + "/jobs/imports", status_code=202, response_model=Queued)
    def import_posting(data: PostingInput, request: Request):
        return {"run_id": workflows().enqueue(operation(request), "posting_import", data.model_dump(mode="json"))}

    @app.post(PREFIX + "/search-runs", status_code=202, response_model=Queued)
    def search(data: SearchInput, request: Request):
        return {"run_id": workflows().enqueue(operation(request), "search", data.model_dump(mode="json"))}

    @app.post(PREFIX + "/ranking-runs", status_code=202, response_model=Queued)
    def rank(data: RankingInput, request: Request):
        return {"run_id": workflows().enqueue(operation(request), "rank", data.model_dump(mode="json"))}

    @app.post(PREFIX + "/evaluation-runs", status_code=202, response_model=Queued)
    def evaluate(data: EvaluationInput, request: Request):
        JobService(ws()).get(data.job_id)
        workflows().require_profile()
        return {"run_id": workflows().enqueue(operation(request), "evaluate", data.model_dump(mode="json"))}

    @app.get(PREFIX + "/applications", response_model=Page[ApplicationView])
    def get_applications(q: str = Query("", max_length=500), status: str = "",
                         limit: int = Query(50, ge=1, le=100), cursor: str | None = None):
        items = ApplicationService(ws()).overview()
        if q:
            items = [item for item in items if q.casefold() in (item["company"] + " " + item["role"]).casefold()]
        if status:
            items = [item for item in items if item["status"] == status]
        items.sort(key=lambda item: (item.get("created_at") or "", item["id"]), reverse=True)
        return paginate(items, limit, cursor, [q, status])

    @app.post(PREFIX + "/applications", response_model=ApplicationView)
    def create_application(data: ApplicationInput, request: Request):
        if data.evaluation_run_id:
            run = ws().store.run(data.evaluation_run_id)
            if run["kind"] != "evaluate" or run["status"] != "succeeded" or not run["result"].get("application_id"):
                raise AppError("approval_required", "Approve the fit assessment before creating its application.", 409)
            return ApplicationService(ws()).get(run["result"]["application_id"])
        return ApplicationService(ws()).create_external(operation(request), operation(request, "application"), data)

    @app.get(PREFIX + "/applications/{application_id}", response_model=ApplicationView)
    def get_application(application_id: str):
        return ApplicationService(ws()).get(application_id)

    @app.get(PREFIX + "/applications/{application_id}/documents/{kind}", response_model=DocumentView)
    def get_document(application_id: str, kind: str):
        application = ApplicationService(ws()).get(application_id)
        if kind not in application["documents"]:
            raise AppError("not_found", "This document has not been prepared yet.", 404)
        return application["documents"][kind]

    @app.patch(PREFIX + "/applications/{application_id}/documents/{kind}", response_model=DocumentView)
    def edit_document(application_id: str, kind: str, data: DocumentEdit, request: Request):
        return ApplicationService(ws()).save_content(operation(request), application_id, kind, data.content, data.expected_revision)

    @app.post(PREFIX + "/applications/{application_id}/document-runs", status_code=202, response_model=Queued)
    def document_run(application_id: str, data: DocumentRunInput, request: Request):
        with ws().lock():
            run_id = operation(request)
            with ws().store.connect() as connection:
                existing = connection.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone()
            inputs = {**data.model_dump(mode="json", exclude={"resume_from"}), "application_id": application_id}
            if existing:
                saved = ws().store.run(run_id)
                if (saved["kind"] != "documents" or any(saved["input"].get(key) != value for key, value in inputs.items())
                        or (data.resume_from and saved["input"].get("_retry_of") != data.resume_from)):
                    raise AppError("idempotency_conflict", "This operation key was already used for different document inputs.", 409)
                return {"run_id": run_id, "application_id": application_id}
            application = ApplicationService(ws()).get(application_id)
            if application["revision"] != data.expected_revision:
                raise AppError("revision_conflict", "The application changed. Reload before continuing its checks.", 409)
            if data.repair:
                inputs["repair_findings"] = [
                    *application.get("content_issues", []),
                    *[{"document": kind, **check} for kind, document in application["documents"].items()
                      for check in document.get("checks", []) if check["status"] != "pass"],
                ]
            with ws().store.connect() as connection:
                active = connection.execute(
                    "SELECT id FROM runs WHERE kind IN ('documents','document_pdf') AND status IN ('queued','running') "
                    "AND json_extract(input_json,'$.application_id')=? LIMIT 1", (application_id,),
                ).fetchone()
            if active:
                raise AppError("operation_in_progress", "Document checks are already in progress. Follow the current operation instead.", 409)
            source_id = data.resume_from or application["latest_run_id"]
            source = ws().store.run(source_id) if source_id else None
            pending = []
            bundle = None
            if source:
                if source["kind"] != "documents" or source["input"].get("application_id") != application_id:
                    raise AppError("invalid_document_resume", "Choose a document operation for this application.", 409)
                if source["status"] in ("queued", "running"):
                    raise AppError("operation_in_progress", "Document checks are already in progress. Follow the current operation instead.", 409)
                pending = [approval for approval in source["approvals"] if approval["status"] == "pending"]
                if any(approval["kind"] not in ("document_correction", "document_review") for approval in pending):
                    raise AppError("pending_document_decision", "Accept or reject the existing rewrite before starting another check.", 409)
                if (pending and not (data.instruction.strip() or data.repair)
                        and any(approval["proposal"]["expected_revision"] == application["revision"] for approval in pending)):
                    raise AppError("document_correction_required", "Accept the proposed corrections, or save your manual edits before resuming checks.", 409)
                bundle = workflows().bundle(source["id"])
                if bundle["hash"] != workflows().policy.bundle("apply")["hash"]:
                    raise AppError("policy_changed", "The workflow policy changed. Evaluate this role again before continuing.", 409)
                inputs["_retry_of"] = source["id"]
            queued = workflows().enqueue(run_id, "documents", inputs, pinned_policy=bundle)
            if pending:
                for approval in pending:
                    ws().store.record_decision(approval["id"], "reject", approval["revision"])
                ws().store.finish(source["id"], {
                    "application_id": application_id, "continued_as": queued,
                    "manual_edits_preserved": not bool(data.instruction.strip() or data.repair),
                    "ai_edits_requested": bool(data.instruction.strip() or data.repair),
                })
            return {"run_id": queued, "application_id": application_id}

    @app.post(PREFIX + "/applications/{application_id}/documents/{kind}/pdf-runs", status_code=202, response_model=Queued)
    def document_pdf_run(application_id: str, kind: Literal["cv", "cover_letter"], data: DocumentPdfInput, request: Request):
        with ws().lock():
            run_id = operation(request)
            with ws().store.connect() as connection:
                existing = connection.execute("SELECT id FROM runs WHERE id=?", (run_id,)).fetchone()
            if existing:
                saved = ws().store.run(run_id)
                if (saved["kind"] != "document_pdf" or any(saved["input"].get(key) != value for key, value in {
                    "application_id": application_id, "kind": kind, "expected_revision": data.expected_revision,
                }.items())):
                    raise AppError("idempotency_conflict", "This operation key was used for different PDF inputs.", 409)
                return {"run_id": run_id, "application_id": application_id}
            application = ApplicationService(ws()).get(application_id)
            if application["revision"] != data.expected_revision:
                raise AppError("revision_conflict", "The saved draft changed. Reload before generating its PDF.", 409)
            if kind not in application["documents"]:
                raise AppError("not_found", "Save this document before generating a PDF.", 404)
            with ws().store.connect() as connection:
                active = connection.execute(
                    "SELECT id FROM runs WHERE kind IN ('documents','document_pdf') AND status IN ('queued','running') "
                    "AND json_extract(input_json,'$.application_id')=? LIMIT 1", (application_id,),
                ).fetchone()
            if active:
                raise AppError("operation_in_progress", "Document work is already in progress. Wait for it to stop before compiling.", 409)
            workflows().enqueue(run_id, "document_pdf", {
                "application_id": application_id, "kind": kind, "expected_revision": data.expected_revision,
                "profile_revision": ProfileService(ws()).read().revision,
            })
            return {"run_id": run_id, "application_id": application_id}

    @app.post(PREFIX + "/applications/{application_id}/submissions", response_model=ApplicationView)
    def submit(application_id: str, data: SubmissionInput, request: Request):
        return ApplicationService(ws()).submit(operation(request), application_id, data)

    @app.post(PREFIX + "/applications/{application_id}/outcomes", response_model=ApplicationView)
    def outcome(application_id: str, data: OutcomeInput, request: Request):
        return ApplicationService(ws()).outcome(operation(request), application_id, data)

    @app.get(PREFIX + "/dashboard")
    def dashboard():
        applications = ApplicationService(ws()).overview()
        published = [item for item in applications if item["published"]]
        submitted = [item for item in published if item["status"] != "drafted"]
        interviews = [item for item in submitted if item["status"] == "interview"
                      or any(stage["status"] == "interview" for stage in item["timeline"])]
        offers = [item for item in submitted if item["status"] in ("offer", "hired", "offer_declined")
                  or any(stage["status"] in ("offer", "hired", "offer_declined") for stage in item["timeline"])]
        hired = [item for item in submitted if item["status"] == "hired"]
        deadlines = []
        for item in applications:
            value = item.get("deadline")
            if not value or item["status"] in FINAL:
                continue
            try:
                if date.fromisoformat(value) <= date.today() + timedelta(days=7):
                    deadlines.append(item)
            except ValueError:
                continue
        with ws().store.connect() as connection:
            pending = connection.execute("SELECT COUNT(*) FROM approvals WHERE status='pending'").fetchone()[0]
        return {
            "counts": {"drafted": sum(item["status"] == "drafted" for item in published), "submitted": len(submitted),
                       "active": sum(item["status"] not in FINAL for item in submitted), "interview": len(interviews),
                       "offer": len(offers), "hired": len(hired), "closed": sum(item["status"] in FINAL for item in submitted)},
            "funnel": [{"stage": name, "count": count} for name, count in (
                ("Submitted", len(submitted)), ("Interview", len(interviews)), ("Offer", len(offers)), ("Hired", len(hired)),
            )],
            "deadlines": sorted(deadlines, key=lambda item: item["deadline"])[:20],
            "recent_applications": sorted(applications, key=lambda item: item.get("created_at") or "", reverse=True)[:8],
            "pending_approvals": pending,
        }

    @app.get(PREFIX + "/runs", response_model=Page[RunView])
    def runs(limit: int = Query(50, ge=1, le=100), cursor: str | None = None):
        with ws().store.connect() as connection:
            ids = [row["id"] for row in connection.execute("SELECT id FROM runs ORDER BY created_at DESC,id DESC")]
        page = paginate([{"id": run_id} for run_id in ids], limit, cursor, "runs")
        page["items"] = [ws().store.run(row["id"]) for row in page["items"]]
        return page

    @app.get(PREFIX + "/runs/{run_id}", response_model=RunView)
    def run(run_id: str):
        return ws().store.run(run_id)

    @app.get(PREFIX + "/runs/{run_id}/ai-traces", response_model=list[AITraceSummary])
    def ai_traces(run_id: str):
        return trace_index(ws(), run_id)

    @app.get(PREFIX + "/runs/{run_id}/ai-traces/{trace_id}", response_model=AITraceDetail)
    def ai_trace(run_id: str, trace_id: str):
        return trace_detail(ws(), run_id, trace_id)

    @app.get(PREFIX + "/runs/{run_id}/events")
    def events(run_id: str, request: Request, after: int = Query(0, ge=0)):
        selected = ws()
        selected.store.run(run_id)
        header = request.headers.get("last-event-id", "")
        if header and not header.isdigit():
            raise AppError("invalid_event_id", "Reload activity to reconnect to its events.")
        start = max(after, int(header or 0))

        async def stream():
            sequence = start
            while not await request.is_disconnected():
                try:
                    sessions.verify(request.cookies.get(sessions.cookie_name))
                except AppError:
                    return
                if selected is not ws() or app.state.busy or app.state.stopping:
                    return
                with selected.store.connect() as connection:
                    rows = connection.execute(
                        "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT 100", (run_id, sequence),
                    ).fetchall()
                    state = connection.execute("SELECT status FROM runs WHERE id=?", (run_id,)).fetchone()
                for row in rows:
                    sequence = row["sequence"]
                    event = {"run_id": run_id, "sequence": sequence, "type": row["type"], "stage": row["stage"],
                             "occurred_at": row["occurred_at"], "payload": json.loads(row["payload"])}
                    yield f"id: {sequence}\ndata: {encoded(event)}\n\n"
                if state is None or (len(rows) < 100 and state["status"] in ("succeeded", "failed", "cancelled", "interrupted")):
                    return
                if not rows:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.post(PREFIX + "/runs/{run_id}/cancel", response_model=RunView)
    def cancel_run(run_id: str):
        run = ws().store.run(run_id)
        if run["status"] in ("succeeded", "failed", "cancelled", "interrupted"):
            raise AppError("run_not_active", "This operation has already stopped.", 409)
        with ws().store.connect(write=True) as connection:
            connection.execute("UPDATE runs SET cancel_requested=1 WHERE id=?", (run_id,))
            connection.execute("UPDATE approvals SET status='invalidated' WHERE run_id=? AND status='pending'", (run_id,))
        if run["status"] != "running":
            ws().store.finish(run_id, status="cancelled", error="Cancelled before further work.", code="cancelled")
        return ws().store.run(run_id)

    @app.post(PREFIX + "/runs/{run_id}/retry", status_code=202, response_model=Queued)
    def retry_run(run_id: str, request: Request):
        original = ws().store.run(run_id)
        if original["status"] not in ("failed", "cancelled", "interrupted"):
            raise AppError("run_not_retryable", "Only stopped operations can be retried.", 409)
        retry_id = operation(request)
        with ws().store.connect() as connection:
            existing = connection.execute("SELECT id FROM runs WHERE id=?", (retry_id,)).fetchone()
        if existing:
            return {"run_id": retry_id}
        inputs = {**original["input"], "_retry_of": run_id}
        if original["kind"] == "documents":
            # Saved drafts advance the revision; pin this retry now, not when its worker starts.
            application = ApplicationService(ws()).get(inputs["application_id"])
            inputs["expected_revision"] = application["revision"]
        return {"run_id": workflows().enqueue(retry_id, original["kind"], inputs,
                                               pinned_policy=workflows().bundle(run_id))}

    @app.post(PREFIX + "/approvals/{approval_id}/decision")
    async def decide(approval_id: str, data: Decision):
        approval = ws().store.approval(approval_id)
        destructive = approval["kind"] in ("reset", "restore") and data.decision not in ("reject", "not_now")
        manager = app.state.worker
        if destructive:
            with gate:
                if app.state.busy:
                    raise AppError("workspace_busy", "A workspace operation is already running.", 409)
                app.state.busy = True
            if manager:
                await asyncio.to_thread(manager.stop)
        try:
            return await asyncio.to_thread(workflows().decide, approval_id, data)
        finally:
            if destructive:
                if manager:
                    manager.start()
                app.state.busy = False

    @app.get(PREFIX + "/artifacts/{artifact_id}")
    def artifact(artifact_id: str):
        record = ws().store.artifact(artifact_id)
        content = ws().read(record["relative_path"])
        if content is None:
            raise AppError("artifact_missing", "The file is missing. Rebuild it or restore a backup.", 404)
        if digest(content) != record["hash"]:
            raise AppError("artifact_changed", "This file was edited outside the app. Rebuild it before downloading.", 409)
        return FileResponse(ws().path(record["relative_path"]), media_type=record["media_type"],
                            filename=record["name"], content_disposition_type="inline" if record["media_type"] == "application/pdf" else "attachment")

    @app.post(PREFIX + "/backups")
    def backup(data: BackupInput, request: Request):
        return {"artifact_id": BackupService(ws()).create(operation(request), data.passphrase)}

    @app.post(PREFIX + "/restores", status_code=202, response_model=Queued)
    async def restore_backup(request: Request, file: UploadFile = File(...), passphrase: str = Form("")):
        content = await file.read(MAX_ARCHIVE + 1)
        await file.close()
        proposal = await asyncio.to_thread(BackupService(ws()).inspect, content, passphrase)
        run_id = operation(request)
        relative = str(Path(".app", "runs", run_id, "restore-proposal.json"))
        ws()._durable_write(ws().path(relative), encoded(proposal).encode("utf-8"))
        return {"run_id": workflows().enqueue(run_id, "restore", {"proposal_path": relative})}

    @app.post(PREFIX + "/resets", status_code=202, response_model=Queued)
    def reset(data: ResetInput, request: Request):
        return {"run_id": workflows().enqueue(operation(request), "reset", data.model_dump())}

    if config.frontend.is_dir():
        mimetypes.add_type("text/javascript", ".js")
        mimetypes.add_type("text/javascript", ".mjs")
        mimetypes.add_type("application/wasm", ".wasm")
        assets = config.frontend / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def frontend(path: str):
            if path.startswith(("api/", ".")):
                raise AppError("not_found", "This page does not exist.", 404)
            candidate = (config.frontend / path).resolve()
            try:
                candidate.relative_to(config.frontend.resolve())
            except ValueError as exc:
                raise AppError("not_found", "This page does not exist.", 404) from exc
            if candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(config.frontend / "index.html")
    else:
        @app.get("/", include_in_schema=False)
        def missing_frontend():
            return error_response(AppError("frontend_missing", "Start the Vite frontend separately, or run npm run build in app/frontend. See app/README.md.", 503))

    return app


def open_instance_lock(workspace: Workspace):
    handle = workspace.path(str(Path(".app", "server.lock"))).open("a+b")
    handle.seek(0, 2)
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    try:
        handle.seek(0)
        if __import__("os").name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise AppError("workspace_already_open", "Another backend already owns this workspace. Use its terminal and browser session, or stop it before restarting.", 409) from exc
    return handle


def close_instance_lock(handle) -> None:
    handle.seek(0)
    if __import__("os").name == "nt":
        import msvcrt
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    handle.close()
