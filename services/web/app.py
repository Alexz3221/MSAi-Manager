from __future__ import annotations
import base64
import datetime as dt
import json
import logging
import os
import sys
import traceback
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from dotenv import load_dotenv
from msai_core.matching import (
    build_feed,
    load_customer_profiles,
    load_msa_profiles,
)
from services.john.john_agent.runtime import JohnRuntime
from services.web.rate_limit import JohnRateLimiter
from services.web import users, sessions
load_dotenv(Path(__file__).resolve().parents[2] / ".env")
users.init_db()
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
SERVICE_NAME = os.environ.get("K_SERVICE", "msai-manager")
ENVIRONMENT = os.environ.get("ENVIRONMENT", os.environ.get("APP_ENV", "prod"))
PROJECT_ID = (
    os.environ.get("GOOGLE_CLOUD_PROJECT")
    or os.environ.get("BQ_PROJECT_ID")
    or "sprinternship-bld-2026"
)
WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = WEB_DIR / "templates" / "index.html"
LOGIN_TEMPLATE_PATH = WEB_DIR / "templates" / "login.html"
STATIC_DIR = WEB_DIR / "static"
# Paths reachable WITHOUT a logged-in session.
#  - /login, /api/login, /api/register: the auth flow itself
#  - /health: liveness probe
#  - POST /: the Pub/Sub push webhook that ingests GCS files (called by Google,
#    not a browser -- must stay open or data ingestion breaks)
PUBLIC_GET = {"/login", "/health"}
PUBLIC_POST = {"/api/login", "/api/register", "/"}
RESERVED_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__) | {
    "message",
    "asctime",
}
class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "severity": record.levelname,
            "message": record.getMessage(),
            "timestamp": dt.datetime.fromtimestamp(
                record.created,
                tz=dt.UTC,
            ).isoformat(),
            "logger": record.name,
            "service": SERVICE_NAME,
            "environment": ENVIRONMENT,
        }
        for key, value in record.__dict__.items():
            if key not in RESERVED_LOG_RECORD_FIELDS and value is not None:
                payload[key] = value
        if record.exc_info:
            exception_type = record.exc_info[0]
            exception_value = record.exc_info[1]
            payload["exception_type"] = (
                exception_type.__name__ if exception_type else "Exception"
            )
            payload["exception_message"] = str(exception_value)
            payload["stack_trace"] = "".join(traceback.format_exception(*record.exc_info))
        return json.dumps(payload, default=str, separators=(",", ":"))
def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        handlers=[handler],
        force=True,
    )
configure_logging()
LOGGER = logging.getLogger(__name__)
JOHN_RUNTIME = JohnRuntime()
MAX_JOHN_MESSAGE_LENGTH = 4_000
CUSTOMER_DATA_BUCKET = os.environ.get("CUSTOMER_DATA_BUCKET", "dummy_client_bucket")
def cloud_trace_fields(trace_header: str | None) -> dict[str, str]:
    if not trace_header:
        return {"trace": "unavailable"}
    trace_id = trace_header.split("/", 1)[0].split(";", 1)[0].strip()
    if not trace_id:
        return {"trace": "unavailable"}
    fields = {"trace": trace_id}
    if PROJECT_ID:
        fields["logging.googleapis.com/trace"] = (
            f"projects/{PROJECT_ID}/traces/{trace_id}"
        )
    return fields
def bool_setting(name: str, default: bool) -> bool:
    value = os.environ.get(name, str(default)).strip().casefold()
    if value in {"1", "true", "yes", "y", "on"}:
        return True
    if value in {"0", "false", "no", "n", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")
def positive_int_setting(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value
JOHN_ENABLED = bool_setting("JOHN_ENABLED", True)
JOHN_RATE_LIMITER = JohnRateLimiter(
    per_client_limit=positive_int_setting("JOHN_RATE_LIMIT_PER_CLIENT", 25),
    per_client_window_seconds=positive_int_setting(
        "JOHN_RATE_LIMIT_CLIENT_WINDOW_SECONDS",
        300,
    ),
    global_limit=positive_int_setting("JOHN_RATE_LIMIT_GLOBAL", 300),
    global_window_seconds=positive_int_setting(
        "JOHN_RATE_LIMIT_GLOBAL_WINDOW_SECONDS",
        3_600,
    ),
)
def bool_param(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    return value.casefold() in {"1", "true", "yes", "y"}
def feed_item_payload(item) -> dict[str, object]:
    return {
        "msa_id": item.msa_id,
        "subject": item.subject,
        "date": item.date,
        "effective_date": item.effective_date,
        "requires_customer_action": item.requires_customer_action,
        "affected_services": item.affected_services,
        "impacted_companies": [
            {
                "company_id": impact.company_id,
                "company_name": impact.company_name,
                "contacts": impact.contacts,
                "matching_services": impact.matching_services,
            }
            for impact in item.impacted_companies
        ],
        "summary": item.summary,
        "actions": item.actions,
        "raw_msa_path": str(item.raw_msa_path),
    }
def feed_payload(query: dict[str, list[str]], force_company: str | None = None) -> dict[str, object]:
    # force_company: when set (a customer session), the feed is locked to this
    # company regardless of any 'company' query param the client sends.
    if force_company is not None:
        company = force_company
    else:
        company = query.get("company", [""])[0].strip() or None
    service = query.get("service", [""])[0].strip() or None
    effective_from = query.get("effective_from", [""])[0].strip() or None
    effective_to = query.get("effective_to", [""])[0].strip() or None
    requires_action = bool_param(query.get("requires_action", [""])[0])
    feed = build_feed(
        company_query=company,
        service_query=service,
        requires_action=requires_action,
        effective_from=effective_from,
        effective_to=effective_to,
    )
    return {
        "filters": {
            "company": company,
            "service": service,
            "effective_from": effective_from,
            "effective_to": effective_to,
            "requires_action": requires_action,
        },
        "count": len(feed),
        "items": [feed_item_payload(item) for item in feed],
    }
def companies_payload(role: str = "internal") -> dict[str, object]:
    # Customers may not enumerate other companies.
    if role != "internal":
        return {"companies": []}
    return {
        "companies": [
            {"id": profile.company_id, "name": profile.company_name}
            for profile in load_customer_profiles().values()
        ]
    }
def services_payload() -> dict[str, object]:
    services = set()
    for profile in load_customer_profiles().values():
        services.update(profile.services)
    for profile in load_msa_profiles().values():
        services.update(profile.affected_services)
    return {"services": sorted(services)}
def html_page() -> str:
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return template.replace("__JOHN_ENABLED__", json.dumps(JOHN_ENABLED))
def login_page() -> str:
    return LOGIN_TEMPLATE_PATH.read_text(encoding="utf-8")
class RequestHandler(BaseHTTPRequestHandler):
    def log_context(self, method: str) -> dict[str, object]:
        parsed_url = urlparse(self.path)
        return {
            "http_method": method,
            "path": parsed_url.path,
            "event": "request_error",
            **cloud_trace_fields(self.headers.get("X-Cloud-Trace-Context")),
        }
    def log_exception(self, method: str) -> None:
        LOGGER.exception(
            "Unhandled request error",
            extra=self.log_context(method),
        )
    # ---- auth helpers -------------------------------------------------------
    def session(self) -> dict | None:
        return sessions.session_from_cookie(self.headers.get("Cookie"))
    def read_json_body(self, max_bytes: int = 16_384) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > max_bytes:
            return None
        try:
            data = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None
    # ---- response helpers ---------------------------------------------------
    def send_json(
        self,
        status: int,
        payload: dict[str, object],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)
    def client_key(self) -> str:
        forwarded_for = self.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            forwarded_client = forwarded_for.split(",", 1)[0].strip()
            if forwarded_client:
                return forwarded_client
        return str(self.client_address[0])
    def send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()
    def serve_static(self, url_path: str) -> bool:
        """Serve a file from STATIC_DIR if url_path is under /static/. Returns True if handled."""
        if not url_path.startswith("/static/"):
            return False
        relative = url_path.removeprefix("/static/")
        candidate = (STATIC_DIR / relative).resolve()
        static_root = STATIC_DIR.resolve()
        if static_root != candidate and static_root not in candidate.parents:
            self.send_json(404, {"error": "Not found"})
            return True
        if not candidate.is_file():
            self.send_json(404, {"error": "Not found"})
            return True
        content_type, _ = mimetypes.guess_type(str(candidate))
        body = candidate.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True
    def do_GET(self) -> None:
        try:
            self.handle_get()
        except Exception:
            self.log_exception("GET")
            self.send_json(503, {"error": "Service unavailable"})
    def handle_get(self) -> None:
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)
        # Static assets are always allowed (login page needs its CSS/JS).
        if self.serve_static(parsed_url.path):
            return
        # Login page is public.
        if parsed_url.path == "/login":
            self.send_html(login_page())
            return
        if parsed_url.path == "/health":
            self.send_json(200, {"status": "ok"})
            return
        # Everything else requires a session.
        sess = self.session()
        if sess is None:
            self.redirect("/login")
            return
        role = sess.get("role", "customer")
        company_id = sess.get("company_id")
        if parsed_url.path == "/":
            self.send_html(html_page())
            return
        if parsed_url.path == "/api/companies":
            self.send_json(200, companies_payload(role))
            return
        if parsed_url.path == "/api/services":
            self.send_json(200, services_payload())
            return
        if parsed_url.path in {"/api/feed", "/api/company"}:
            # Customers are locked to their own company; internal may filter freely.
            force = company_id if role == "customer" else None
            if role != "customer" and parsed_url.path == "/api/company" and "company" not in query:
                name = query.get("name", [""])[0]
                if name:
                    query["company"] = [name]
            self.send_json(200, feed_payload(query, force_company=force))
            return
        self.send_json(404, {"error": "Not found"})
    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)
        # --- public auth routes (no session required) ------------------------
        if parsed_url.path == "/api/register":
            body = self.read_json_body()
            if body is None:
                self.send_json(400, {"error": "Invalid request body."})
                return
            user, err = users.create_user(
                str(body.get("email", "")), str(body.get("password", ""))
            )
            if err:
                self.send_json(400, {"error": err})
                return
            signed = sessions.create_session(user.email, user.role, user.company_id)
            name, value = sessions.session_cookie_header(signed)
            self.send_json(200, {"ok": True}, headers={name: value})
            return
        if parsed_url.path == "/api/login":
            body = self.read_json_body()
            if body is None:
                self.send_json(400, {"error": "Invalid request body."})
                return
            user = users.verify_user(
                str(body.get("email", "")), str(body.get("password", ""))
            )
            if user is None:
                self.send_json(401, {"error": "Invalid email or password."})
                return
            signed = sessions.create_session(user.email, user.role, user.company_id)
            name, value = sessions.session_cookie_header(signed)
            self.send_json(200, {"ok": True}, headers={name: value})
            return
        if parsed_url.path == "/api/logout":
            sessions.destroy(self.headers.get("Cookie"))
            name, value = sessions.clear_cookie_header()
            self.send_json(200, {"ok": True}, headers={name: value})
            return
        # --- John: requires a session ---------------------------------------
        if parsed_url.path == "/api/john":
            if not JOHN_ENABLED:
                self.send_json(503, {"error": "John is currently disabled."})
                return
            sess = self.session()
            if sess is None:
                self.send_json(401, {"error": "Not authenticated."})
                return
            try:
                content_length = int(self.headers.get("Content-Length", 0))
                if content_length <= 0 or content_length > 16_384:
                    raise ValueError("Request body must be between 1 and 16384 bytes.")
                payload = json.loads(self.rfile.read(content_length))
                if not isinstance(payload, dict):
                    raise ValueError("Request body must be a JSON object.")
                message = str(payload.get("message", "")).strip()
                user_id = str(payload.get("user_id", "web-user")).strip()
                session_id = str(payload.get("session_id", "")).strip() or None
                if not message:
                    raise ValueError("Message is required.")
                if len(message) > MAX_JOHN_MESSAGE_LENGTH:
                    raise ValueError(
                        f"Message must be {MAX_JOHN_MESSAGE_LENGTH} characters or fewer."
                    )
                if not user_id or len(user_id) > 128:
                    raise ValueError("User ID must be between 1 and 128 characters.")
                if session_id and len(session_id) > 128:
                    raise ValueError("Session ID must be 128 characters or fewer.")
                rate_limit = JOHN_RATE_LIMITER.check(self.client_key())
                if not rate_limit.allowed:
                    retry_after = str(rate_limit.retry_after_seconds)
                    LOGGER.warning(
                        "John rate limit reached",
                        extra={
                            **self.log_context("POST"),
                            "event": "john_rate_limited",
                            "rate_limit_reason": rate_limit.reason,
                            "retry_after_seconds": rate_limit.retry_after_seconds,
                        },
                    )
                    self.send_json(
                        429,
                        {
                            "error": "John is receiving too many requests. Try again shortly.",
                            "retry_after_seconds": rate_limit.retry_after_seconds,
                        },
                        headers={"Retry-After": retry_after},
                    )
                    return
                # Role/company from the verified session drive John's access
                # control. These are injected into John's session state, NOT
                # taken from the model or the client payload.
                self.send_json(
                    200,
                    JOHN_RUNTIME.chat(
                        message=message,
                        user_id=user_id,
                        session_id=session_id,
                        role=sess.get("role", "customer"),
                        company_id=sess.get("company_id"),
                        principal_email=sess.get("email"),
                    ),
                )
            except ValueError as exc:
                self.send_json(400, {"error": str(exc)})
            except TimeoutError:
                self.log_exception("POST")
                self.send_json(504, {"error": "John took too long to respond."})
            except Exception:
                self.log_exception("POST")
                self.send_json(503, {"error": "John is temporarily unavailable."})
            return
        # --- Pub/Sub push webhook: GCS file ingestion -----------------------
        # Called by Google Pub/Sub, NOT a browser. Must stay unauthenticated.
        if parsed_url.path != "/":
            self.send_json(404, {"error": "Not found"})
            return
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)
        try:
            envelope = json.loads(raw_body)
            message = envelope["message"]
            decoded_bytes = base64.b64decode(message["data"])
            file_info = json.loads(decoded_bytes)
            bucket_name = file_info["bucket"]
            blob_name = file_info["name"]
            if not blob_name.endswith(".txt"):
                self.send_response(204)
                self.end_headers()
                return
            if bucket_name == CUSTOMER_DATA_BUCKET:
                from scripts.asset_checker import (
                    read_gcs_file,
                    transform_txt_to_dict,
                    merge_via_staging,
                    DATASET_ID,
                    TABLE_ID,
                    STAGING_TABLE_ID,
                )
                raw_text = read_gcs_file(bucket_name, blob_name)
                record = transform_txt_to_dict(raw_text)
                merge_via_staging(DATASET_ID, TABLE_ID, STAGING_TABLE_ID, record)
                self.send_response(204)
                self.end_headers()
                return
            from scripts.msa_keyword_extractor import parse_msa_file, write_profile
            profile = parse_msa_file(bucket_name, blob_name)
            errors = write_profile(profile)
            if errors:
                LOGGER.error(
                    "BigQuery insert failed",
                    extra={
                        **self.log_context("POST"),
                        "event": "bigquery_insert_failed",
                        "error_count": len(errors),
                        "errors": errors,
                    },
                )
                self.send_json(500, {"error": "Failed to write MSA profile"})
                return
            self.send_response(204)
            self.end_headers()
        except Exception:
            self.log_exception("POST")
            self.send_json(500, {"error": "Failed to process Pub/Sub message"})
    def log_message(self, format: str, *args: object) -> None:
        return
def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)
    LOGGER.info(
        "MSAi Manager web app started",
        extra={"event": "server_started", "host": HOST, "port": PORT},
    )
    server.serve_forever()
if __name__ == "__main__":
    main()
