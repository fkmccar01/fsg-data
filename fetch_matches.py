"""
FSG match fetcher.

Logs into Xpert Eleven, finds finished Goondesliga / Spoondesliga / Kzar's Kzup
games, downloads each match's viewer JSON, and archives it in this repo:

    matches/S44/R06/<gameId>.json
    index.json          <- list of every archived match, newest first

Runs from GitHub Actions. Settings come from repo secrets (see README.md).
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

X11 = "https://www.xperteleven.com"
LOGIN_URL = f"{X11}/front_new3.aspx"
PREGAME_URL = X11 + "/preGameViewer.aspx?Cup={cup}&gameID={game_id}&dh=2&sound=0"
API_URL = "https://game.xperteleven.com/api/game?watch={ticket}"

ROOT = Path(__file__).parent
MATCH_DIR = ROOT / "matches"
INDEX_FILE = ROOT / "index.json"

USERNAME = os.environ.get("X11_USERNAME", "")
PASSWORD = os.environ.get("X11_PASSWORD", "")
# Pages that list game links. Any page with gameDetails.aspx?GameID= links works.
SOURCE_PAGES = [u for u in (
    os.environ.get("GOONDESLIGA_URL", ""),
    os.environ.get("SPOONDESLIGA_URL", ""),
    os.environ.get("KZUP_URL", ""),          # optional: the Kzar's Kzup page
) if u]
# Optional: extra game IDs to grab (comma separated), e.g. for backfilling old matches
EXTRA_IDS = [g.strip() for g in os.environ.get("EXTRA_GAME_IDS", "").split(",") if g.strip()]
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "")

HEADERS = {"User-Agent": "Mozilla/5.0 (FSG match archiver)"}


def log(msg):
    print(msg, flush=True)


def notify(msg):
    """Push an alert to your phone via ntfy.sh (only if NTFY_TOPIC is set)."""
    log(msg)
    if NTFY_TOPIC:
        try:
            requests.post(f"https://ntfy.sh/{NTFY_TOPIC}", data=msg.encode("utf-8"),
                          headers={"Title": "FSG match fetcher"}, timeout=15)
        except Exception:
            pass


def hidden_fields(html):
    soup = BeautifulSoup(html, "html.parser")
    return {i["name"]: i.get("value", "")
            for i in soup.find_all("input", {"type": "hidden"}) if i.get("name")}


def login():
    s = requests.Session()
    s.headers.update(HEADERS)
    page = s.get(LOGIN_URL, timeout=30)
    data = hidden_fields(page.text)
    data.update({
        "ctl00$cphMain$FrontControl$lwLogin$tbUsername": USERNAME,
        "ctl00$cphMain$FrontControl$lwLogin$tbPassword": PASSWORD,
        "ctl00$cphMain$FrontControl$lwLogin$btnLogin": "Login",
    })
    r = s.post(LOGIN_URL, data=data, timeout=30)
    if "Logout" not in r.text:
        raise RuntimeError("Login to Xpert Eleven failed (check X11_USERNAME / X11_PASSWORD).")
    log("Logged in to Xpert Eleven.")
    return s


PAGE_COMPETITION = {
    os.environ.get("GOONDESLIGA_URL", "") or "-g": "Goondesliga",
    os.environ.get("SPOONDESLIGA_URL", "") or "-s": "Spoondesliga",
    os.environ.get("KZUP_URL", "") or "-k": "Kzar's Kzup",
}
CUP_HINT = {}   # gameId -> Cup value seen in the page's viewer links
UPCOMING = {}   # gameId -> fixture info for games not played yet

GID_RE = re.compile(r"[Gg]ame[Ii][Dd]=(\d+)")
SCORE_RE = re.compile(r"^\s*\d{1,2}\s*-\s*\d{1,2}(?:\s|\(|$)")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:\s+\d{1,2}:\d{2})?")
STAGE_RE = re.compile(r"(semi|quarter|final|round|group|kzup|eighth|16)", re.I)


def parse_fixture_rows(html, source_url):
    """Read every table row on the page that links to a game: date, home, away, score, stage."""
    soup = BeautifulSoup(html, "html.parser")
    rows = {}
    for tr in soup.find_all("tr"):
        m = GID_RE.search(str(tr.get("onclick", "")) + " ".join(a.get("href", "") for a in tr.find_all("a")))
        if not m:
            continue
        gid = m.group(1)
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        cells = [c for c in cells if c]
        date = next((DATE_RE.search(c).group(0) for c in cells if DATE_RE.search(c)), None)
        score = next((c for c in cells if SCORE_RE.match(c)), None)
        names = [c for c in cells if c not in (date, score, "-") and not DATE_RE.fullmatch(c) and not SCORE_RE.match(c)]
        # nearest heading above the row, e.g. "SEMIFINALS", "LAST ROUND"
        head = tr.find_previous(string=lambda t: t and STAGE_RE.search(t) and len(t.strip()) < 40)
        rows[gid] = {
            "gameId": int(gid),
            "date": date,
            "home": names[0] if len(names) > 0 else None,
            "away": names[1] if len(names) > 1 else None,
            "played": bool(score),
            "stage": head.strip().title() if head else None,
            "source": source_url,
        }
    return rows


def find_game_ids(s):
    ids = []
    for url in SOURCE_PAGES:
        try:
            html = s.get(url, timeout=30).text
        except Exception as e:
            log(f"  Could not read {url}: {e}")
            continue
        for cup, gid in re.findall(r"[Cc]up=(\d+)[^\"'<>]*?[Gg]ame[Ii][Dd]=(\d+)", html):
            CUP_HINT[gid] = int(cup)
        try:
            for gid, row in parse_fixture_rows(html, url).items():
                if not row["played"]:
                    row["competition"] = PAGE_COMPETITION.get(url)
                    UPCOMING[gid] = row
        except Exception as e:
            log(f"  Could not read fixtures on {url}: {e}")
        found = GID_RE.findall(html)
        log(f"  {len(set(found))} game links on {url}")
        ids.extend(found)
    ids.extend(EXTRA_IDS)
    return list(dict.fromkeys(ids))  # unique, keep order


def ticket_from(text_or_url):
    m = re.search(r"watch=([A-Za-z0-9_\-]+)", text_or_url or "")
    return m.group(1) if m else None


def get_ticket(s, game_id):
    """Open preGameViewer for the game and press 'HTML5 (beta) Start match'."""
    hint = CUP_HINT.get(str(game_id))
    for cup in ([hint, 1 - hint] if hint in (0, 1) else [0, 1]):
        url = PREGAME_URL.format(cup=cup, game_id=game_id)
        page = s.get(url, timeout=30)
        if "btWTG" not in page.text:
            continue
        data = hidden_fields(page.text)
        data["btWTG"] = "Start match"
        r = s.post(url, data=data, timeout=30, allow_redirects=False)
        # Usually a redirect to game.xperteleven.com/watchgame?watch=...
        loc = r.headers.get("Location", "")
        t = ticket_from(urljoin(url, loc)) if loc else None
        # Otherwise the ticket may be inside the returned page (JS redirect / link)
        if not t:
            t = ticket_from(r.text)
        if not t and r.is_redirect:
            r2 = s.get(urljoin(url, loc), timeout=30)
            t = ticket_from(r2.url) or ticket_from(r2.text)
        if t:
            return t
    return None


def fetch_match(s, game_id):
    ticket = get_ticket(s, game_id)
    if not ticket:
        log(f"  {game_id}: no watch ticket (match not played yet, or page changed)")
        return None
    r = requests.get(API_URL.format(ticket=ticket), headers=HEADERS, timeout=60)
    if r.status_code != 200:
        log(f"  {game_id}: API returned {r.status_code}")
        return None
    data = r.json()
    if not data.get("game", {}).get("played", True):
        log(f"  {game_id}: not played yet")
        return None
    return data


# ---------- scoring helpers (match goals vs shootout) ----------

def real_goals(data):
    """Goals in periods 1-4 (no disallowed, no penalty misses), plus shootout pens."""
    home = data["homeTeam"]["name"]
    goals = {"home": 0, "away": 0}
    pens = {"home": 0, "away": 0}
    for m in data.get("minutes", []):
        evs = m.get("events") or []
        for i, e in enumerate(evs):
            if e.get("type") != 60:
                continue
            text = (e.get("goalText") or "").lower()
            if "miss" in text and not e.get("goalType"):
                continue  # missed penalty logged as type 60
            disallowed = any(
                x.get("type") == 80 and (x.get("scorerId") == e.get("scorerId") or not x.get("scorerId"))
                for x in evs[i + 1:]
            )
            if disallowed:
                continue
            side = "home" if e.get("teamName") == home else "away"
            if (e.get("period") or m.get("period")) == 5:
                pens[side] += 1
            else:
                goals[side] += 1
    return goals, pens


def summarize(data, rel_path):
    g = data["game"]
    goals, pens = real_goals(data)
    season = g.get("season") or 0
    kzup_season = season % 5 == 4          # seasons ending in 4 or 9
    is_cup = g.get("type") == 2 or bool(g.get("cupRoundName")) or kzup_season
    return {
        "gameId": g.get("gameId"),
        "file": rel_path,
        "season": g.get("season"),
        "round": g.get("round"),
        "date": g.get("date") or g.get("dateString"),
        "competition": "Kzar's Kzup" if is_cup else (g.get("leagueName") or ""),
        "kzupEdition": (season - 14) // 5 if is_cup and kzup_season else None,
        "stage": g.get("cupRoundName") or g.get("divisonName"),   # e.g. "Group C", "Quarter final"
        "division": g.get("divisonName"),
        "cupRound": g.get("cupRoundName"),
        "home": data["homeTeam"]["name"],
        "away": data["awayTeam"]["name"],
        "homeGoals": goals["home"],
        "awayGoals": goals["away"],
        "homePens": pens["home"] if (pens["home"] or pens["away"]) else None,
        "awayPens": pens["away"] if (pens["home"] or pens["away"]) else None,
        "arena": g.get("arenaName"),
    }


def load_index():
    if INDEX_FILE.exists():
        return json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    return {"updated": None, "matches": []}


def main():
    if not USERNAME or not PASSWORD:
        notify("FSG fetcher: X11_USERNAME / X11_PASSWORD secrets are missing.")
        sys.exit(1)

    index = load_index()
    have = {str(m["gameId"]) for m in index["matches"]}

    try:
        s = login()
    except Exception as e:
        notify(f"FSG fetcher: {e}")
        sys.exit(1)

    ids = find_game_ids(s)
    new_ids = [i for i in ids if i not in have]
    log(f"{len(ids)} games found, {len(new_ids)} not yet archived.")

    added, failed = 0, 0
    for gid in new_ids:
        try:
            data = fetch_match(s, gid)
        except Exception as e:
            log(f"  {gid}: error {e}")
            failed += 1
            continue
        if not data:
            continue
        g = data["game"]
        folder = MATCH_DIR / f"S{g.get('season', 0)}" / f"R{int(g.get('round') or 0):02d}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{gid}.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        rel = path.relative_to(ROOT).as_posix()
        added += 1
        log(f"  {gid}: saved {data['homeTeam']['name']} vs {data['awayTeam']['name']} -> {rel}")
        time.sleep(2)  # be gentle with X11

    # Rebuild the index from every archived match, so summary fixes apply to old files too
    index["matches"] = []
    for f in sorted(MATCH_DIR.rglob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            index["matches"].append(summarize(data, f.relative_to(ROOT).as_posix()))
        except Exception as e:
            log(f"  Skipping unreadable file {f}: {e}")

    # Upcoming fixtures (for the venue / TV assigner), minus anything already archived
    archived = {str(m["gameId"]) for m in index["matches"]}
    index["upcoming"] = sorted((r for g, r in UPCOMING.items() if g not in archived),
                               key=lambda r: (r.get("date") or "", r["gameId"]))
    log(f"{len(index['upcoming'])} upcoming fixtures listed.")

    index["matches"].sort(key=lambda m: (m.get("season") or 0, m.get("date") or "", m.get("gameId") or 0),
                          reverse=True)
    index["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    INDEX_FILE.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    log(f"Done: {added} new matches archived, {failed} errors.")
    if new_ids and added == 0 and failed:
        notify(f"FSG fetcher: found {len(new_ids)} new games but couldn't download any. X11 may have changed something.")


if __name__ == "__main__":
    main()
