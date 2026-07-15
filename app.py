from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from msa_chatbot import (
    build_feed,
    load_customer_profiles,
    load_msa_profiles,
)


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))


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
    input {
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
      grid-template-columns: 1fr 1fr;
      gap: 8px;
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

    @media (max-width: 860px) {
      .layout,
      .topline {
        grid-template-columns: 1fr;
      }

      .panel {
        position: static;
      }
    }
  </style>
</head>
<body>
  <main>
    <div class="layout">
      <aside class="panel">
        <h1>MSA Feed</h1>
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
  </main>

  <script>
    const filters = document.querySelector("#filters");
    const companySelect = document.querySelector("#company");
    const serviceSelect = document.querySelector("#service");
    const feed = document.querySelector("#feed");
    const noticeCount = document.querySelector("#notice-count");
    const companyCount = document.querySelector("#company-count");
    const actionCount = document.querySelector("#action-count");

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

    loadFilters().then(loadFeed);
  </script>
</body>
</html>"""


class RequestHandler(BaseHTTPRequestHandler):
    def send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
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

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), RequestHandler)
    print(f"MSAi Manager web app listening on http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
