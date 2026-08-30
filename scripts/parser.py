# scripts/parser.py — William (O'Neil / CAN SLIM) Stock Report
#
# Converts ONE raw narrative report into the structured dict logger.py
# needs. Best-effort extraction, not a strict validator.
#
# Known limitations, stated plainly:
#   1. Your sample report shows TWO different stock-block styles even
#      within the same file: a structured multi-line style (ticker /
#      sector / base pattern & stage / action as separate bullets) and
#      a terse one-line style ("Ticker (SYM): prose sentence."). The
#      terse style does not contain sector or base-pattern data in the
#      text at all — this parser cannot invent what was never written.
#      Those fields come back blank for terse-style rows.
#   2. One sample story bundles MULTIPLE days' index recaps under a
#      single "===" block (a catch-up digest). This parser treats the
#      whole block as one entry and does not attempt to split it into
#      per-day entries — the index list may end up mixing two days'
#      figures together for that specific report shape.
#   3. If a story has no parseable date anywhere in its text, this
#      parser cannot recover one and falls back to "now" at parse time
#      — that story will sort by dispatch time, not market date.

import re
from datetime import datetime, timezone

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def _strip_refs(text):
    if not text:
        return ""
    t = text
    t = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'\1', t)
    t = re.sub(r'\[[\d,\.\s]+\]', '', t)
    t = re.sub(r'\[\s*\]', '', t)
    t = re.sub(r'\s{2,}', ' ', t).strip()
    if t and not t.endswith((".", "!", "?", '"')):
        t += "."
    return t


def _split_stories(raw_text):
    normalized = raw_text.replace("\r\n", "\n")
    parts = re.split(r'\n===\n|^===\n|\n===$', normalized)
    return [p.strip() for p in parts if p.strip()]


def _find_header_and_date(text):
    m = re.search(r"##\s*((\w+),\s*(\w+)\s+(\d{1,2}),\s*(\d{4})'s Report)", text)
    if m:
        header, _, month_name, day, year = m.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            return header, (int(year), month, int(day))

    m = re.search(r"(\w+),\s*(\w+)\s+(\d{1,2}),\s*(\d{4})", text)
    if m:
        weekday, month_name, day, year = m.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            header = f"{weekday}, {month_name} {day}, {year}'s Report"
            return header, (int(year), month, int(day))

    m = re.search(r"^(\w+)\s+(\d{1,2}),\s*\d{1,2}:\d{2}\s*[AP]M", text, re.MULTILINE)
    if m:
        month_name, day = m.groups()
        month = MONTHS.get(month_name.lower())
        if month:
            now = datetime.now(timezone.utc)
            year = now.year
            try:
                weekday = datetime(year, month, int(day)).strftime("%A")
            except ValueError:
                weekday = "Unknown"
            header = f"{weekday}, {month_name} {day}, {year}'s Report"
            return header, (year, month, int(day))

    return None, None


def _headline(text):
    body = text
    div = re.search(r'\n-{5,}\n', body)
    if div:
        body = body[:div.start()]
    lines = [l for l in body.split("\n") if l.strip()]
    for line in lines:
        stripped = line.strip()
        if re.match(r'^\w+\s+\d{1,2},\s*\d{1,2}:\d{2}\s*[AP]M$', stripped):
            continue  # date/time line
        if re.match(r"^O'?Neil Market Update:", stripped, re.IGNORECASE):
            continue  # agent name line
        if stripped.startswith("##"):
            # a headline-styled H2 (story 2's format) still counts as headline text
            candidate = re.sub(r'^##\s*[^\w]*', '', stripped).strip()
            if len(candidate) > 20:
                return _strip_refs(candidate)
            continue
        if len(stripped) > 20:
            return _strip_refs(stripped)
    return ""


def _market_status(text):
    m = re.search(r'"([^"]*(?:Uptrend|Correction|Downtrend)[^"]*)"', text)
    return m.group(1).strip() if m else ""


def _section(text, start_patterns, end_pattern=r'\n-{5,}\n|\Z'):
    for sp in start_patterns:
        m = re.search(sp, text)
        if m:
            rest = text[m.end():]
            end_m = re.search(end_pattern, rest)
            return rest[:end_m.start()] if end_m else rest
    return ""


def _subsection(text, start_pattern, end_patterns):
    """Like _section but the end boundary is any of several possible
    next-heading patterns (used to split the breakout screen into its
    Sector-Led / Divergent sub-sections, which are '## ' headings, not
    '-----' dividers)."""
    m = re.search(start_pattern, text)
    if not m:
        return ""
    rest = text[m.end():]
    end_positions = []
    for ep in end_patterns:
        em = re.search(ep, rest)
        if em:
            end_positions.append(em.start())
    end_positions.append(len(rest))
    return rest[:min(end_positions)]


def _parse_indexes(section_text):
    lines = [l.strip() for l in section_text.split('\n') if l.strip().startswith('*')]
    indexes = []
    current = None
    for line in lines:
        content = line.lstrip('*').strip()
        if not content:
            continue
        am = re.match(r"^O'?Neil (?:Action|Indicator):\s*(.*)", content, re.IGNORECASE)
        if am:
            if current is not None:
                current['action'] = (current['action'] + ' ' + _strip_refs(am.group(1))).strip()
            continue
        if current is not None:
            indexes.append(current)
            current = None
        if ':' not in content:
            continue
        name, rest = content.split(':', 1)
        name, rest = name.strip(), rest.strip()
        pct_m = re.search(r'([+-]\d+\.?\d*%)', rest)
        chg_m = re.search(r'\(([+-][\d,\.]+\s*pts)', rest)
        val_m = re.match(r'^\$?([\d,]+\.?\d*)', rest)
        current = {
            "name": name,
            "value": val_m.group(0) if val_m else "",
            "change": chg_m.group(1) if chg_m else "",
            "pct": pct_m.group(1) if pct_m else "",
            "distribution_days": None,
            "action": _strip_refs(rest) if not val_m else "",
        }
    if current is not None:
        indexes.append(current)

    for ix in indexes:
        dd_m = re.search(r'(\d+)\s*(?:new\s+)?Distribution Days?', ix.get('action', ''), re.IGNORECASE)
        if dd_m:
            ix['distribution_days'] = int(dd_m.group(1))
    return indexes


HEADER_RE = re.compile(
    r'^(.+?)\s*\(\[?([A-Za-z.]{1,8})\]?(?:\([^)]*\))?\)\s*(?::\s*(.*))?$'
)


def _iter_stock_blocks(section_text):
    """Split a breakout/watchlist section into per-stock blocks. A new
    block starts on any bullet line whose content matches HEADER_RE
    (i.e. it names a ticker in parens) — NOT on indentation, because
    the sample reports put 'Sector / Industry:' continuation lines at
    the SAME indentation as the ticker header line; only 'Base Pattern
    & Stage:' / 'Action:' / 'Technical Telemetry:' are actually
    indented. Keying off content shape handles both correctly and
    also skips empty spacer bullets ('* ' with nothing after it)."""
    blocks = []
    current = []
    for line in section_text.split('\n'):
        stripped = line.strip()
        if not stripped.startswith('*'):
            continue
        content = stripped.lstrip('*').strip()
        if not content:
            continue  # spacer bullet
        if HEADER_RE.match(content):
            if current:
                blocks.append(current)
            current = [content]
        elif current:
            current.append(content)
        # else: orphan continuation line with no header yet — drop it
    if current:
        blocks.append(current)
    return blocks


def _parse_stock_block(content_lines):
    """Extract whatever fields are present in one stock block. Missing
    fields come back as empty strings, never guessed."""
    header_line = content_lines[0]
    out = {
        "ticker": "", "company": "", "sector": "", "industry": "",
        "base_pattern": "", "base_stage": "", "price": "", "pct": "",
        "action": "", "telemetry": "", "pivot_price": "", "distance_to_pivot": "",
    }

    hm = HEADER_RE.match(header_line)
    if hm:
        company, ticker, desc = hm.groups()
        out["company"] = company.strip()
        out["ticker"] = ticker.strip()
        if desc:
            # Terse or hybrid variant: everything we'll get is on this one line.
            pct_m = re.search(r'([+-]\d+\.?\d*%)', desc)
            if pct_m:
                out["pct"] = pct_m.group(1)
            out["action"] = _strip_refs(desc)
    else:
        out["company"] = header_line

    for content in content_lines[1:]:
        m = re.match(r'^Sector\s*/\s*Industry:\s*(.*)$', content, re.IGNORECASE)
        if m:
            parts = [p.strip() for p in m.group(1).split('/', 1)]
            out["sector"] = parts[0] if parts else ""
            out["industry"] = parts[1] if len(parts) > 1 else ""
            continue
        m = re.match(r'^Base Pattern\s*&\s*Stage:\s*(.+?)\s*\((Stage[^)]*)\)\s*$', content, re.IGNORECASE)
        if m:
            out["base_pattern"], out["base_stage"] = m.group(1).strip(), m.group(2).strip()
            continue
        m = re.match(r'^Base Pattern:\s*(.*)$', content, re.IGNORECASE)
        if m:
            out["base_pattern"] = m.group(1).strip()
            continue
        m = re.match(r'^Base Stage:\s*(.*)$', content, re.IGNORECASE)
        if m:
            out["base_stage"] = m.group(1).strip()
            continue
        m = re.match(r'^Action:\s*(.*)$', content, re.IGNORECASE)
        if m:
            action_text = m.group(1).strip()
            price_m = re.match(r'^\$?([\d,]+\.?\d*)\s*\(([+-]\d+\.?\d*%)\)\.?\s*(.*)$', action_text)
            if price_m:
                out["price"] = f"${price_m.group(1)}"
                out["pct"] = price_m.group(2)
                out["action"] = _strip_refs(price_m.group(3))
            else:
                out["action"] = _strip_refs(action_text)
            continue
        m = re.match(r'^Technical Telemetry:\s*(.*)$', content, re.IGNORECASE)
        if m:
            telemetry_text = m.group(1).strip()
            out["telemetry"] = _strip_refs(telemetry_text)
            pivot_m = re.search(r'\$[\d,]+\.?\d*', telemetry_text)
            if pivot_m:
                out["pivot_price"] = pivot_m.group(0)
            dist_m = re.search(r'(\d+\.?\d*%)\s*(?:of|below|above)', telemetry_text, re.IGNORECASE)
            if dist_m:
                out["distance_to_pivot"] = dist_m.group(1)
            continue

    return out


def _parse_breakout_rows(section_text):
    rows = []
    for i, block in enumerate(_iter_stock_blocks(section_text), start=1):
        parsed = _parse_stock_block(block)
        rows.append({
            "rank": i,
            "ticker": parsed["ticker"],
            "company": parsed["company"],
            "sector": parsed["sector"],
            "industry": parsed["industry"],
            "base_pattern": parsed["base_pattern"],
            "base_stage": parsed["base_stage"],
            "price": parsed["price"],
            "pct": parsed["pct"],
            "action": parsed["action"],
        })
    return rows


def _parse_watchlist_rows(section_text):
    rows = []
    for i, block in enumerate(_iter_stock_blocks(section_text), start=1):
        parsed = _parse_stock_block(block)
        rows.append({
            "rank": i,
            "ticker": parsed["ticker"],
            "company": parsed["company"],
            "sector": parsed["sector"],
            "industry": parsed["industry"],
            "base_pattern": parsed["base_pattern"],
            "base_stage": parsed["base_stage"],
            "pivot_price": parsed["pivot_price"],
            "distance_to_pivot": parsed["distance_to_pivot"],
            "telemetry": parsed["telemetry"],
        })
    return rows


def parse_story(text):
    header, ymd = _find_header_and_date(text)
    if not header or not ymd:
        raise ValueError("could not find a date or 'Report' header anywhere in this story")

    year, month, day = ymd
    timestamp = f"{year:04d}-{month:02d}-{day:02d}T20:00:00+00:00"
    headline = _headline(text)
    market_status = _market_status(text)

    idx_section = _section(text, [
        r'##[^\n]*Major (?:Market )?Index(?:es|ices)?[^\n]*\n',
    ])
    indexes = _parse_indexes(idx_section)

    # The breakout screen section contains two '## ' sub-headings
    # (Sector-Led, Divergent) rather than being divider-separated, so
    # we grab the whole screen block first, then split by sub-heading.
    screen_section = _section(text, [
        r'##[^\n]*CAN SLIM Stock Breakout[^\n]*\n',
        r'##[^\n]*Breakout Screen[^\n]*\n',
    ])
    sector_led_text = _subsection(
        screen_section,
        r'##[^\n]*Sector-Led Breakouts[^\n]*\n',
        [r'##[^\n]*Divergent Breakouts[^\n]*\n', r'\n-{5,}\n']
    )
    divergent_text = _subsection(
        screen_section,
        r'##[^\n]*Divergent Breakouts[^\n]*\n',
        [r'\n-{5,}\n']
    )
    sector_led_breakouts = _parse_breakout_rows(sector_led_text)
    divergent_breakouts = _parse_breakout_rows(divergent_text)

    watchlist_section = _section(text, [
        r'##[^\n]*Pre-Breakout Watchlist[^\n]*\n',
    ])
    pre_breakout_watchlist = _parse_watchlist_rows(watchlist_section)

    return {
        "timestamp": timestamp,
        "header": header,
        "headline": headline,
        "market_status": market_status,
        "indexes": indexes,
        "sector_led_breakouts": sector_led_breakouts,
        "divergent_breakouts": divergent_breakouts,
        "pre_breakout_watchlist": pre_breakout_watchlist,
    }


def parse_stories(raw_text):
    events, errors = [], []
    for block in _split_stories(raw_text):
        try:
            events.append(parse_story(block))
        except Exception as e:
            snippet = block.strip().split("\n")[0][:80]
            errors.append((snippet, e))
    return events, errors
