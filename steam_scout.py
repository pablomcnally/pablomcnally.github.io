#!/usr/bin/env python3
# steam_scout.py — v1.8.3
import os, time, csv, argparse, math, re, subprocess, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
import requests

# ------------ Config ------------
HEADERS = {"User-Agent": "Escapist-SteamScout/1.8.3 (+editorial discovery)"}
STORE_FEATURE = "https://store.steampowered.com/api/featuredcategories"
STORE_APPDETAILS = "https://store.steampowered.com/api/appdetails"
API_CURRENT_PLAYERS = "https://api.steampowered.com/ISteamUserStats/GetNumberOfCurrentPlayers/v1/"
API_NEWS = "https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/"
API_STEAMSPY = "https://steamspy.com/api.php"

STEAM_KEY = os.getenv("STEAM_API_KEY")
GOOGLE_SHEETS_ID = os.getenv("GOOGLE_SHEETS_ID")
GOOGLE_CREDS = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")

TODAY = datetime.now(timezone.utc).date().isoformat()

LOG_FILE = "steam_scout_log.csv"
TODAY_FILE = "steam_scout_today.csv"
TODAY_SHEET_FILE = "steam_scout_today_readable.csv"
WATCHLIST_FILE = "steam_scout_watchlist.csv"
DEBUG_FILE = "steam_scout_debug.csv"

# ------------ HTTP helper with tiny retry ------------
def http_json(url, params, name, timeout=20, tries=3, backoff=0.6):
    last_err = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=HEADERS, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            time.sleep(backoff * (i + 1))
    raise RuntimeError(f"{name} failed after {tries} tries: {last_err}")

# ------------ Fetch candidates ------------
def get_featured_new_releases(cc="US", lang="english"):
    data = http_json(STORE_FEATURE, {"cc": cc, "l": lang}, "featuredcategories")
    appids = set()
    blocks = []
    # cover historical key variants
    for key in ("new_releases", "NewReleases", "coming_soon", "ComingSoon",
                "specials", "Specials", "top_sellers", "TopSellers"):
        block = data.get(key)
        if block:
            if isinstance(block, dict) and "items" in block:
                blocks.extend(block["items"])
            elif isinstance(block, list):
                blocks.extend(block)
    for it in blocks:
        aid = it.get("id") or it.get("appid")
        if aid:
            appids.add(int(aid))
    return list(appids)

def appdetails(appid, cc="US", lang="english"):
    data = http_json(STORE_APPDETAILS, {"appids": appid, "cc": cc, "l": lang}, "appdetails")
    entry = data.get(str(appid), {})
    if not entry or not entry.get("success"):
        return {}
    return entry.get("data", {}) or {}

def parse_release_date(rd):
    date_str = (rd or {}).get("date", "")
    coming = (rd or {}).get("coming_soon", False)
    for fmt in ("%b %d, %Y", "%d %b, %Y", "%b %Y", "%Y"):
        try:
            return datetime.strptime(date_str, fmt), coming
        except Exception:
            pass
    return None, coming

def current_players(appid):
    j = http_json(API_CURRENT_PLAYERS, {"appid": appid}, "currentplayers")
    return j.get("response", {}).get("player_count")

def recent_news_count(appid, days=14):
    j = http_json(API_NEWS, {"appid": appid, "count": 100}, "news")
    items = j.get("appnews", {}).get("newsitems", []) or []
    cutoff = time.time() - days * 86400
    return sum(1 for n in items if n.get("date", 0) >= cutoff)

def steamspy(appid):
    try:
        j = http_json(API_STEAMSPY, {"request": "appdetails", "appid": appid}, "steamspy")
        return {
            "owners": j.get("owners", ""),
            "players_2weeks": j.get("players_2weeks", ""),
            "average_forever": j.get("average_forever", "")
        }
    except Exception:
        return {"owners": "", "players_2weeks": "", "average_forever": ""}

def price_string(d):
    if d.get("is_free"): return "Free"
    pov = d.get("price_overview") or {}
    return pov.get("final_formatted", "")

def is_probably_aaa(d, price_threshold=49.99):
    pub = ", ".join(d.get("publishers", [])).lower()
    big = any(x in pub for x in [
        "ubisoft","ea","electronic arts","activision","sony","microsoft",
        "bethesda","bandai","square enix","capcom","sega","2k"
    ])
    price = price_string(d)
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)", price.replace(",", ""))
    num = float(m.group(1)) if m else 0.0
    return big or num >= price_threshold

# ------------ CSV helpers ------------
def load_csv(fname):
    if not os.path.exists(fname):
        return []
    with open(fname, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def save_csv(fname, rows, fieldnames=None):
    """Union-safe CSV writer for old logs/new columns."""
    if not rows:
        with open(fname, "w", newline="", encoding="utf-8") as f:
            if fieldnames:
                csv.DictWriter(f, fieldnames=fieldnames).writeheader()
        return
    if fieldnames is None:
        keys = set()
        for r in rows:
            keys.update(r.keys())
        fieldnames = list(keys)
    norm_rows = [{k: r.get(k, "") for k in fieldnames} for r in rows]
    with open(fname, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(norm_rows)

# ------------ Trends & Watchlist ------------
def compute_7d_trends(log_rows):
    per_app = defaultdict(list)
    for r in log_rows:
        per_app[r["appid"]].append(r)
    for a in per_app.values():
        a.sort(key=lambda x: x["date"])
    cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    trends = []
    for aid, rows in per_app.items():
        window = [r for r in rows if r["date"] >= cutoff]
        if not window:
            continue
        def fnum(r, k):
            try: return float(r.get(k) or 0)
            except: return 0.0
        latest, earliest = window[-1], window[0]
        ccu_l, ccu_e = fnum(latest, "ccu_now"), fnum(earliest, "ccu_now")
        pct = (ccu_l - ccu_e) / ccu_e * 100 if ccu_e > 0 else (100 if ccu_l > 0 else 0)
        trends.append({
            "appid": aid,
            "title": latest.get("title", ""),
            "release_date": latest.get("release_date", ""),
            "publisher": latest.get("publisher", ""),
            "price": latest.get("price", ""),
            "is_free": latest.get("is_free", ""),
            "genres": latest.get("genres", ""),
            "ccu_latest": round(ccu_l, 2),
            "ccu_pct_change_7d": round(pct, 2),
            "days_in_window": len(window),
        })
    return sorted(trends, key=lambda r: (r["ccu_pct_change_7d"], r["ccu_latest"]), reverse=True)

def build_watchlist(trends, min_ccu=100, min_pct=25.0, require_history=False):
    rows = []
    for t in trends:
        ccu = t.get("ccu_latest", 0) or 0
        pct = t.get("ccu_pct_change_7d", 0) or 0
        days = t.get("days_in_window", 0) or 0
        if ccu < min_ccu:
            continue
        if require_history:
            if days >= 2 and pct >= min_pct:
                rows.append(t)
        else:
            if days < 2 or pct >= min_pct:
                rows.append(t)
    rows.sort(key=lambda r: (r["days_in_window"] >= 2, r["ccu_pct_change_7d"], r["ccu_latest"]), reverse=True)
    return rows

# ------------ Sheets push ------------
def push_csv_to_sheet(csv_path, sheet_id, worksheet_name):
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        from datetime import datetime as _dt
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        creds = Credentials.from_service_account_file(GOOGLE_CREDS, scopes=scopes)
        gc = gspread.authorize(creds)
        sh = gc.open_by_key(sheet_id)
        try:
            ws = sh.worksheet(worksheet_name)
            ws.clear()
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=worksheet_name, rows="2000", cols="50")
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows = list(csv.reader(f))
        if not rows:
            print(f"[Sheets] (No rows) {worksheet_name}")
            return
        headers = rows[0]
        # UK date formatting for any header containing "Date"
        for i, h in enumerate(headers):
            if "Date" in h:
                for j in range(1, len(rows)):
                    d = rows[j][i]
                    try:
                        if d and "-" in d:
                            rows[j][i] = _dt.strptime(d, "%Y-%m-%d").strftime("%d/%m/%Y")
                    except Exception:
                        pass
        # new signature: values first, then range_name
        ws.update(rows, "A1")
        print(f"[Sheets] ✅ Pushed {len(rows)-1} rows to '{worksheet_name}'")
    except Exception as e:
        print(f"[Sheets] ❌ Push failed for {worksheet_name}: {e}")

# ------------ Pretty tables for Sheets ------------
def make_today_sheet_csv(raw):
    def as_int(v):
        try: return int(float(v))
        except: return 0
    sorted_rows = sorted(raw, key=lambda r: as_int(r.get("ccu_now", 0)), reverse=True)
    cols = [("date","Date"),("title","Game Title"),("release_date","Release Date"),
            ("publisher","Publisher"),("price","Price"),("is_free","Free?"),
            ("genres","Genres"),("ccu_now","Current Players (Now)"),
            ("news_posts_14d","Dev News Posts (14 Days)"),
            ("steamspy_owners","Estimated Owners"),
            ("steamspy_players_2w","2-Week Players (Latest)"),
            ("steamspy_avg_mins_total","Avg Minutes Played (Lifetime)")]
    out=[]
    for r in sorted_rows:
        row={n:r.get(k,"") for k,n in cols}
        row["Store Link"]=f"https://store.steampowered.com/app/{r.get('appid')}/"
        out.append(row)
    save_csv(TODAY_SHEET_FILE,out,[n for _,n in cols]+["Store Link"])
    return TODAY_SHEET_FILE

def make_watchlist_csv(trends):
    cols=["Game Title","Release Date","Publisher","Price","Free?","Genres",
          "Current Players (Latest)","7-Day Player % Change","Days in Trend Window","Store Link"]
    with open(WATCHLIST_FILE,"w",newline="",encoding="utf-8") as f:
        w=csv.DictWriter(f,fieldnames=cols)
        w.writeheader()
        for t in trends:
            w.writerow({
                "Game Title":t.get("title",""),
                "Release Date":t.get("release_date",""),
                "Publisher":t.get("publisher",""),
                "Price":t.get("price",""),
                "Free?":t.get("is_free",""),
                "Genres":t.get("genres",""),
                "Current Players (Latest)":t.get("ccu_latest",""),
                "7-Day Player % Change":t.get("ccu_pct_change_7d",""),
                "Days in Trend Window":t.get("days_in_window",""),
                "Store Link":f"https://store.steampowered.com/app/{t.get('appid')}/"
            })
    return WATCHLIST_FILE

# ------------ Main ------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days",type=int,default=21)
    ap.add_argument("--region",default="US")
    ap.add_argument("--limit",type=int,default=200)
    ap.add_argument("--exclude_aaa",action="store_true")
    ap.add_argument("--no_sheets",action="store_true")
    ap.add_argument("--watch_min_ccu",type=int,default=100)
    ap.add_argument("--watch_min_pct",type=float,default=25.0)
    ap.add_argument("--watch_require_history",action="store_true")
    ap.add_argument("--debug",action="store_true")
    ap.add_argument("--no_release_filter",action="store_true", help="Skip release-date window filter")
    args=ap.parse_args()

    def dbg(msg):
        if args.debug: print(msg)

    # Candidate pool
    pool=get_featured_new_releases(cc=args.region)
    dbg(f"[debug] featuredcategories pool size: {len(pool)} (region={args.region})")
    pool=pool[:args.limit]

    lookback=datetime.now(timezone.utc)-timedelta(days=args.days)
    today=[]
    debug_rows=[]
    kept=dropped_type=dropped_coming=dropped_release=dropped_aaa=detail_fail=0

    for aid in pool:
        try:
            d=appdetails(aid,cc=args.region)
            if not d:
                detail_fail+=1
                debug_rows.append({"appid":aid,"title":"","reason":"appdetails_failed"})
                continue
            if d.get("type")!="game":
                dropped_type+=1
                debug_rows.append({"appid":aid,"title":d.get("name",""),"reason":f"type_{d.get('type')}"})
                continue

            rd,coming=parse_release_date(d.get("release_date",{}))
            if not args.no_release_filter:
                if coming:
                    dropped_coming+=1
                    debug_rows.append({"appid":aid,"title":d.get("name",""),"reason":"coming_soon"})
                    continue
                if not rd:
                    dropped_release+=1
                    debug_rows.append({"appid":aid,"title":d.get("name",""),"reason":"no_parseable_release_date"})
                    continue
                if rd<lookback:
                    dropped_release+=1
                    debug_rows.append({"appid":aid,"title":d.get("name",""),"reason":f"older_than_window({rd.date().isoformat()})"})
                    continue
            else:
                debug_rows.append({"appid":aid,"title":d.get("name",""),"reason":"kept_no_release_filter"})

            if args.exclude_aaa and is_probably_aaa(d):
                dropped_aaa+=1
                debug_rows.append({"appid":aid,"title":d.get("name",""),"reason":"probable_AAA"})
                continue

            ccu=current_players(aid)
            news=recent_news_count(aid)
            spy=steamspy(aid)
            price=price_string(d)
            genres=", ".join(g["description"] for g in d.get("genres",[]) if "description" in g)

            today.append({
                "date":TODAY,"appid":str(aid),"title":d.get("name",""),
                "release_date": rd.strftime("%Y-%m-%d") if rd else "",
                "publisher":", ".join(d.get("publishers",[])),
                "price":price,"is_free":"yes" if d.get("is_free") else "",
                "genres":genres,"ccu_now":str(ccu or 0),"news_posts_14d":str(news),
                "steamspy_owners":spy.get("owners",""),
                "steamspy_players_2w":spy.get("players_2weeks",""),
                "steamspy_avg_mins_total":spy.get("average_forever","")
            })
            kept+=1
            time.sleep(0.2)
        except Exception as e:
            detail_fail+=1
            debug_rows.append({"appid":aid,"title":"","reason":f"exception_{type(e).__name__}"})
            continue

    print(f"[summary] pool={len(pool)} kept={kept} dropped_type={dropped_type} dropped_coming={dropped_coming} dropped_release={dropped_release} dropped_aaa={dropped_aaa} appdetail_fail={detail_fail}")
    if debug_rows:
        save_csv(DEBUG_FILE, debug_rows)

    # Auto-fallback run if release filter killed everything
    if not today:
        if not args.no_release_filter:
            print("[fallback] No rows after release filter; retrying without it once…")
            cmd = [sys.executable, sys.argv[0]] + [a for a in sys.argv[1:] if a != "--no_sheets"]
            if "--no_release_filter" not in cmd:
                cmd.append("--no_release_filter")
            if args.no_sheets:
                cmd.append("--no_sheets")
            subprocess.run(cmd, check=False)
            return
        else:
            print("No games found in window.")
            return

    # Write today's snapshot & log (union-safe)
    save_csv(TODAY_FILE,today)
    log=load_csv(LOG_FILE)
    merged = log + today
    save_csv(LOG_FILE, merged)

    # Build trends & watchlist
    trends = compute_7d_trends(merged)
    watch = build_watchlist(trends, args.watch_min_ccu, args.watch_min_pct, args.watch_require_history)

    # Build Sheets CSVs
    today_sheet = make_today_sheet_csv(today)
    watch_sheet = make_watchlist_csv(watch)

    # Push to Sheets if configured
    if not args.no_sheets and GOOGLE_SHEETS_ID and GOOGLE_CREDS and os.path.exists(GOOGLE_CREDS):
        push_csv_to_sheet(today_sheet, GOOGLE_SHEETS_ID, "Today")
        push_csv_to_sheet(watch_sheet, GOOGLE_SHEETS_ID, "Watchlist")
    elif not args.no_sheets:
        print("[Sheets] Skipped (missing GOOGLE_SHEETS_ID or GOOGLE_APPLICATION_CREDENTIALS)")

if __name__=="__main__":
    main()
