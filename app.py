from __future__ import annotations

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from msa_chatbot import (
    CUSTOMER_KEYWORDS_DIR,
    build_matches,
    display_name,
    find_company,
    load_keyword_files,
)


HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))


def company_payload(company_query: str) -> tuple[int, dict[str, object]]:
    companies = load_keyword_files(CUSTOMER_KEYWORDS_DIR)
    company_name = find_company(company_query, companies)

    if company_name is None:
        return 404, {
            "error": f"No cleaned customer profile found for '{company_query}'.",
            "available_companies": [
                {"id": company_id, "name": display_name(company_id)}
                for company_id in companies
            ],
        }

    matches = build_matches(company_name)
    return 200, {
        "company": {
            "id": company_name,
            "name": display_name(company_name),
            "services": sorted(companies[company_name]),
        },
        "matches": [
            {
                "msa_id": match.msa_id,
                "subject": match.subject,
                "date": match.date,
                "matching_services": match.matching_services,
                "summary": match.summary,
                "actions": match.actions,
                "raw_msa_path": str(match.raw_msa_path),
            }
            for match in matches
        ],
    }


def list_companies_payload() -> dict[str, object]:
    companies = load_keyword_files(CUSTOMER_KEYWORDS_DIR)
    return {
        "companies": [
            {"id": company_id, "name": display_name(company_id)}
            for company_id in companies
        ]
    }


def html_page() -> str:
    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>MSAi Manager</title>
  <style>
    :root {
      --ink: #18211f;
      --muted: #5b6864;
      --line: #d9e1dc;
      --field: #f7faf8;
      --paper: #ffffff;
      --accent: #0f7b63;
      --accent-strong: #084f42;
      --warn: #a75318;
      --bg: #edf4f0;
    }

    * {
      box-sizing: border-box;
    }

    body {
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(135deg, rgba(15, 123, 99, 0.13), transparent 34%),
        linear-gradient(315deg, rgba(167, 83, 24, 0.12), transparent 30%),
        var(--bg);
      font-family: Georgia, "Times New Roman", serif;
      min-height: 100vh;
    }

    main {
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 36px 0;
    }

    .workspace {
      display: grid;
      grid-template-columns: 320px 1fr;
      gap: 18px;
      align-items: start;
    }

    .panel,
    .result-card {
      background: rgba(255, 255, 255, 0.88);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 20px 48px rgba(24, 33, 31, 0.08);
    }

    .panel {
      padding: 20px;
      position: sticky;
      top: 20px;
    }

    h1 {
      margin: 0 0 10px;
      font-size: 30px;
      line-height: 1.05;
    }

    .subtitle {
      margin: 0 0 22px;
      color: var(--muted);
      font-size: 15px;
      line-height: 1.45;
    }

    label {
      display: block;
      color: var(--muted);
      font: 700 12px/1.2 Verdana, sans-serif;
      letter-spacing: 0;
      margin-bottom: 8px;
      text-transform: uppercase;
    }

    .lookup {
      display: flex;
      gap: 8px;
    }

    input {
      width: 100%;
      min-width: 0;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--field);
      color: var(--ink);
      font: 16px/1.2 Georgia, "Times New Roman", serif;
      padding: 12px;
    }

    button {
      border: 0;
      border-radius: 6px;
      background: var(--accent);
      color: white;
      cursor: pointer;
      font: 700 14px/1 Verdana, sans-serif;
      padding: 0 16px;
      min-height: 45px;
    }

    button:hover {
      background: var(--accent-strong);
    }

    .examples {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin-top: 14px;
    }

    .example {
      border: 1px solid var(--line);
      background: var(--paper);
      color: var(--ink);
      min-height: 34px;
      padding: 0 10px;
      font-size: 12px;
    }

    .results {
      display: grid;
      gap: 14px;
    }

    .empty {
      padding: 28px;
      color: var(--muted);
    }

    .result-card {
      padding: 22px;
    }

    .meta {
      color: var(--muted);
      font: 700 12px/1.3 Verdana, sans-serif;
      margin-bottom: 10px;
      text-transform: uppercase;
    }

    h2 {
      margin: 0 0 10px;
      font-size: 22px;
      line-height: 1.2;
    }

    .services {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 12px 0;
    }

    .pill {
      background: #e6f3ee;
      border: 1px solid #c9e2d8;
      border-radius: 999px;
      color: var(--accent-strong);
      font: 700 12px/1 Verdana, sans-serif;
      padding: 7px 10px;
    }

    p {
      color: var(--ink);
      line-height: 1.55;
      margin: 10px 0;
    }

    ul {
      margin: 10px 0 0;
      padding-left: 20px;
    }

    li {
      margin: 7px 0;
      line-height: 1.4;
    }

    .path {
      color: var(--muted);
      font: 12px/1.4 Consolas, monospace;
      overflow-wrap: anywhere;
    }

    @media (max-width: 820px) {
      .workspace {
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
    <div class="workspace">
      <section class="panel">
        <h1>MSAi Manager</h1>
        <p class="subtitle">Look up a company and see which Google Cloud MSA notices are relevant to the services it uses.</p>
        <form class="lookup" id="lookup-form">
          <div style="flex: 1;">
            <label for="company">Company</label>
            <input id="company" name="company" value="Apple" autocomplete="off">
          </div>
          <button type="submit" aria-label="Search">Search</button>
        </form>
        <div class="examples" id="examples"></div>
      </section>
      <section class="results" id="results">
        <div class="result-card empty">Search Apple or Oracle to preview relevant MSA notices.</div>
      </section>
    </div>
  </main>

  <script>
    const form = document.querySelector("#lookup-form");
    const input = document.querySelector("#company");
    const results = document.querySelector("#results");
    const examples = document.querySelector("#examples");

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, char => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }

    function renderError(message, companies = []) {
      const companyList = companies.map(company => `<li>${escapeHtml(company.name)}</li>`).join("");
      results.innerHTML = `
        <div class="result-card empty">
          <strong>${escapeHtml(message)}</strong>
          ${companyList ? `<ul>${companyList}</ul>` : ""}
        </div>
      `;
    }

    function renderPayload(payload) {
      if (!payload.matches.length) {
        results.innerHTML = `<div class="result-card empty">No relevant MSA notices found for ${escapeHtml(payload.company.name)}.</div>`;
        return;
      }

      const servicePills = payload.company.services
        .map(service => `<span class="pill">${escapeHtml(service)}</span>`)
        .join("");

      const cards = payload.matches.map(match => {
        const matched = match.matching_services
          .map(service => `<span class="pill">${escapeHtml(service)}</span>`)
          .join("");
        const actions = match.actions
          .map(action => `<li>${escapeHtml(action)}</li>`)
          .join("");

        return `
          <article class="result-card">
            <div class="meta">${escapeHtml(match.date)} · ${escapeHtml(match.msa_id)}</div>
            <h2>${escapeHtml(match.subject)}</h2>
            <div class="services">${matched}</div>
            <p>${escapeHtml(match.summary)}</p>
            ${actions ? `<ul>${actions}</ul>` : ""}
            <p class="path">${escapeHtml(match.raw_msa_path)}</p>
          </article>
        `;
      }).join("");

      results.innerHTML = `
        <article class="result-card">
          <div class="meta">Detected services for ${escapeHtml(payload.company.name)}</div>
          <div class="services">${servicePills}</div>
        </article>
        ${cards}
      `;
    }

    async function searchCompany(companyName) {
      results.innerHTML = `<div class="result-card empty">Checking relevant MSA notices...</div>`;
      const response = await fetch(`/api/company?name=${encodeURIComponent(companyName)}`);
      const payload = await response.json();
      if (!response.ok) {
        renderError(payload.error || "Company not found.", payload.available_companies || []);
        return;
      }
      renderPayload(payload);
    }

    async function loadExamples() {
      const response = await fetch("/api/companies");
      const payload = await response.json();
      examples.innerHTML = payload.companies.map(company => (
        `<button class="example" type="button" data-company="${escapeHtml(company.name)}">${escapeHtml(company.name)}</button>`
      )).join("");
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      searchCompany(input.value);
    });

    examples.addEventListener("click", event => {
      const button = event.target.closest("button[data-company]");
      if (!button) return;
      input.value = button.dataset.company;
      searchCompany(button.dataset.company);
    });

    loadExamples();
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

        if parsed_url.path == "/":
            self.send_html(html_page())
            return

        if parsed_url.path == "/health":
            self.send_json(200, {"status": "ok"})
            return

        if parsed_url.path == "/api/companies":
            self.send_json(200, list_companies_payload())
            return

        if parsed_url.path == "/api/company":
            query = parse_qs(parsed_url.query)
            company_query = query.get("name", [""])[0]
            if not company_query.strip():
                self.send_json(400, {"error": "Missing required query parameter: name"})
                return

            status, payload = company_payload(company_query)
            self.send_json(status, payload)
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
