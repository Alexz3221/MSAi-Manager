from __future__ import annotations

import base64
import json
import logging
import os
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

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
LOGGER = logging.getLogger(__name__)
JOHN_RUNTIME = JohnRuntime()
MAX_JOHN_MESSAGE_LENGTH = 4_000


def positive_int_setting(name: str, default: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a positive integer.") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be a positive integer.")
    return value


JOHN_RATE_LIMITER = JohnRateLimiter(
    per_client_limit=positive_int_setting("JOHN_RATE_LIMIT_PER_CLIENT", 5),
    per_client_window_seconds=positive_int_setting(
        "JOHN_RATE_LIMIT_CLIENT_WINDOW_SECONDS",
        300,
    ),
    global_limit=positive_int_setting("JOHN_RATE_LIMIT_GLOBAL", 30),
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


def feed_payload(query: dict[str, list[str]]) -> dict[str, object]:
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


def companies_payload() -> dict[str, object]:
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
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MSAi Manager</title>
  <style>
    :root {
      --ink: #17211f;
      --muted: #65716d;
      --line: #d7e1dc;
      --paper: #ffffff;
      --panel: rgba(255, 255, 255, 0.9);
      --field: #f7faf8;
      --accent: #0d7562;
      --accent-strong: #074d41;
      --warning: #9a521e;
      --bg: #eef5f1;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(135deg, rgba(13, 117, 98, 0.14), transparent 32%),
        linear-gradient(315deg, rgba(154, 82, 30, 0.12), transparent 35%),
        var(--bg);
      font-family: Georgia, "Times New Roman", serif;
      min-height: 100vh;
    }

    main {
      width: min(1200px, calc(100% - 32px));
      margin: 0 auto;
      padding: 34px 0;
    }

    .app-header {
      align-items: end;
      display: flex;
      gap: 28px;
      justify-content: space-between;
      margin-bottom: 22px;
    }

    .app-header h1 { margin-bottom: 5px; }

    .eyebrow {
      color: var(--accent);
      font: 700 11px/1 Verdana, sans-serif;
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }

    .tool-nav {
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid var(--line);
      border-radius: 9px;
      display: grid;
      gap: 5px;
      grid-template-columns: repeat(2, minmax(110px, 1fr));
      padding: 5px;
    }

    .tool-tab {
      background: transparent;
      color: var(--muted);
      margin: 0;
      min-height: 40px;
      width: auto;
    }

    .tool-tab:hover { background: #e5f2ed; }

    .tool-tab.active {
      background: var(--accent);
      color: white;
    }

    .tool-view[hidden] { display: none; }

    .layout {
      display: grid;
      grid-template-columns: 310px 1fr;
      gap: 18px;
      align-items: start;
    }

    .panel,
    .feed-card,
    .stat {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 42px rgba(23, 33, 31, 0.08);
    }

    .panel {
      padding: 20px;
      position: sticky;
      top: 20px;
    }

    h1 {
      font-size: 30px;
      line-height: 1.05;
      margin: 0 0 10px;
    }

    .subtitle {
      color: var(--muted);
      font-size: 15px;
      line-height: 1.45;
      margin: 0 0 20px;
    }

    label {
      color: var(--muted);
      display: block;
      font: 700 12px/1.2 Verdana, sans-serif;
      margin: 14px 0 7px;
      text-transform: uppercase;
    }

    select,
    input,
    textarea {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--field);
      color: var(--ink);
      font: 15px/1.2 Georgia, "Times New Roman", serif;
      min-height: 42px;
      padding: 10px;
    }

    .date-grid {
      display: grid;
      grid-template-columns: 1fr;
      gap: 10px;
    }

    .toggle {
      align-items: center;
      display: flex;
      gap: 9px;
      margin-top: 14px;
    }

    .toggle input {
      min-height: auto;
      width: auto;
    }

    button {
      width: 100%;
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      font: 700 14px/1 Verdana, sans-serif;
      margin-top: 18px;
      min-height: 44px;
      padding: 0 14px;
    }

    button:hover { background: var(--accent-strong); }

    .topline {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }

    .stat {
      padding: 14px;
    }

    .stat strong {
      display: block;
      font-size: 22px;
      line-height: 1;
    }

    .stat span {
      color: var(--muted);
      font: 700 11px/1 Verdana, sans-serif;
      text-transform: uppercase;
    }

    .feed {
      display: grid;
      gap: 14px;
    }

    .feed-card {
      padding: 22px;
    }

    .meta {
      color: var(--muted);
      font: 700 12px/1.3 Verdana, sans-serif;
      margin-bottom: 9px;
      text-transform: uppercase;
    }

    h2 {
      font-size: 22px;
      line-height: 1.2;
      margin: 0 0 12px;
    }

    p {
      line-height: 1.55;
      margin: 10px 0;
    }

    .pills {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 12px 0;
    }

    .pill {
      background: #e5f2ed;
      border: 1px solid #c8e0d7;
      border-radius: 999px;
      color: var(--accent-strong);
      font: 700 12px/1 Verdana, sans-serif;
      padding: 7px 10px;
    }

    .pill.warning {
      background: #fff1e7;
      border-color: #f0cfb4;
      color: var(--warning);
    }

    ul {
      margin: 10px 0 0;
      padding-left: 20px;
    }

    li {
      line-height: 1.4;
      margin: 7px 0;
    }

    .path {
      color: var(--muted);
      font: 12px/1.4 Consolas, monospace;
      overflow-wrap: anywhere;
    }

    .john-shell {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 42px rgba(23, 33, 31, 0.08);
      margin: 0 auto;
      max-width: 880px;
      overflow: hidden;
    }

    .john-heading {
      border-bottom: 1px solid var(--line);
      padding: 22px 24px 18px;
    }

    .john-heading h2 { margin-bottom: 6px; }
    .john-heading .subtitle { margin: 0; }

    .chat-log {
      display: flex;
      flex-direction: column;
      gap: 12px;
      min-height: 360px;
      padding: 24px;
    }

    .message {
      border: 1px solid var(--line);
      border-radius: 10px;
      line-height: 1.55;
      max-width: 82%;
      padding: 13px 15px;
    }

    .message.john {
      align-self: flex-start;
      background: var(--paper);
    }

    .message.user {
      align-self: flex-end;
      background: #dff0e9;
      border-color: #bddbce;
    }

    .message-label {
      color: var(--muted);
      display: block;
      font: 700 10px/1 Verdana, sans-serif;
      margin-bottom: 7px;
      text-transform: uppercase;
    }

    .tool-note {
      color: var(--accent);
      display: block;
      font: 700 10px/1.3 Verdana, sans-serif;
      margin-top: 9px;
    }

    .message code {
      background: #edf2ef;
      border-radius: 4px;
      font: 12px/1.4 Consolas, monospace;
      padding: 2px 5px;
    }

    .message-heading {
      display: inline-block;
      font-size: 16px;
      margin-top: 8px;
    }

    .suggestions {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      padding: 0 24px 18px;
    }

    .suggestion {
      background: #e5f2ed;
      border: 1px solid #c8e0d7;
      color: var(--accent-strong);
      margin: 0;
      min-height: 36px;
      width: auto;
    }

    .john-form {
      background: var(--field);
      border-top: 1px solid var(--line);
      display: grid;
      gap: 10px;
      grid-template-columns: 1fr auto;
      padding: 18px 24px 10px;
    }

    .john-form textarea {
      min-height: 76px;
      resize: vertical;
    }

    .john-form button {
      align-self: stretch;
      margin: 0;
      min-width: 110px;
      width: auto;
    }

    .john-status {
      background: var(--field);
      color: var(--muted);
      font: 11px/1.4 Verdana, sans-serif;
      margin: 0;
      min-height: 27px;
      padding: 0 24px 12px;
    }

    @media (max-width: 860px) {
      .app-header {
        align-items: stretch;
        flex-direction: column;
      }

      .layout,
      .topline {
        grid-template-columns: 1fr;
      }

      .panel {
        position: static;
      }

      .john-form { grid-template-columns: 1fr; }
      .john-form button { min-height: 44px; }
      .message { max-width: 94%; }
    }
  </style>
</head>
<body>
  <main>
    <header class="app-header">
      <div>
        <span class="eyebrow">Google Cloud MSA workspace</span>
        <h1>MSAi Manager</h1>
        <p class="subtitle">One place to review service notices and ask John what matters.</p>
      </div>
      <nav class="tool-nav" aria-label="Tools">
        <button class="tool-tab active" type="button" data-tool-target="feed-tool" aria-selected="true">Feed</button>
        <button class="tool-tab" type="button" data-tool-target="john-tool" aria-selected="false">John</button>
      </nav>
    </header>

    <section class="tool-view" id="feed-tool">
      <div class="layout">
      <aside class="panel">
        <h2>MSA Feed</h2>
        <p class="subtitle">Browse all MSA notices and filter by company, service, timing, and whether action is required.</p>
        <form id="filters">
          <label for="company">Company</label>
          <select id="company" name="company"></select>

          <label for="service">Service</label>
          <select id="service" name="service"></select>

          <div class="date-grid">
            <div>
              <label for="effective_from">From</label>
              <input id="effective_from" name="effective_from" type="date">
            </div>
            <div>
              <label for="effective_to">To</label>
              <input id="effective_to" name="effective_to" type="date">
            </div>
          </div>

          <label class="toggle">
            <input id="requires_action" name="requires_action" type="checkbox">
            Requires customer action
          </label>

          <button type="submit">Apply filters</button>
        </form>
      </aside>

      <section>
        <div class="topline">
          <div class="stat"><strong id="notice-count">0</strong><span>Matching MSAs</span></div>
          <div class="stat"><strong id="company-count">0</strong><span>Impacted companies</span></div>
          <div class="stat"><strong id="action-count">0</strong><span>Action required</span></div>
        </div>
        <div class="feed" id="feed"></div>
      </section>
      </div>
    </section>

    <section class="tool-view" id="john-tool" hidden>
      <div class="john-shell">
        <header class="john-heading">
          <span class="eyebrow">Conversational advisor</span>
          <h2>Ask John</h2>
          <p class="subtitle">John checks the scoped project and MSA demo data, then uses Gemini on Vertex AI to explain the result.</p>
        </header>
        <div class="chat-log" id="chat-log" aria-live="polite">
          <article class="message john">
            <span class="message-label">John</span>
            Hi. Ask me which MSA notices affect your projects, what deadline comes first, or about a specific Google Cloud product.
          </article>
        </div>
        <div class="suggestions">
          <button class="suggestion" type="button" data-prompt="Which MSA notices affect my projects?">What affects my projects?</button>
          <button class="suggestion" type="button" data-prompt="Which deadline comes first, and what should I do?">Show my next deadline</button>
        </div>
        <form class="john-form" id="john-form">
          <textarea id="john-message" maxlength="4000" placeholder="Ask John about notices, deadlines, migrations, or a Google Cloud product..." required></textarea>
          <button id="john-send" type="submit">Send</button>
        </form>
        <p class="john-status" id="john-status">Conversation history is temporary and can reset when Cloud Run restarts.</p>
      </div>
    </section>
  </main>

  <script>
    const filters = document.querySelector("#filters");
    const companySelect = document.querySelector("#company");
    const serviceSelect = document.querySelector("#service");
    const feed = document.querySelector("#feed");
    const noticeCount = document.querySelector("#notice-count");
    const companyCount = document.querySelector("#company-count");
    const actionCount = document.querySelector("#action-count");
    const toolTabs = document.querySelectorAll("[data-tool-target]");
    const johnForm = document.querySelector("#john-form");
    const johnMessage = document.querySelector("#john-message");
    const johnSend = document.querySelector("#john-send");
    const johnStatus = document.querySelector("#john-status");
    const chatLog = document.querySelector("#chat-log");
    const johnUserId = `web-${crypto.randomUUID ? crypto.randomUUID() : Date.now()}`;
    let johnSessionId = null;

    function escapeHtml(value) {
      return String(value ?? "").replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }

    function option(value, label) {
      return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
    }

    function selectTool(targetId) {
      document.querySelectorAll(".tool-view").forEach(view => {
        view.hidden = view.id !== targetId;
      });
      toolTabs.forEach(tab => {
        const active = tab.dataset.toolTarget === targetId;
        tab.classList.toggle("active", active);
        tab.setAttribute("aria-selected", String(active));
      });
      if (targetId === "john-tool") johnMessage.focus();
    }

    function appendMessage(role, text, tools = []) {
      const article = document.createElement("article");
      article.className = `message ${role}`;
      const toolNote = tools.length
        ? `<span class="tool-note">Used: ${escapeHtml(tools.join(", "))}</span>`
        : "";
      const formattedText = escapeHtml(text)
        .replace(/`([^`]+)`/g, "<code>$1</code>")
        .replace(/\\*\\*([^*]+)\\*\\*/g, "<strong>$1</strong>")
        .replace(/^### (.+)$/gm, '<strong class="message-heading">$1</strong>')
        .replace(/\\n/g, "<br>");
      article.innerHTML = `
        <span class="message-label">${role === "user" ? "You" : "John"}</span>
        ${formattedText}
        ${toolNote}
      `;
      chatLog.appendChild(article);
      article.scrollIntoView({ behavior: "smooth", block: "end" });
    }

    async function askJohn(message) {
      appendMessage("user", message);
      johnSend.disabled = true;
      johnMessage.disabled = true;
      johnStatus.textContent = "John is checking your project context...";

      try {
        const response = await fetch("/api/john", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            message,
            user_id: johnUserId,
            session_id: johnSessionId
          })
        });
        const payload = await response.json();
        if (!response.ok) {
          const retry = payload.retry_after_seconds
            ? ` Try again in ${payload.retry_after_seconds} seconds.`
            : "";
          throw new Error((payload.error || "John is unavailable.") + retry);
        }
        johnSessionId = payload.session_id;
        appendMessage("john", payload.reply, payload.tools || []);
        johnStatus.textContent = "John is ready for a follow-up question.";
      } catch (error) {
        appendMessage("john", error.message || "John is temporarily unavailable.");
        johnStatus.textContent = "The request failed. You can try again.";
      } finally {
        johnSend.disabled = false;
        johnMessage.disabled = false;
        johnMessage.focus();
      }
    }

    async function loadFilters() {
      const [companiesResponse, servicesResponse] = await Promise.all([
        fetch("/api/companies"),
        fetch("/api/services")
      ]);
      const companies = await companiesResponse.json();
      const services = await servicesResponse.json();

      companySelect.innerHTML = option("", "All companies") + companies.companies
        .map(company => option(company.id, company.name))
        .join("");
      serviceSelect.innerHTML = option("", "All services") + services.services
        .map(service => option(service, service))
        .join("");
    }

    function paramsFromForm() {
      const data = new FormData(filters);
      const params = new URLSearchParams();
      for (const [key, value] of data.entries()) {
        if (value) params.set(key, value);
      }
      if (document.querySelector("#requires_action").checked) {
        params.set("requires_action", "true");
      }
      return params;
    }

    function renderFeed(payload) {
      const impacted = new Set();
      let actionRequired = 0;

      payload.items.forEach(item => {
        if (item.requires_customer_action) actionRequired += 1;
        item.impacted_companies.forEach(company => impacted.add(company.company_id));
      });

      noticeCount.textContent = payload.count;
      companyCount.textContent = impacted.size;
      actionCount.textContent = actionRequired;

      if (!payload.items.length) {
        feed.innerHTML = `<article class="feed-card">No MSA notices match the selected filters.</article>`;
        return;
      }

      feed.innerHTML = payload.items.map(item => {
        const services = item.affected_services
          .map(service => `<span class="pill">${escapeHtml(service)}</span>`)
          .join("");
        const companies = item.impacted_companies
          .map(company => {
            const matched = company.matching_services.join(", ");
            return `<span class="pill warning">${escapeHtml(company.company_name)}: ${escapeHtml(matched)}</span>`;
          })
          .join("");
        const actions = item.actions
          .map(action => `<li>${escapeHtml(action)}</li>`)
          .join("");

        return `
          <article class="feed-card">
            <div class="meta">${escapeHtml(item.date)} | ${escapeHtml(item.msa_id)}</div>
            <h2>${escapeHtml(item.subject)}</h2>
            <div class="pills">${services}</div>
            <p><strong>Effective date:</strong> ${escapeHtml(item.effective_date || "Not listed")}</p>
            <p><strong>Customer action required:</strong> ${item.requires_customer_action ? "Yes" : "No"}</p>
            <p>${escapeHtml(item.summary)}</p>
            <div class="pills">${companies}</div>
            ${actions ? `<ul>${actions}</ul>` : ""}
            <p class="path">${escapeHtml(item.raw_msa_path)}</p>
          </article>
        `;
      }).join("");
    }

    async function loadFeed() {
      feed.innerHTML = `<article class="feed-card">Loading MSA feed...</article>`;
      const response = await fetch(`/api/feed?${paramsFromForm().toString()}`);
      renderFeed(await response.json());
    }

    filters.addEventListener("submit", event => {
      event.preventDefault();
      loadFeed();
    });

    toolTabs.forEach(tab => {
      tab.addEventListener("click", () => selectTool(tab.dataset.toolTarget));
    });

    johnForm.addEventListener("submit", event => {
      event.preventDefault();
      const message = johnMessage.value.trim();
      if (!message || johnSend.disabled) return;
      johnMessage.value = "";
      askJohn(message);
    });

    document.querySelectorAll("[data-prompt]").forEach(button => {
      button.addEventListener("click", () => {
        johnMessage.value = button.dataset.prompt;
        johnForm.requestSubmit();
      });
    });

    loadFilters().then(loadFeed);
  </script>
</body>
</html>"""


class RequestHandler(BaseHTTPRequestHandler):
    def log_exception(self, method: str) -> None:
        LOGGER.exception(
            "Unhandled %s request error path=%s trace=%s",
            method,
            self.path,
            self.headers.get("X-Cloud-Trace-Context", "unavailable"),
        )

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

    def do_GET(self) -> None:
        try:
            self.handle_get()
        except Exception:
            self.log_exception("GET")
            self.send_json(503, {"error": "Service unavailable"})

    def handle_get(self) -> None:
        parsed_url = urlparse(self.path)
        query = parse_qs(parsed_url.query)

        if parsed_url.path == "/":
            self.send_html(html_page())
            return

        if parsed_url.path == "/health":
            self.send_json(200, {"status": "ok"})
            return

        if parsed_url.path == "/api/companies":
            self.send_json(200, companies_payload())
            return

        if parsed_url.path == "/api/services":
            self.send_json(200, services_payload())
            return

        if parsed_url.path in {"/api/feed", "/api/company"}:
            if parsed_url.path == "/api/company" and "company" not in query:
                name = query.get("name", [""])[0]
                if name:
                    query["company"] = [name]
            self.send_json(200, feed_payload(query))
            return

        self.send_json(404, {"error": "Not found"})

    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)

        if parsed_url.path == "/api/john":
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
                        "John rate limit reached reason=%s retry_after=%s trace=%s",
                        rate_limit.reason,
                        retry_after,
                        self.headers.get("X-Cloud-Trace-Context", "unavailable"),
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

                self.send_json(
                    200,
                    JOHN_RUNTIME.chat(
                        message=message,
                        user_id=user_id,
                        session_id=session_id,
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

            from scripts.msa_keyword_extractor import parse_msa_file, write_profile

            profile = parse_msa_file(bucket_name, blob_name)
            errors = write_profile(profile)

            if errors:
                LOGGER.error(
                    "BigQuery insert failed path=%s trace=%s errors=%r",
                    self.path,
                    self.headers.get("X-Cloud-Trace-Context", "unavailable"),
                    errors,
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
    print(f"MSAi Manager web app listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
