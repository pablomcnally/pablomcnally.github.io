#!/usr/bin/env python3
"""
Steam Scout v2.0 – with hourly trend logging

- Fetches featured/new games from the Steam Store API
- Looks up current CCU via the Steam Web API
- Logs every run to a History sheet
- Computes real 3-day % change per game from History
- Writes three tabs to Google Sheets:
    - Today
    - 7d Trends (includes 3-day % change and 3-day oldest CCU)
    - Watchlist (filtered by CCU + 3-day growth)
"""

import os
import csv
import sys
import time
import argparse
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import requests
import gspread

# --------------------------------------------------------------------
# Config
# --------------------------------------------------------------------

HEADERS = {
    "User-Agent": "Escapist-SteamScout/2.0 (+editorial discovery)"
}

TODAY_CSV = "steam_scout_today.csv"
TRENDS_CSV = "steam_scout_7day_trends.csv"
WATCHLIST_CSV = "steam_scout_watchlist.csv"
DEBUG_CSV = "steam_scout_debug.csv"

TODAY_SHEET_NAME = "Today"
TRENDS_SHEET_NAME = "7d Trends"
WATCHLIST_SHEET_NAME = "Watchlist"
HISTORY_SHEET_NAME = "History"

# --------------------------------------------------------------------
# Helpers – HTTP
# --------------------------------------------------------------------


def http_get_json(url: str, params: Dict[str, Any] = None, timeout: int = 15) -> Any:
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                return r.json()
            time.sleep(1)
        except Exception:
            time.sleep(1)
    return None


# --------------------------------------------------------------------
# Steam data collection
# --------------------------------------------------------------------


def fetch_featured_pool(region: str, limit: int, debug: bool = False) -> List[int]:
    """
    Use Steam Storefront featuredcategories to build an appid pool.
    We focus on new/featured things vs all of Steam.
    """
    url = "https://store.steampowered.com/api/featuredcategories"
    data = http_get_json(url, params={"cc": region, "l": "en"})
    if not data:
        print("[error] failed to fetch featuredcategories", file=sys.stderr)
        return []

    pool_ids = []
    buckets = ["new_releases", "specials", "topnewreleases", "coming_soon", "topsellers"]

    for b in buckets:
        section = data.get(b) or {}
        items = section.get("items") or []
        for it in items:
            appid = it.get("id")
            if appid and appid not in pool_ids:
                pool_ids.append(appid)

    if debug:
        print(f"[debug] featuredcategories pool size: {len(pool_ids)} (region={region})")

    if limit and len(pool_ids) > limit:
        pool_ids = pool_ids[:limit]

    return pool_ids


def fetch_appdetails(appid: int, region: str) -> Dict[str, Any]:
    """
    Steam Store appdetails for metadata.
    """
    url = "https://store.steampowered.com/api/appdetails"
    data = http_get_json(url, params={"appids": appid, "cc": region, "l": "en"})
    if not data or str(appid) not in data:
        return {}

    entry = data[str(appid)]
    if not entry.get("success"):
        return {}

    return entry.get("data") or {}


def parse_release_date(raw: str) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    # Try a few Steam formats
    fmts = ["%d %b, %Y", "%b %d, %Y", "%d %B, %Y", "%B %d, %Y", "%Y-%m-%d"]
    for fmt in fmts:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def extract_basic_row(appid: int, ad: Dict[str, Any], region: str) -> Dict[str, Any]:
    """
    Turn an appdetails payload into our core row structure.
    """
    name = ad.get("name") or ""
    release = ad.get("release_date") or {}
    release_str = release.get("date") or ""
    release_dt = parse_release_date(release_str)

    publishers = ad.get("publishers") or []
    publisher = ", ".join(publishers) if publishers else ""

    price_overview = ad.get("price_overview") or {}
    initial = price_overview.get("initial")  # in cents
    final = price_overview.get("final")
    currency = price_overview.get("currency") or "USD"

    if ad.get("is_free"):
        price_str = "Free"
    elif final is not None:
        # convert cents to major unit
        price_value = final / 100.0
        symbol = "$" if currency == "USD" else ""
        price_str = f"{symbol}{price_value:.2f}"
    else:
        price_str = ""

    genres = ad.get("genres") or []
    genre_names = ", ".join([g.get("description", "") for g in genres if g.get("description")])

    categories = ad.get("categories") or []
    category_names = ", ".join([c.get("description", "") for c in categories if c.get("description")])

    store_link = f"https://store.steampowered.com/app/{appid}/"

    row = {
        "Steam App ID": appid,
        "Game Title": name,
        "Release Date": release_dt.strftime("%Y-%m-%d") if release_dt else release_str,
        "Publisher": publisher,
        "Price": price_str,
        "Free?": "Yes" if ad.get("is_free") else "No",
        "Genres": genre_names,
        "Categories": category_names,
        "Store Link": store_link,
        # placeholders – filled later
        "Current Players (Latest)": 0,
        "Avg Players (7 Days)": 0,
        "Peak Players (7 Days)": 0,
        "Lowest Players (7 Days)": 0,
        "Player Change (7 Days)": 0,
        "7-Day Player % Change": 0,
        "2-Week Players (Latest)": 0,
        "2-Week Player Change": 0,
        "Dev News Posts (14 Days)": 0,
        "Estimated Owners": "",
        "Scout Score": 0.0,
    }
    return row


def fetch_current_players(appid: int, api_key: str) -> int:
    if not api_key:
        return 0
    url = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
    data = http_get_json(url, params={"key": api_key, "appid": appid})
    if not data:
        return 0
    try:
        return int(data.get("response", {}).get("player_count", 0))
    except Exception:
        return 0


# --------------------------------------------------------------------
# CSV / Sheets helpers
# --------------------------------------------------------------------


def save_csv(path: str, rows: List[Dict[str, Any]], headers: List[str]) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=headers)
        w.writeheader()
        for r in rows:
            w.writerow({h: r.get(h, "") for h in headers})


def get_gspread_client() -> gspread.Client:
    # creds.json should already exist (GitHub Action writes it)
    return gspread.service_account(filename="creds.json")


def ensure_worksheet(sh: gspread.Spreadsheet, title: str, rows: int = 1000, cols: int = 30) -> gspread.Worksheet:
    try:
        return sh.worksheet(title)
    except gspread.WorksheetNotFound:
        return sh.add_worksheet(title=title, rows=rows, cols=cols)


def push_sheet_tab(gs_client: gspread.Client, sheet_id: str, tab_name: str,
                   headers: List[str], rows: List[Dict[str, Any]]) -> None:
    sh = gs_client.open_by_key(sheet_id)
    ws = ensure_worksheet(sh, tab_name, rows=max(1000, len(rows) + 10), cols=len(headers) + 2)
    # Clear and write fresh
    ws.clear()
    values = [headers]
    for r in rows:
        values.append([r.get(h, "") for h in headers])
    if values:
        ws.update("A1", values)


# --------------------------------------------------------------------
# History logging + 3-day trend calculation
# --------------------------------------------------------------------


def append_history_rows(gs_client: gspread.Client, sheet_id: str, today_rows: List[Dict[str, Any]]) -> None:
    """
    Append snapshot rows to the History tab:
    timestamp_utc, appid, game_title, current_players
    """
    if not today_rows:
        return

    sh = gs_client.open_by_key(sheet_id)
    ws = ensure_worksheet(sh, HISTORY_SHEET_NAME, rows=2000, cols=4)

    # Ensure header row is correct
    values = ws.get_all_values()
    if not values:
        # Empty sheet, create header
        ws.append_row(["timestamp_utc", "appid", "game_title", "current_players"])
        values = ws.get_all_values()
    else:
        header = values[0]
        if "timestamp_utc" not in header:
            # Legacy / wrong header – reset the sheet once
            ws.clear()
            ws.append_row(["timestamp_utc", "appid", "game_title", "current_players"])
            values = ws.get_all_values()

    now_utc = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows_to_append = []

    for row in today_rows:
        appid = row.get("Steam App ID")
        title = row.get("Game Title")
        ccu = row.get("Current Players (Latest)")
        if not appid or ccu is None:
            continue
        try:
            ccu_int = int(ccu)
        except Exception:
            continue
        rows_to_append.append([now_utc, str(appid), str(title or ""), ccu_int])

    if rows_to_append:
        ws.append_rows(rows_to_append, value_input_option="RAW")

    # Trim history older than 8 days (optional tidy-up)
    values = ws.get_all_values()
    if len(values) <= 1:
        return

    header = values[0]
    if "timestamp_utc" not in header:
        # Something odd – don't try to trim
        return

    ts_idx = header.index("timestamp_utc")
    eight_days_ago = datetime.now(timezone.utc) - timedelta(days=8)

    keep = [header]
    for r in values[1:]:
        try:
            ts = datetime.fromisoformat(r[ts_idx])
        except Exception:
            keep.append(r)
            continue
        if ts >= eight_days_ago:
            keep.append(r)

    if len(keep) != len(values):
        ws.clear()
        ws.append_rows(keep, value_input_option="RAW")


def compute_3d_trends_from_history(gs_client: gspread.Client, sheet_id: str,
                                   appids: List[int]) -> Dict[int, Dict[str, Any]]:
    """
    From History tab, compute per-appid:
        - ccu_3d_ago  (oldest CCU in last 3 days)
        - pct_change_3d (latest vs oldest)
    Returns {appid: {ccu_3d_ago, pct_change_3d}}
    """
    result: Dict[int, Dict[str, Any]] = {}
    if not appids:
        return result

    sh = gs_client.open_by_key(sheet_id)
    try:
        ws = sh.worksheet(HISTORY_SHEET_NAME)
    except gspread.WorksheetNotFound:
        return result

    values = ws.get_all_values()
    if len(values) <= 1:
        return result

    header = values[0]
    ts_idx = header.index("timestamp_utc")
    app_idx = header.index("appid")
    ccu_idx = header.index("current_players")

    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    per_app: Dict[str, List[tuple]] = {}

    for r in values[1:]:
        try:
            ts = datetime.fromisoformat(r[ts_idx])
        except Exception:
            continue
        if ts < cutoff:
            continue
        appid = r[app_idx]
        try:
            ccu = int(float(r[ccu_idx]))
        except Exception:
            continue
        per_app.setdefault(appid, []).append((ts, ccu))

    for appid in appids:
        key = str(appid)
        hist = per_app.get(key)
        if not hist or len(hist) < 2:
            result[appid] = {"ccu_3d_ago": None, "pct_change_3d": 0.0}
            continue

        hist.sort(key=lambda x: x[0])
        oldest_ts, oldest_ccu = hist[0]
        latest_ts, latest_ccu = hist[-1]

        if oldest_ccu <= 0:
            pct = 0.0
        else:
            pct = ((latest_ccu - oldest_ccu) / oldest_ccu) * 100.0

        result[appid] = {"ccu_3d_ago": oldest_ccu, "pct_change_3d": pct}

    return result


# --------------------------------------------------------------------
# Main
# --------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Steam Scout – editorial radar with 3-day trends")
    parser.add_argument("--region", default="US", help="Steam store country code, e.g. US, GB, DE")
    parser.add_argument("--days", type=int, default=21, help="Lookback for release date filter")
    parser.add_argument("--limit", type=int, default=200, help="Max number of apps to consider from featured pool")
    parser.add_argument("--watch_min_ccu", type=int, default=100, help="Watchlist minimum CCU")
    parser.add_argument("--watch_min_pct", type=float, default=25.0, help="Watchlist minimum 3-day %% growth")
    parser.add_argument("--no_release_filter", action="store_true", help="Ignore release date lookback")
    parser.add_argument("--debug", action="store_true", help="Verbose debugging output")

    args = parser.parse_args()

    steam_key = os.environ.get("STEAM_API_KEY", "").strip()
    sheet_id = os.environ.get("GOOGLE_SHEETS_ID", "").strip()

    if not sheet_id:
        print("[error] GOOGLE_SHEETS_ID env var is required", file=sys.stderr)
        sys.exit(1)

    # 1) Build pool
    pool = fetch_featured_pool(args.region, args.limit, debug=args.debug)
    if not pool:
        print("No apps in pool.", file=sys.stderr)
        sys.exit(0)

    lookback_cutoff = datetime.now() - timedelta(days=args.days)
    today_rows: List[Dict[str, Any]] = []
    debug_rows: List[Dict[str, Any]] = []

    kept = 0
    dropped_type = 0
    dropped_coming = 0
    dropped_release = 0

    for appid in pool:
        ad = fetch_appdetails(appid, args.region)
        if not ad:
            continue

        if ad.get("type") not in ("game", "dlc"):
            dropped_type += 1
            continue

        release_info = ad.get("release_date") or {}
        coming_soon = release_info.get("coming_soon")
        if coming_soon:
            dropped_coming += 1
            continue

        rd = parse_release_date(release_info.get("date") or "")
        if rd and not args.no_release_filter and rd < lookback_cutoff:
            dropped_release += 1
            continue

        row = extract_basic_row(appid, ad, args.region)

        ccu = fetch_current_players(appid, steam_key)
        row["Current Players (Latest)"] = ccu

        today_rows.append(row)
        kept += 1

        if args.debug:
            debug_rows.append({
                "appid": appid,
                "name": row["Game Title"],
                "release": row["Release Date"],
                "ccu": ccu,
            })

    print(f"[summary] pool={len(pool)} kept={kept} dropped_type={dropped_type} "
          f"dropped_coming={dropped_coming} dropped_release={dropped_release}")

    if not today_rows:
        print("No games found in window.", file=sys.stderr)
        sys.exit(0)

    # 2) Save Today CSV
    today_headers = [
        "Game Title",
        "Release Date",
        "Publisher",
        "Price",
        "Free?",
        "Genres",
        "Current Players (Latest)",
        "Avg Players (7 Days)",
        "Peak Players (7 Days)",
        "Lowest Players (7 Days)",
        "Player Change (7 Days)",
        "7-Day Player % Change",
        "2-Week Players (Latest)",
        "2-Week Player Change",
        "Dev News Posts (14 Days)",
        "Estimated Owners",
        "Scout Score",
        "Store Link",
        "Steam App ID",
        "Categories",
    ]
    save_csv(TODAY_CSV, today_rows, today_headers)

    if args.debug:
        save_csv(DEBUG_CSV, debug_rows, ["appid", "name", "release", "ccu"])

    # 3) History + 3-day trends
    gs_client = get_gspread_client()
    append_history_rows(gs_client, sheet_id, today_rows)
    appids = [r["Steam App ID"] for r in today_rows]
    trends_3d = compute_3d_trends_from_history(gs_client, sheet_id, appids)

    # 4) Build 7d Trends rows (for now 7-day fields are placeholders, 3-day is real)
    trend_rows: List[Dict[str, Any]] = []
    for row in today_rows:
        appid = row["Steam App ID"]
        t3 = trends_3d.get(appid, {"ccu_3d_ago": None, "pct_change_3d": 0.0})

        tr = dict(row)  # copy
        tr["3-Day Players (Oldest)"] = t3.get("ccu_3d_ago")
        tr["3-Day Player % Change"] = t3.get("pct_change_3d", 0.0)
        trend_rows.append(tr)

    trend_headers = today_headers + ["3-Day Players (Oldest)", "3-Day Player % Change"]
    save_csv(TRENDS_CSV, trend_rows, trend_headers)

    # 5) Build Watchlist (using CCU + 3-day growth)
    watch_rows: List[Dict[str, Any]] = []
    for tr in trend_rows:
        ccu = tr.get("Current Players (Latest)") or 0
        pct3 = tr.get("3-Day Player % Change") or 0.0
        try:
            ccu = int(ccu)
        except Exception:
            ccu = 0
        try:
            pct3 = float(pct3)
        except Exception:
            pct3 = 0.0

        if ccu >= args.watch_min_ccu and pct3 >= args.watch_min_pct:
            watch_rows.append(tr)

    save_csv(WATCHLIST_CSV, watch_rows, trend_headers)

    # 6) Push to Google Sheets
    print(f"[Sheets] Preparing to push '{TODAY_CSV}' -> Tab:'{TODAY_SHEET_NAME}'")
    push_sheet_tab(gs_client, sheet_id, TODAY_SHEET_NAME, today_headers, today_rows)
    print(f"[Sheets] ✅ Pushed {len(today_rows)} rows to '{TODAY_SHEET_NAME}'")

    print(f"[Sheets] Preparing to push '{TRENDS_CSV}' -> Tab:'{TRENDS_SHEET_NAME}'")
    push_sheet_tab(gs_client, sheet_id, TRENDS_SHEET_NAME, trend_headers, trend_rows)
    print(f"[Sheets] ✅ Pushed {len(trend_rows)} rows to '{TRENDS_SHEET_NAME}'")

    print(f"[Sheets] Preparing to push '{WATCHLIST_CSV}' -> Tab:'{WATCHLIST_SHEET_NAME}'")
    push_sheet_tab(gs_client, sheet_id, WATCHLIST_SHEET_NAME, trend_headers, watch_rows)
    print(f"[Sheets] ✅ Pushed {len(watch_rows)} rows to '{WATCHLIST_SHEET_NAME}'")


if __name__ == "__main__":
    main()
