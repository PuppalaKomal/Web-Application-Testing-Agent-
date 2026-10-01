"""
Browser-free exploration & analysis.

This is the Vercel-compatible counterpart to the full agent's Playwright
explorer. It cannot run JavaScript, so it cannot see console errors, XHR/
fetch traffic, or JS-rendered content - but it CAN, within a few seconds and
no external browser binary, fetch pages, parse the HTML, crawl same-origin
links, and run a useful set of static QA checks:

  - reachability / HTTP status / response time per page
  - <title> presence
  - forms + which inputs are required (with a crude "has a matching label?"
    accessibility check)
  - links with no visible text
  - images missing alt text
  - heading structure (missing or duplicated <h1>)
  - missing <html lang="">
  - broken same-origin links (404s found while crawling)

Kept deliberately small and bounded (max_pages / max_depth / per-request
timeout) so a full scan finishes well inside a serverless function's
execution time limit.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; QA-Lite-Agent/1.0; +https://vercel.com)"
}


def _same_origin(url: str, origin: str) -> bool:
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}" == origin


def _has_label(soup: BeautifulSoup, input_tag) -> bool:
    input_id = input_tag.get("id")
    if input_id and soup.find("label", attrs={"for": input_id}):
        return True
    if input_tag.get("aria-label"):
        return True
    if input_tag.find_parent("label"):
        return True
    return False


def scan_page(client: httpx.Client, url: str) -> dict:
    """Fetch and analyze a single page. Never raises - failures are recorded, not thrown."""
    try:
        resp = client.get(url, headers=DEFAULT_HEADERS, timeout=6.0, follow_redirects=True)
    except httpx.HTTPError as exc:
        return {"url": url, "reachable": False, "error": str(exc)}

    duration_ms = round(resp.elapsed.total_seconds() * 1000)
    if resp.status_code >= 400:
        return {
            "url": url, "reachable": False, "status": resp.status_code,
            "duration_ms": duration_ms, "error": f"HTTP {resp.status_code}",
        }

    content_type = resp.headers.get("content-type", "")
    if "text/html" not in content_type:
        return {"url": url, "reachable": True, "status": resp.status_code, "duration_ms": duration_ms, "skipped": "non-html"}

    soup = BeautifulSoup(resp.text, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else None
    html_tag = soup.find("html")
    has_lang = bool(html_tag and html_tag.get("lang"))

    forms = []
    for form in soup.find_all("form"):
        inputs = []
        for inp in form.find_all(["input", "select", "textarea"]):
            inputs.append({
                "name": inp.get("name"),
                "type": inp.get("type", inp.name),
                "required": inp.has_attr("required"),
                "has_label": _has_label(soup, inp),
            })
        forms.append({"action": form.get("action"), "method": (form.get("method") or "get").lower(), "inputs": inputs})

    links = []
    empty_text_links = 0
    for a in soup.find_all("a", href=True):
        href = urljoin(url, a["href"]).split("#")[0]
        text = a.get_text(strip=True)
        if not text and not a.find("img"):
            empty_text_links += 1
        links.append(href)

    images_without_alt = sum(1 for img in soup.find_all("img") if not img.get("alt"))

    h1_count = len(soup.find_all("h1"))
    heading_count = len(soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]))

    unlabeled_required_inputs = sum(
        1 for f in forms for inp in f["inputs"] if inp["required"] and not inp["has_label"] and inp["type"] not in ("hidden", "submit", "button")
    )

    return {
        "url": url,
        "reachable": True,
        "status": resp.status_code,
        "duration_ms": duration_ms,
        "title": title,
        "has_lang_attr": has_lang,
        "forms": forms,
        "link_count": len(links),
        "links": links,
        "empty_text_links": empty_text_links,
        "images_without_alt": images_without_alt,
        "h1_count": h1_count,
        "heading_count": heading_count,
        "unlabeled_required_inputs": unlabeled_required_inputs,
    }


def crawl(base_url: str, max_pages: int = 8, max_depth: int = 1, timeout_budget_s: float = 8.0) -> dict:
    """
    Breadth-first, same-origin crawl bounded by max_pages/max_depth AND a wall-clock
    budget (timeout_budget_s) - the budget is what actually protects a Vercel
    function from timing out, since a slow target site could otherwise blow past
    max_pages*timeout on its own.
    """
    import time
    origin = f"{urlparse(base_url).scheme}://{urlparse(base_url).netloc}"
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(base_url, 0)]
    pages: list[dict] = []
    start = time.monotonic()

    with httpx.Client() as client:
        while queue and len(pages) < max_pages:
            if time.monotonic() - start > timeout_budget_s:
                break
            url, depth = queue.pop(0)
            if url in visited or depth > max_depth:
                continue
            visited.add(url)

            result = scan_page(client, url)
            result["depth"] = depth
            pages.append(result)

            if result.get("reachable") and depth < max_depth:
                for link in result.get("links", []):
                    if _same_origin(link, origin) and link not in visited:
                        queue.append((link, depth + 1))

    return {"base_url": base_url, "pages": pages}


def analyze(site_map: dict) -> dict:
    """
    Heuristic, browser-free bug detection: no console/network evidence is
    available, so every check here is derived purely from the fetched HTML
    and HTTP response - still enough to catch real, common issues.
    """
    bugs = []
    checks = []
    counter = 0

    def next_id():
        nonlocal counter
        counter += 1
        return f"BUG-{counter:03d}"

    def check(title, page_type, priority, expected):
        checks.append({"id": f"CHK-{len(checks) + 1}", "title": title, "page": page_type, "type": page_type, "priority": priority, "expected": expected})

    for p in site_map["pages"]:
        url = p["url"]

        if not p.get("reachable"):
            bugs.append({
                "id": next_id(), "title": f"Page unreachable: {url}", "page": url,
                "severity": "critical", "likely_layer": "network/routing",
                "evidence": p.get("error", "unknown error"),
                "recommended_fix": "Verify the route exists and returns a successful status code.",
            })
            check(f"Reachability: {url}", "smoke", "high", "HTTP < 400")
            continue

        check(f"Smoke: {url} loads and has a title", "smoke", "high", "HTTP < 400 and non-empty <title>")
        if not p.get("title"):
            bugs.append({
                "id": next_id(), "title": f"Missing or empty <title>: {url}", "page": url,
                "severity": "medium", "likely_layer": "UI defect",
                "evidence": "No <title> text found in the page head",
                "recommended_fix": "Add a descriptive <title> for SEO and accessibility.",
            })

        if p.get("duration_ms", 0) > 3000:
            bugs.append({
                "id": next_id(), "title": f"Slow response ({p['duration_ms']}ms): {url}", "page": url,
                "severity": "low", "likely_layer": "performance",
                "evidence": f"duration_ms={p['duration_ms']}",
                "recommended_fix": "Investigate server-side rendering time or payload size for this route.",
            })

        check(f"Accessibility: {url} has lang attribute and heading structure", "accessibility", "medium", "html[lang] set, exactly one <h1>")
        if not p.get("has_lang_attr"):
            bugs.append({
                "id": next_id(), "title": f"Missing lang attribute on <html>: {url}", "page": url,
                "severity": "medium", "likely_layer": "accessibility",
                "evidence": "<html> tag has no lang attribute",
                "recommended_fix": "Add lang=\"en\" (or the correct locale) to the <html> tag.",
            })
        if p.get("h1_count", 0) == 0:
            bugs.append({
                "id": next_id(), "title": f"No <h1> found: {url}", "page": url,
                "severity": "low", "likely_layer": "accessibility",
                "evidence": "Zero <h1> elements on the page",
                "recommended_fix": "Add exactly one <h1> describing the page's main content.",
            })
        elif p.get("h1_count", 0) > 1:
            bugs.append({
                "id": next_id(), "title": f"Multiple <h1> elements ({p['h1_count']}): {url}", "page": url,
                "severity": "low", "likely_layer": "accessibility",
                "evidence": f"{p['h1_count']} <h1> elements found",
                "recommended_fix": "Use a single <h1> per page; demote the others to <h2>/<h3>.",
            })

        if p.get("images_without_alt"):
            bugs.append({
                "id": next_id(), "title": f"{p['images_without_alt']} image(s) missing alt text: {url}", "page": url,
                "severity": "medium", "likely_layer": "accessibility",
                "evidence": f"images_without_alt={p['images_without_alt']}",
                "recommended_fix": "Add descriptive alt text (or alt=\"\" for purely decorative images).",
            })

        if p.get("empty_text_links"):
            bugs.append({
                "id": next_id(), "title": f"{p['empty_text_links']} link(s) with no visible text: {url}", "page": url,
                "severity": "low", "likely_layer": "accessibility",
                "evidence": f"empty_text_links={p['empty_text_links']}",
                "recommended_fix": "Give every link discernible text or an aria-label.",
            })

        if p.get("unlabeled_required_inputs"):
            bugs.append({
                "id": next_id(), "title": f"{p['unlabeled_required_inputs']} required input(s) without a label: {url}", "page": url,
                "severity": "medium", "likely_layer": "accessibility",
                "evidence": f"unlabeled_required_inputs={p['unlabeled_required_inputs']}",
                "recommended_fix": "Associate a <label for=...> or aria-label with every required input.",
            })

        for i, form in enumerate(p.get("forms", [])):
            required = [inp for inp in form["inputs"] if inp["required"]]
            if required:
                check(f"Form validation: form #{i} on {url} has required fields", "negative/form-validation", "medium",
                      "Required fields exist; server/client should reject empty submissions")

    return {"bugs": bugs, "checks": checks}
