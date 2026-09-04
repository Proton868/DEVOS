"""Create real workspace artifacts for CREATION intents (not chat-only dumps)."""
from __future__ import annotations

import html
import logging
import re
from typing import Optional

logger = logging.getLogger("devos.artifact_scaffold")


def _brand_from_goal(goal: str) -> str:
    g = goal or ""
    # "shoe business named Footwalk" / "called Footwalk"
    m = re.search(r"(?:named|called)\s+([A-Za-z0-9][\w\s\-]{1,40})", g, re.I)
    if m:
        return m.group(1).strip()[:48]
    m = re.search(r"for\s+([A-Za-z0-9][\w\s\-]{1,40})\s+(?:shoe|store|shop|business)", g, re.I)
    if m:
        return m.group(1).strip()[:48]
    return "Studio"


def _is_website_goal(goal: str) -> bool:
    t = (goal or "").lower()
    keys = (
        "website", "web site", "landing page", "one page", "1 page",
        "single page", "homepage", "web page", "site for",
    )
    return any(k in t for k in keys)


def render_static_site_html(brand: str, goal: str) -> str:
    b = html.escape(brand)
    tagline = "Crafted for every step."
    if "shoe" in (goal or "").lower():
        tagline = "Footwear that moves with you."
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{b}</title>
  <link rel="stylesheet" href="style.css" />
</head>
<body>
  <header class="nav">
    <div class="logo">{b}</div>
    <nav>
      <a href="#collection">Collection</a>
      <a href="#about">About</a>
      <a href="#contact">Contact</a>
    </nav>
  </header>
  <main>
    <section class="hero">
      <h1>{b}</h1>
      <p class="lead">{html.escape(tagline)}</p>
      <a class="btn" href="#collection">Shop the collection</a>
    </section>
    <section id="collection" class="grid">
      <article class="card"><h3>Essential</h3><p>Everyday comfort.</p><span class="price">$89</span></article>
      <article class="card"><h3>Classic</h3><p>Timeless design.</p><span class="price">$129</span></article>
      <article class="card"><h3>Signature</h3><p>Premium materials.</p><span class="price">$189</span></article>
    </section>
    <section id="about" class="about">
      <h2>About {b}</h2>
      <p>A focused one-page presence for {b}. Built inside DevOS as a real workspace artifact — not a chat paste.</p>
    </section>
    <section id="contact" class="contact">
      <h2>Contact</h2>
      <p>hello@{html.escape(re.sub(r'[^a-z0-9]+', '', brand.lower()) or 'studio')}.example</p>
    </section>
  </main>
  <footer><p>© 2026 {b}. All rights reserved.</p></footer>
  <script src="script.js"></script>
</body>
</html>
"""


STYLE_CSS = """:root {
  --bg: #0f1419;
  --fg: #f4f1ea;
  --muted: #a39e93;
  --accent: #e8a87c;
  --card: #1a222c;
  --border: #2a3441;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: Georgia, "Times New Roman", serif;
  background: var(--bg);
  color: var(--fg);
  line-height: 1.55;
}
.nav {
  display: flex; justify-content: space-between; align-items: center;
  padding: 1.25rem 1.5rem; border-bottom: 1px solid var(--border);
  position: sticky; top: 0; background: rgba(15,20,25,0.92); backdrop-filter: blur(8px);
}
.logo { font-weight: 700; letter-spacing: 0.04em; color: var(--accent); }
.nav a { color: var(--muted); margin-left: 1.25rem; text-decoration: none; font-size: 0.95rem; }
.nav a:hover { color: var(--fg); }
.hero { padding: 5rem 1.5rem 3rem; max-width: 720px; }
.hero h1 { font-size: clamp(2.4rem, 6vw, 3.6rem); line-height: 1.1; margin-bottom: 1rem; }
.lead { color: var(--muted); font-size: 1.15rem; margin-bottom: 1.75rem; }
.btn {
  display: inline-block; background: var(--accent); color: #1a120c;
  padding: 0.75rem 1.4rem; border-radius: 999px; text-decoration: none; font-weight: 700;
}
.grid {
  display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: 1rem; padding: 2rem 1.5rem 3rem;
}
.card {
  background: var(--card); border: 1px solid var(--border); border-radius: 14px; padding: 1.25rem;
}
.card h3 { margin-bottom: 0.4rem; }
.card p { color: var(--muted); font-size: 0.95rem; }
.price { display: block; margin-top: 0.75rem; color: var(--accent); font-weight: 700; }
.about, .contact { padding: 2rem 1.5rem; max-width: 640px; }
.about h2, .contact h2 { margin-bottom: 0.75rem; }
.about p, .contact p { color: var(--muted); }
footer { padding: 2rem 1.5rem; border-top: 1px solid var(--border); color: var(--muted); font-size: 0.9rem; }
@media (max-width: 640px) { .nav a { display: none; } }
"""

SCRIPT_JS = """document.querySelectorAll('a[href^="#"]').forEach((a) => {
  a.addEventListener('click', (e) => {
    const id = a.getAttribute('href');
    const el = id && document.querySelector(id);
    if (el) { e.preventDefault(); el.scrollIntoView({ behavior: 'smooth' }); }
  });
});
"""


async def scaffold_website_artifacts(
    *,
    user_id: str,
    project_id: str = "default",
    goal: str,
) -> dict:
    """Write a real static site into the user's project workspace.

    Returns paths written. Does not grant UCIP authority beyond FileService path rules.
    """
    if not _is_website_goal(goal):
        return {"ok": False, "reason": "not_website_goal", "files": []}
    brand = _brand_from_goal(goal)
    try:
        from execution.files import FileService
        fs = FileService(user_id, project_id or "default")
        files = {
            "index.html": render_static_site_html(brand, goal),
            "style.css": STYLE_CSS,
            "script.js": SCRIPT_JS,
            "README.md": f"# {brand}\n\nStatic one-page site scaffolded by DevOS from your request.\n\nOpen `index.html` in the IDE preview.\n",
        }
        written = []
        for path, content in files.items():
            maybe = fs.write(path, content)
            if hasattr(maybe, "__await__"):
                await maybe
            written.append(path)
        return {
            "ok": True,
            "brand": brand,
            "files": written,
            "entrypoint": "index.html",
            "message": (
                f"Created a one-page site for **{brand}** in your DevOS workspace "
                f"({', '.join(written)}). Open the IDE and preview `index.html` — "
                "this is a real project file, not a chat paste."
            ),
        }
    except Exception as e:
        logger.exception("scaffold_website_artifacts failed")
        return {"ok": False, "reason": str(e)[:300], "files": []}
