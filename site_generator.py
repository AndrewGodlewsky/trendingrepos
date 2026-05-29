"""Static site generator for the GitHub trending pipeline.

Reads data/repos.json and data/summaries.json, merges them, and writes:
  - docs/index.html   — fully self-contained dark-themed HTML page
  - docs/data.json    — JSON array for client-side JS filtering/search

Public API
----------
generate(cfg: dict) -> int
    Build the static site. Returns number of repo cards written.
"""
from __future__ import annotations

import html
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import storage

logger = logging.getLogger(__name__)

DOCS_DIR = Path("docs")
DATA_DIR = Path("data")

# Language -> colour mapping (GitHub linguist inspired)
LANG_COLOURS: Dict[str, str] = {
    "Python":     "#3572A5",
    "JavaScript": "#f1e05a",
    "TypeScript": "#2b7489",
    "Go":         "#00ADD8",
    "Rust":       "#dea584",
    "C++":        "#f34b7d",
    "C":          "#555555",
    "Java":       "#b07219",
    "Ruby":       "#701516",
    "PHP":        "#4F5D95",
    "Swift":      "#F05138",
    "Kotlin":     "#A97BFF",
    "Scala":      "#c22d40",
    "Shell":      "#89e051",
    "Dockerfile": "#384d54",
    "HTML":       "#e34c26",
    "CSS":        "#563d7c",
    "Vue":        "#41b883",
    "Svelte":     "#ff3e00",
    "Elixir":     "#6e4a7e",
    "Haskell":    "#5e5086",
    "Lua":        "#000080",
    "R":          "#198CE7",
    "Dart":       "#00B4AB",
    "Zig":        "#ec915c",
    "Nim":        "#ffc200",
    "Julia":      "#a270ba",
    "CUDA":       "#3A4E3A",
    "Nix":        "#7e7eff",
}
DEFAULT_LANG_COLOUR = "#8b949e"

# SVG icon paths (inline, no external dependency)
SVG_STAR = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="14" height="14" '
    'fill="currentColor" aria-hidden="true">'
    '<path d="M8 .25a.75.75 0 0 1 .673.418l1.882 3.815 4.21.612a.75.75 0 0 1 .416 '
    '1.279l-3.046 2.97.719 4.192a.751.751 0 0 1-1.088.791L8 11.996l-3.766 1.976a.75.75 '
    '0 0 1-1.088-.79l.72-4.194L.818 6.374a.75.75 0 0 1 .416-1.28l4.21-.611L7.327.668A.75'
    '.75 0 0 1 8 .25Z"/>'
    '</svg>'
)
SVG_FORK = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="14" height="14" '
    'fill="currentColor" aria-hidden="true">'
    '<path d="M5 5.372v.878c0 .414.336.75.75.75h4.5a.75.75 0 0 0 .75-.75v-.878a2.25 2.25 '
    '0 1 1 1.5 0v.878a2.25 2.25 0 0 1-2.25 2.25h-1.5v2.128a2.251 2.251 0 1 1-1.5 0V8.5h'
    '-1.5A2.25 2.25 0 0 1 3.5 6.25v-.878a2.25 2.25 0 1 1 1.5 0ZM5 3.25a.75.75 0 1 0-1.5'
    ' 0 .75.75 0 0 0 1.5 0Zm6.75.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Zm-3 8.75a.75.75'
    ' 0 1 0-1.5 0 .75.75 0 0 0 1.5 0Z"/>'
    '</svg>'
)
SVG_EYE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="14" height="14" '
    'fill="currentColor" aria-hidden="true">'
    '<path d="M8 2c1.981 0 3.671.992 4.933 2.078 1.27 1.091 2.187 2.345 2.637 3.023a1.62 '
    '1.62 0 0 1 0 1.798c-.45.678-1.367 1.932-2.637 3.023C11.67 13.008 9.981 14 8 14c-1.981'
    ' 0-3.671-.992-4.933-2.078C1.797 10.83.88 9.576.43 8.898a1.62 1.62 0 0 1 0-1.798c.45'
    '-.677 1.367-1.931 2.637-3.022C4.33 2.992 6.019 2 8 2ZM1.679 7.932a.12.12 0 0 0 0 '
    '.136c.411.622 1.241 1.75 2.366 2.717C5.176 11.758 6.527 12.5 8 12.5c1.473 0 2.824-'
    '.742 3.955-1.715 1.125-.967 1.955-2.095 2.366-2.717a.12.12 0 0 0 0-.136c-.411-.621'
    '-1.241-1.75-2.366-2.717C10.824 4.242 9.473 3.5 8 3.5c-1.473 0-2.824.742-3.955 1.715'
    '-1.125.967-1.955 2.096-2.366 2.717ZM8 10a2 2 0 1 1-.001-3.999A2 2 0 0 1 8 10Z"/>'
    '</svg>'
)
SVG_TREND = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="14" height="14" '
    'fill="currentColor" aria-hidden="true">'
    '<path d="M1.5 8a6.5 6.5 0 1 1 13 0 6.5 6.5 0 0 1-13 0ZM8 0a8 8 0 1 0 0 16A8 8 0 0 '
    '0 8 0ZM6.379 5.227A.25.25 0 0 1 6.75 5h4.5a.25.25 0 0 1 .25.25v4.5a.25.25 0 0 1-.'
    '427.177L9.893 8.647l-2.817 2.817a.75.75 0 0 1-1.06-1.06l2.817-2.817-1.28-1.28a.25'
    '.25 0 0 1 .073-.25l-.247-.83Z"/>'
    '</svg>'
)

SVG_GITHUB = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16" width="16" height="16" '
    'fill="currentColor" aria-hidden="true">'
    '<path d="M8 0c4.42 0 8 3.58 8 8a8.013 8.013 0 0 1-5.45 7.59c-.4.08-.55-.17-.55-.38 '
    '0-.27.01-1.13.01-2.2 0-.75-.25-1.23-.54-1.48 1.78-.2 3.65-.88 3.65-3.95 0-.88-.31-'
    '1.59-.82-2.15.08-.2.36-1.02-.08-2.12 0 0-.67-.22-2.2.82-.64-.18-1.32-.27-2-.27-.68'
    ' 0-1.36.09-2 .27-1.53-1.03-2.2-.82-2.2-.82-.44 1.1-.16 1.92-.08 2.12-.51.56-.82 '
    '1.28-.82 2.15 0 3.06 1.86 3.75 3.64 3.95-.23.2-.44.55-.51 1.07-.46.21-1.61.55-2.33'
    '-.66-.15-.24-.6-.83-1.23-.82-.67.01-.27.38.01.53.34.19.73.9.82 1.13.16.45.68 1.31 '
    '2.69.94 0 .67.01 1.3.01 1.49 0 .21-.15.45-.55.38A7.995 7.995 0 0 1 0 8c0-4.42 3.58'
    '-8 8-8Z"/>'
    '</svg>'
)


def _fmt_number(n: int) -> str:
    """Format a number as human-friendly string (e.g. 12345 -> 12.3k)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}k"
    return str(n)


def _lang_colour(lang: Optional[str]) -> str:
    if not lang:
        return DEFAULT_LANG_COLOUR
    return LANG_COLOURS.get(lang, DEFAULT_LANG_COLOUR)


def _stars_delta_badge(stars_7d: Optional[int]) -> str:
    """Return an HTML badge showing stars gained, or empty string."""
    if stars_7d is None:
        return ""
    sign = "+" if stars_7d >= 0 else ""
    cls = "delta-pos" if stars_7d >= 0 else "delta-neg"
    return f'<span class="delta {cls}">{sign}{_fmt_number(stars_7d)} this cycle</span>'


def _build_card(repo: dict) -> str:
    """Render a single repo as an HTML <article> card string."""
    name = html.escape(repo.get("name") or "")
    full_name = html.escape(repo.get("full_name") or "")
    description = html.escape(repo.get("description") or "")
    html_url = html.escape(repo.get("html_url") or "#")
    language = repo.get("language") or ""
    lang_escaped = html.escape(language)
    lang_colour = _lang_colour(language)
    stars = int(repo.get("stars") or 0)
    forks = int(repo.get("forks") or 0)
    watchers = int(repo.get("watchers") or 0)
    rank = int(repo.get("rank") or 0)
    trending_score = float(repo.get("trending_score") or 0.0)
    stars_7d = repo.get("stars_7d")
    summary_text = html.escape(repo.get("summary") or "")
    tags: List[str] = repo.get("tags") or []
    topics: List[str] = repo.get("topics") or []
    pushed_at = repo.get("pushed_at") or ""

    # Format pushed date for display
    pushed_display = ""
    if pushed_at:
        try:
            dt = datetime.fromisoformat(pushed_at.replace("Z", "+00:00"))
            pushed_display = dt.strftime("%-d %b %Y") if hasattr(dt, "strftime") else pushed_at[:10]
        except Exception:
            pushed_display = pushed_at[:10]

    # Collect all unique display tags (topics + AI tags merged)
    all_tags = list(dict.fromkeys(
        [t.lower() for t in tags] + [t.lower() for t in topics]
    ))[:8]  # cap at 8 tags

    tags_html = "".join(
        f'<span class="tag">{html.escape(t)}</span>' for t in all_tags
    )

    lang_badge = ""
    if language:
        lang_badge = (
            f'<span class="lang-badge" style="--lang-color:{lang_colour}" '
            f'data-lang="{lang_escaped}">'
            f'<span class="lang-dot" style="background:{lang_colour}"></span>'
            f'{lang_escaped}</span>'
        )

    delta_badge = _stars_delta_badge(stars_7d)

    # stars_7d as data attr (empty string when null so JS comparison works)
    stars_7d_attr = str(stars_7d) if stars_7d is not None else ""

    summary_section = ""
    if summary_text:
        summary_section = f'<p class="summary">{summary_text}</p>'

    return f"""<article class="repo-card" data-lang="{lang_escaped}" data-stars7d="{stars_7d_attr}" data-rank="{rank}" data-name="{full_name}" data-score="{trending_score:.2f}">
  <div class="card-header">
    <span class="rank-badge">#{rank}</span>
    <div class="card-title-row">
      <h2 class="repo-name"><a href="{html_url}" target="_blank" rel="noopener noreferrer">{full_name}</a></h2>
    </div>
    {f'<p class="description">{description}</p>' if description else ''}
  </div>
  <div class="card-body">
    {summary_section}
    <div class="tags-row">{tags_html}</div>
  </div>
  <div class="card-footer">
    <div class="stats">
      <span class="stat" title="Stars">{SVG_STAR} {_fmt_number(stars)}</span>
      <span class="stat" title="Forks">{SVG_FORK} {_fmt_number(forks)}</span>
      <span class="stat" title="Watchers">{SVG_EYE} {_fmt_number(watchers)}</span>
      {f'<span class="stat lang-stat">{lang_badge}</span>' if lang_badge else ''}
    </div>
    <div class="card-actions">
      {delta_badge}
      <a href="{html_url}" class="btn-github" target="_blank" rel="noopener noreferrer">{SVG_GITHUB} View on GitHub</a>
    </div>
  </div>
</article>"""


def _build_inline_css() -> str:
    return """
/* ===== CSS Custom Properties (Dark GitHub-inspired theme) ===== */
:root {
  --bg:         #0d1117;
  --surface:    #161b22;
  --surface-2:  #1c2230;
  --border:     #30363d;
  --text:       #c9d1d9;
  --text-muted: #8b949e;
  --accent:     #58a6ff;
  --accent-dim: #1f6feb33;
  --green:      #3fb950;
  --red:        #f85149;
  --yellow:     #d29922;
  --radius:     8px;
  --radius-sm:  4px;
  --shadow:     0 4px 16px rgba(0,0,0,0.4);
  --transition: 0.18s ease;
  font-size: 16px;
}

/* ===== Reset & Base ===== */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { scroll-behavior: smooth; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif;
  line-height: 1.6;
  min-height: 100vh;
  padding-bottom: 4rem;
}
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
svg { vertical-align: middle; flex-shrink: 0; }

/* ===== Header ===== */
.site-header {
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  z-index: 100;
  backdrop-filter: blur(8px);
}
.header-inner {
  max-width: 1400px;
  margin: 0 auto;
  padding: 0.85rem 1.5rem;
  display: flex;
  align-items: center;
  gap: 1.2rem;
  flex-wrap: wrap;
}
.site-logo {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 1.25rem;
  font-weight: 700;
  color: var(--text);
  white-space: nowrap;
}
.site-logo svg { color: var(--accent); }
.header-meta {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 1rem;
  font-size: 0.8rem;
  color: var(--text-muted);
  white-space: nowrap;
}
.repo-count-badge {
  background: var(--accent-dim);
  color: var(--accent);
  border: 1px solid var(--accent);
  border-radius: 20px;
  padding: 0.15rem 0.6rem;
  font-size: 0.75rem;
  font-weight: 600;
}

/* ===== Filters Bar ===== */
.filters-bar {
  background: var(--surface-2);
  border-bottom: 1px solid var(--border);
  padding: 0.75rem 0;
  position: sticky;
  top: 57px;
  z-index: 99;
}
.filters-inner {
  max-width: 1400px;
  margin: 0 auto;
  padding: 0 1.5rem;
  display: flex;
  align-items: center;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.search-input {
  flex: 1 1 260px;
  min-width: 180px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 0.875rem;
  padding: 0.4rem 0.75rem;
  transition: border-color var(--transition), box-shadow var(--transition);
  outline: none;
}
.search-input:focus {
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-dim);
}
.search-input::placeholder { color: var(--text-muted); }
.lang-select {
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text);
  font-size: 0.875rem;
  padding: 0.4rem 0.65rem;
  min-width: 140px;
  cursor: pointer;
  transition: border-color var(--transition);
  outline: none;
}
.lang-select:focus { border-color: var(--accent); }
.period-group {
  display: flex;
  gap: 0;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.period-group input[type=radio] { display: none; }
.period-group label {
  padding: 0.4rem 0.8rem;
  font-size: 0.8rem;
  font-weight: 500;
  cursor: pointer;
  background: var(--bg);
  color: var(--text-muted);
  border-right: 1px solid var(--border);
  transition: background var(--transition), color var(--transition);
  user-select: none;
}
.period-group label:last-of-type { border-right: none; }
.period-group input[type=radio]:checked + label {
  background: var(--accent);
  color: #fff;
}
.period-group label:hover { background: var(--surface); color: var(--text); }
.filter-count {
  font-size: 0.78rem;
  color: var(--text-muted);
  margin-left: auto;
  white-space: nowrap;
}

/* ===== Main Content ===== */
.main-content {
  max-width: 1400px;
  margin: 2rem auto;
  padding: 0 1.5rem;
}
.section-title {
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  margin-bottom: 1.25rem;
  padding-bottom: 0.5rem;
  border-bottom: 1px solid var(--border);
}

/* ===== Repo Grid ===== */
.repo-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 1.1rem;
}
@media (max-width: 1200px) { .repo-grid { grid-template-columns: repeat(3, 1fr); } }
@media (max-width: 860px)  { .repo-grid { grid-template-columns: repeat(2, 1fr); } }
@media (max-width: 540px)  { .repo-grid { grid-template-columns: 1fr; } }

/* ===== Repo Card ===== */
.repo-card {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  display: flex;
  flex-direction: column;
  gap: 0;
  transition: border-color var(--transition), box-shadow var(--transition), transform var(--transition);
  overflow: hidden;
}
.repo-card:hover {
  border-color: var(--accent);
  box-shadow: var(--shadow), 0 0 0 1px var(--accent);
  transform: translateY(-2px);
}
.card-header {
  padding: 1rem 1rem 0.6rem;
  border-bottom: 1px solid var(--border);
}
.card-title-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.5rem;
  margin-bottom: 0.4rem;
}
.rank-badge {
  display: inline-block;
  font-size: 0.7rem;
  font-weight: 700;
  color: var(--accent);
  background: var(--accent-dim);
  border-radius: 3px;
  padding: 0.05rem 0.35rem;
  margin-bottom: 0.35rem;
  letter-spacing: 0.03em;
}
.repo-name {
  font-size: 0.95rem;
  font-weight: 600;
  line-height: 1.3;
  word-break: break-word;
}
.repo-name a { color: var(--accent); }
.repo-name a:hover { text-decoration: underline; }
.description {
  font-size: 0.82rem;
  color: var(--text-muted);
  line-height: 1.45;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-body {
  padding: 0.7rem 1rem;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
.summary {
  font-size: 0.82rem;
  color: var(--text);
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 4;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.tags-row {
  display: flex;
  flex-wrap: wrap;
  gap: 0.3rem;
}
.tag {
  font-size: 0.7rem;
  color: var(--text-muted);
  background: rgba(139,148,158,0.1);
  border: 1px solid var(--border);
  border-radius: 20px;
  padding: 0.1rem 0.5rem;
}
.card-footer {
  padding: 0.65rem 1rem;
  border-top: 1px solid var(--border);
  background: rgba(0,0,0,0.12);
}
.stats {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.8rem;
  color: var(--text-muted);
  margin-bottom: 0.55rem;
}
.stat {
  display: flex;
  align-items: center;
  gap: 0.3rem;
}
.lang-badge {
  display: inline-flex;
  align-items: center;
  gap: 0.3rem;
  font-size: 0.78rem;
}
.lang-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  flex-shrink: 0;
}
.card-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.delta {
  font-size: 0.75rem;
  font-weight: 600;
  border-radius: 3px;
  padding: 0.1rem 0.4rem;
}
.delta-pos { color: var(--green); background: rgba(63,185,80,0.12); }
.delta-neg { color: var(--red);   background: rgba(248,81,73,0.12); }
.btn-github {
  display: inline-flex;
  align-items: center;
  gap: 0.35rem;
  font-size: 0.78rem;
  font-weight: 500;
  color: var(--text-muted);
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  padding: 0.3rem 0.65rem;
  transition: color var(--transition), border-color var(--transition), background var(--transition);
  text-decoration: none;
  cursor: pointer;
}
.btn-github:hover {
  color: var(--text);
  border-color: var(--text-muted);
  background: rgba(255,255,255,0.05);
  text-decoration: none;
}

/* ===== Empty / No-results state ===== */
.empty-state {
  grid-column: 1 / -1;
  text-align: center;
  padding: 4rem 2rem;
  color: var(--text-muted);
}
.empty-state h3 { font-size: 1.25rem; margin-bottom: 0.5rem; }

/* ===== Footer ===== */
.site-footer {
  margin-top: 3rem;
  border-top: 1px solid var(--border);
  padding: 1.5rem;
  text-align: center;
  font-size: 0.78rem;
  color: var(--text-muted);
}
.site-footer a { color: var(--text-muted); }
.site-footer a:hover { color: var(--accent); }

/* ===== Scrollbar ===== */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: var(--bg); }
::-webkit-scrollbar-thumb { background: var(--border); border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }
"""


def _build_inline_js() -> str:
    return """
(function () {
  'use strict';

  var REPOS = [];
  var searchTimer = null;
  var activeFilters = { lang: '', period: 'all', query: '' };

  function debounce(fn, ms) {
    return function() {
      var args = arguments;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(function() { fn.apply(null, args); }, ms);
    };
  }

  function stars7dNum(card) {
    var v = card.dataset.stars7d;
    if (v === '' || v === undefined || v === null) return null;
    return parseInt(v, 10);
  }

  function periodMatch(card, period) {
    if (period === 'all' || period === '30d') return true;
    var v = stars7dNum(card);
    if (v === null) return false;
    if (period === '7d') return v > 0;
    if (period === '24h') return v > 0;
    return true;
  }

  function filterAndRender() {
    var grid = document.getElementById('repo-grid');
    var cards = Array.prototype.slice.call(grid.querySelectorAll('.repo-card'));
    var lang   = activeFilters.lang;
    var period = activeFilters.period;
    var query  = activeFilters.query.toLowerCase().trim();

    var visible = 0;
    cards.forEach(function(card) {
      // Language filter
      if (lang && card.dataset.lang !== lang) {
        card.style.display = 'none'; return;
      }
      // Period filter
      if (!periodMatch(card, period)) {
        card.style.display = 'none'; return;
      }
      // Search filter
      if (query) {
        var searchable = [
          card.dataset.name || '',
          card.querySelector('.description') ? card.querySelector('.description').textContent : '',
          card.querySelector('.summary')     ? card.querySelector('.summary').textContent     : '',
          card.querySelectorAll('.tag').length ? Array.prototype.map.call(card.querySelectorAll('.tag'), function(t){ return t.textContent; }).join(' ') : ''
        ].join(' ').toLowerCase();
        if (searchable.indexOf(query) === -1) {
          card.style.display = 'none'; return;
        }
      }
      card.style.display = '';
      visible++;
    });

    // Show/hide empty state
    var existing = grid.querySelector('.empty-state');
    if (visible === 0) {
      if (!existing) {
        var el = document.createElement('div');
        el.className = 'empty-state';
        var h3 = document.createElement('h3');
        h3.textContent = 'No repositories found';
        var p = document.createElement('p');
        p.textContent = 'Try adjusting your filters or search query.';
        el.appendChild(h3);
        el.appendChild(p);
        grid.appendChild(el);
      }
    } else {
      if (existing) existing.remove();
    }

    // Update visible count
    var countEl = document.getElementById('filter-count');
    if (countEl) countEl.textContent = 'Showing ' + visible + ' of ' + cards.length + ' repos';
  }

  function init() {
    var searchEl = document.getElementById('search-input');
    var langEl   = document.getElementById('lang-select');
    var periods  = document.querySelectorAll('input[name="period"]');

    if (searchEl) {
      searchEl.addEventListener('input', debounce(function() {
        activeFilters.query = searchEl.value;
        filterAndRender();
      }, 200));
    }

    if (langEl) {
      langEl.addEventListener('change', function() {
        activeFilters.lang = langEl.value;
        filterAndRender();
      });
    }

    periods.forEach(function(radio) {
      radio.addEventListener('change', function() {
        if (radio.checked) {
          activeFilters.period = radio.value;
          filterAndRender();
        }
      });
    });

    // Initial count
    filterAndRender();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


def _collect_unique_languages(repos: List[dict]) -> List[str]:
    """Return sorted list of unique non-null language strings."""
    langs = sorted(set(
        r["language"] for r in repos
        if r.get("language")
    ))
    return langs


def _collect_top_tags(repos: List[dict], limit: int = 40) -> List[str]:
    """Return tags sorted by frequency, capped to limit."""
    counter: Counter = Counter()
    for r in repos:
        for t in (r.get("tags") or []):
            counter[t] += 1
    return [t for t, _ in counter.most_common(limit)]


def _enrich_repos(repos: List[dict], summaries: Dict[str, Any]) -> List[dict]:
    """Attach summary and tags from summaries dict to each repo dict (in-place copy)."""
    enriched = []
    for r in repos:
        d = dict(r)
        url = d.get("html_url", "")
        entry = summaries.get(url, {})
        summary_text = entry.get("summary", "") or ""
        # Strip error-prefixed summaries — show them as empty
        if summary_text.startswith("ERROR:"):
            summary_text = ""
        d["summary"] = summary_text
        d["tags"] = list(entry.get("tags") or [])
        enriched.append(d)
    return enriched


def _build_data_json_entry(r: dict) -> dict:
    """Build a client-data object with a subset of fields for data.json."""
    return {
        "rank":           int(r.get("rank") or 0),
        "full_name":      r.get("full_name") or "",
        "name":           r.get("name") or "",
        "description":    r.get("description") or "",
        "stars":          int(r.get("stars") or 0),
        "forks":          int(r.get("forks") or 0),
        "watchers":       int(r.get("watchers") or 0),
        "open_issues":    int(r.get("open_issues") or 0),
        "language":       r.get("language"),
        "topics":         list(r.get("topics") or []),
        "license":        r.get("license"),
        "html_url":       r.get("html_url") or "",
        "created_at":     r.get("created_at") or "",
        "pushed_at":      r.get("pushed_at") or "",
        "trending_score": round(float(r.get("trending_score") or 0.0), 2),
        "stars_7d":       r.get("stars_7d"),
        "summary":        r.get("summary") or "",
        "tags":           list(r.get("tags") or []),
    }


def _render_html(
    title: str,
    repos: List[dict],
    unique_langs: List[str],
    generated_at: str,
) -> str:
    """Render the full index.html as a string."""
    # Pre-render all cards
    cards_html = "\n".join(_build_card(r) for r in repos)

    # Language select options
    lang_options = '<option value="">All Languages</option>\n'
    lang_options += "\n".join(
        f'<option value="{html.escape(lang)}">{html.escape(lang)}</option>'
        for lang in unique_langs
    )

    repo_count = len(repos)
    css = _build_inline_css()
    js = _build_inline_js()

    # GitHub octocat logo inline
    logo_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="28" height="28" '
        'fill="currentColor">'
        '<path d="M12 0C5.374 0 0 5.373 0 12c0 5.302 3.438 9.8 8.207 11.387.599.111.793-'
        '.261.793-.577v-2.234c-3.338.726-4.033-1.416-4.033-1.416-.546-1.387-1.333-1.756-'
        '1.333-1.756-1.089-.745.083-.729.083-.729 1.205.084 1.839 1.237 1.839 1.237 1.07 '
        '1.834 2.807 1.304 3.492.997.107-.775.418-1.305.762-1.604-2.665-.305-5.467-1.334-'
        '5.467-5.931 0-1.311.469-2.381 1.236-3.221-.124-.303-.535-1.524.117-3.176 0 0 '
        '1.008-.322 3.301 1.23A11.509 11.509 0 0 1 12 5.803c1.02.005 2.047.138 3.006.404 '
        '2.291-1.552 3.297-1.23 3.297-1.23.653 1.653.242 2.874.118 3.176.77.84 1.235 1.911'
        ' 1.235 3.221 0 4.609-2.807 5.624-5.479 5.921.43.372.823 1.102.823 2.222v3.293c0 '
        '.319.192.694.801.576C20.566 21.797 24 17.3 24 12c0-6.627-5.373-12-12-12z"/>'
        '</svg>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="description" content="{html.escape(title)} — Daily trending GitHub repositories ranked by star velocity and engagement.">
  <title>{html.escape(title)}</title>
  <style>{css}</style>
</head>
<body>

<!-- ===== Site Header ===== -->
<header class="site-header">
  <div class="header-inner">
    <div class="site-logo">
      {logo_svg}
      <span>{html.escape(title)}</span>
    </div>
    <div class="header-meta">
      <span>Updated <time id="last-updated">{html.escape(generated_at)}</time> UTC</span>
      <span class="repo-count-badge" id="repo-count">{repo_count} repos</span>
    </div>
  </div>
</header>

<!-- ===== Filters Bar ===== -->
<nav class="filters-bar" role="navigation" aria-label="Repository filters">
  <div class="filters-inner">
    <input
      id="search-input"
      class="search-input"
      type="search"
      placeholder="Search repos, descriptions, tags..."
      aria-label="Search repositories"
      autocomplete="off"
      spellcheck="false"
    >
    <select id="lang-select" class="lang-select" aria-label="Filter by language">
      {lang_options}
    </select>
    <div class="period-group" role="group" aria-label="Time period filter">
      <input type="radio" name="period" id="period-24h" value="24h">
      <label for="period-24h">24h</label>
      <input type="radio" name="period" id="period-7d" value="7d">
      <label for="period-7d">7d</label>
      <input type="radio" name="period" id="period-30d" value="30d">
      <label for="period-30d">30d</label>
      <input type="radio" name="period" id="period-all" value="all" checked>
      <label for="period-all">All</label>
    </div>
    <span class="filter-count" id="filter-count">Showing {repo_count} of {repo_count} repos</span>
  </div>
</nav>

<!-- ===== Main Content ===== -->
<main class="main-content" id="main">
  <p class="section-title">Trending Repositories</p>
  <div class="repo-grid" id="repo-grid">
{cards_html}
  </div>
</main>

<!-- ===== Footer ===== -->
<footer class="site-footer">
  <p>
    Generated by the GitHub Trending Pipeline &mdash;
    <a href="https://github.com" target="_blank" rel="noopener noreferrer">GitHub</a>
    &bull; Last updated <time>{html.escape(generated_at)}</time> UTC
  </p>
</footer>

<script>{js}</script>
</body>
</html>"""


def generate(cfg: dict) -> int:
    """Build the static site from data files and write to docs/.

    Reads:
      data/repos.json      — ranked repo list
      data/summaries.json  — AI summaries keyed by html_url

    Writes:
      docs/index.html      — self-contained dark-themed HTML site
      docs/data.json       — JSON array for client-side filtering

    Args:
        cfg: Config dict from config.load_config(). Uses SITE_TITLE.

    Returns:
        Number of repo cards written.
    """
    title = cfg.get("SITE_TITLE", "GitHub Trending")
    docs_dir = DOCS_DIR
    data_dir = DATA_DIR

    docs_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Load data
    # ------------------------------------------------------------------
    repos: List[dict] = storage.load_repos(data_dir)
    summaries: Dict[str, Any] = storage.load_summaries(data_dir)

    logger.info(
        "site_generator: loaded %d repos, %d summaries",
        len(repos), len(summaries),
    )

    if not repos:
        logger.warning("No repos found in data/repos.json — generating empty site")

    # ------------------------------------------------------------------
    # Enrich repos with summary data
    # ------------------------------------------------------------------
    enriched = _enrich_repos(repos, summaries)

    # Sort by rank ascending (rank 1 = most trending first)
    enriched.sort(key=lambda r: int(r.get("rank") or 9999))

    # ------------------------------------------------------------------
    # Write docs/data.json
    # ------------------------------------------------------------------
    data_json = [_build_data_json_entry(r) for r in enriched]
    storage.write_json(docs_dir / "data.json", data_json)
    logger.info("Wrote docs/data.json (%d entries)", len(data_json))

    # ------------------------------------------------------------------
    # Collect filter metadata
    # ------------------------------------------------------------------
    unique_langs = _collect_unique_languages(enriched)
    logger.debug("Unique languages: %s", unique_langs)

    # ------------------------------------------------------------------
    # Render and write docs/index.html
    # ------------------------------------------------------------------
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    html_content = _render_html(
        title=title,
        repos=enriched,
        unique_langs=unique_langs,
        generated_at=now_utc,
    )
    index_path = docs_dir / "index.html"
    index_path.write_text(html_content, encoding="utf-8")
    logger.info(
        "Wrote docs/index.html (%d bytes, %d cards)",
        len(html_content), len(enriched),
    )

    return len(enriched)


# ---------------------------------------------------------------------------
# CLI entry point for manual testing
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from pathlib import Path

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    # Minimal stub config — SITE_TITLE only needed for generation
    mock_cfg = {
        "SITE_TITLE": "GitHub Trending",
    }

    count = generate(mock_cfg)
    print(f"Generated site with {count} repo cards.")
    print(f"  -> docs/index.html")
    print(f"  -> docs/data.json")
