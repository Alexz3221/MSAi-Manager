"""The join. Run directly to see results and check the invariants."""
import sqlite3, pathlib, datetime, collections

DB = pathlib.Path(__file__).parent / "msa.db"

# One row per (notice, matched project). Grouping happens in Python -- see notes.
JOIN_SQL = """
WITH scope AS (
  SELECT project_id FROM project_access WHERE principal_email = :principal
),
my_assets AS (
  SELECT a.* FROM asset_inventory a
  JOIN scope s ON s.project_id = a.project_id
  WHERE julianday(:today) - julianday(a.last_seen) <= 30
)
SELECT n.msa_bug_id, n.subject, n.product, n.category,
       n.published_date, n.deadline, n.summary, n.doc_url,
       a.project_id, a.resource_value, a.req_30d
FROM notices n
JOIN msa_targets t ON t.msa_bug_id = n.msa_bug_id
JOIN my_assets a   ON a.resource_type = t.resource_type
                  AND ( (t.match_kind = 'exact'  AND a.resource_value = t.match_value)
                     OR (t.match_kind = 'prefix' AND a.resource_value LIKE t.match_value || '%') )
WHERE n.status = 'published'
  AND julianday(:today) - julianday(n.published_date) <= :lookback_days
  AND (:product IS NULL OR n.product = :product)
"""


def find_msas(principal, lookback_days=90, product=None, today=None):
    today = today or datetime.date.today()
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(JOIN_SQL, {
        "principal": principal,
        "lookback_days": lookback_days,
        "product": product,
        "today": today.isoformat(),
    }).fetchall()
    con.close()

    grouped = collections.OrderedDict()
    for r in rows:
        n = grouped.setdefault(r["msa_bug_id"], {
            "msa_bug_id": r["msa_bug_id"], "subject": r["subject"],
            "product": r["product"], "category": r["category"],
            "published_date": r["published_date"], "deadline": r["deadline"],
            "days_left": None, "summary": r["summary"], "doc_url": r["doc_url"],
            "matched": [],
        })
        if r["deadline"] and n["days_left"] is None:
            n["days_left"] = (datetime.date.fromisoformat(r["deadline"]) - today).days
        n["matched"].append({"project_id": r["project_id"],
                             "resource": r["resource_value"],
                             "req_30d": r["req_30d"]})

    out = list(grouped.values())
    for n in out:
        n["matched"].sort(key=lambda m: -m["req_30d"])
        n["matched"] = n["matched"][:25]
        n["n_projects"] = len({m["project_id"] for m in n["matched"]})
    # No deadline sorts last.
    out.sort(key=lambda n: (n["days_left"] is None, n["days_left"]))
    return {"notices": out, "count": len(out)}


if __name__ == "__main__":
    res = find_msas("usagi@example.com")
    for n in res["notices"]:
        left = f"{n['days_left']}d left" if n["days_left"] is not None else "no deadline"
        print(f"{n['msa_bug_id']}  {left:>12}  {n['n_projects']} proj  {n['subject'][:60]}")
        for m in n["matched"]:
            print(f"      {m['project_id']:<14} {m['resource']:<26} {m['req_30d']:>7}")

    ids = {n["msa_bug_id"] for n in res["notices"]}
    assert "b/999000111" not in ids, "draft notice leaked"
    assert "b/459884120" not in ids, "dialogflow matched but she runs none"
    assert "b/506341882" not in ids, "cloud armor has no targets, cannot match"
    projects = {m["project_id"] for n in res["notices"] for m in n["matched"]}
    assert "other-team" not in projects, "SCOPE LEAK"
    assert "msai-archive" not in projects, "stale asset matched"
    assert res["notices"][-1]["deadline"] is None, "no-deadline notice should sort last"
    print("\nall invariants hold")
