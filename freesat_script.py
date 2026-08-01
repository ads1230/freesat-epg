import concurrent.futures
from datetime import datetime, timedelta, timezone
import html
import json
import os
import random
import re
import sys
import urllib.parse
import requests


def log(msg):
  now = datetime.now().strftime("%H:%M:%S")
  print(f"[{now}] {msg}")
  sys.stdout.flush()


# --- Configuration ---
DAYS = 8
LOGO_DIR = "logos_freesat"
CACHE_FILE = "freesat_cache.json"

REGIONS = {
    "Central_Scotland": "28964",
    "Highlands_and_Islands": "28960",
    "Border_Scotland": "28962",
    "Wales": "29080",
    "Northern_Ireland": "29190",
    "Channel_Islands": "28841",
    "London": "28801",
    "North_East": "28835",
    "North_West": "28807",
    "Yorkshire": "28810",
    "East_Yorks_and_Lincs": "28809",
    "West_Midlands": "28805",
    "East_Midlands": "28813",
    "East_Anglia": "28819",
    "South_East": "28826",
    "West": "28820",
    "South": "28828",
    "South_West": "28833",
}

# Freeview Play Content Classification Scheme
FREEVIEW_GENRES = {
    "0": ["Shopping"],
    "1": ["Movie", "Film"],
    "2": ["News", "Factual", "Documentary"],
    "3": ["Entertainment", "Comedy", "Game Show"],
    "4": ["Sports"],
    "5": ["Children", "Kids"],
    "6": ["Music"],
    "7": ["Lifestyle", "Reality"],
    "8": ["Drama", "Soap"],
    "9": ["Arts", "Education"],
}

GITHUB_REPO_FULL = os.getenv("GITHUB_REPOSITORY", "YourUsername/YourRepo")
GITHUB_USER, GITHUB_REPO = (
    GITHUB_REPO_FULL.split("/")
    if "/" in GITHUB_REPO_FULL
    else ("Unknown", "Unknown")
)
GITHUB_RAW_BASE = (
    f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/main/{LOGO_DIR}/"
)

UAS = [
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15"
        " (KHTML, like Gecko) Version/17.3 Safari/605.1.15"
    ),
    (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101"
        " Firefox/123.0"
    ),
    (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like"
        " Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
]


def clean_xml_text(text):
  if not text:
    return ""
  return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ufffe\uffff]", "", str(text))


def load_cache():
  if os.path.exists(CACHE_FILE):
    try:
      with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)
    except Exception:
      pass
  return {}


def get_freeview_category(genre_urn):
  if not genre_urn:
    return []
  try:
    val = str(genre_urn).split(":")[-1]
    main_cat = val.split(".")[0]
    return FREEVIEW_GENRES.get(main_cat, [])
  except Exception:
    return []


def fetch_deep_info(crid, prog_url, session):
  try:
    r = session.get(prog_url, timeout=15)
    if r.status_code == 200:
      p_data = r.json().get("data", {})
      if isinstance(p_data, dict):
        syn = p_data.get("synopsis", {})
        desc_val = (
            p_data.get("description")
            or syn.get("medium", "")
            or syn.get("short", "")
        )

        access = p_data.get("access_services", {})
        if not access and "events" in p_data and p_data["events"]:
          access = p_data["events"][0].get("access_services", {})

        tv_access = access.get("tv", {}) if isinstance(access, dict) else {}

        return crid, {
            "sub": p_data.get("episodeTitle")
            or p_data.get("secondary_title", ""),
            "desc": desc_val,
            "subs": tv_access.get(
                "subtitles", p_data.get("hasSubtitles", False)
            ),
            "ad": tv_access.get(
                "audio_description", p_data.get("hasAudioDescription", False)
            ),
            "genre": p_data.get("genre"),
        }, 200
    return crid, {}, r.status_code
  except Exception as e:
    return crid, {}, str(e)


def run(target_region=None):
  if not os.path.exists(LOGO_DIR):
    os.makedirs(LOGO_DIR)
  meta_cache = load_cache()
  now_utc = datetime.now(timezone.utc)
  start_of_today = datetime(
      now_utc.year, now_utc.month, now_utc.day, tzinfo=timezone.utc
  )

  items = (
      [(target_region, REGIONS[target_region])]
      if target_region in REGIONS
      else REGIONS.items()
  )

  for region_name, nid in items:
    log(f"--- REGION: {region_name} (Freesat) ---")

    session = requests.Session()
    session.headers.update({"User-Agent": random.choice(UAS)})

    channels, progs, missing_crids = {}, [], {}

    # PASS 0: Fetch Channel Logos and LCNs
    log("   [INFO] Checking channel logos and LCNs...")
    try:
      r_chan = session.get(
          f"https://www.freesat.co.uk/api/channel-list?nid={nid}", timeout=15
      )
      if r_chan.status_code == 200:
        missing_logos = []
        for chan in r_chan.json().get("data", {}).get("services", []):
          cid = str(chan.get("service_id"))

          lcn_val = (
              chan.get("logical_channel_number")
              or chan.get("number")
              or chan.get("lcn")
          )
          if cid not in channels:
            channels[cid] = {
                "name": chan.get("title", "Unknown"),
                "lcn": str(lcn_val) if lcn_val is not None else "",
            }
          else:
            channels[cid]["lcn"] = str(lcn_val) if lcn_val is not None else ""

          logo_url = chan.get("service_image")
          if (
              not logo_url
              and "images" in chan
              and isinstance(chan["images"], dict)
          ):
            logo_url = chan["images"].get("default") or chan["images"].get(
                "square_white"
            )

          if cid and logo_url:
            logo_path = os.path.join(LOGO_DIR, f"{cid}.png")
            if not os.path.exists(logo_path):
              fetch_url = logo_url + ("&w=800" if "?" in logo_url else "?w=800")
              missing_logos.append((cid, logo_path, fetch_url))

        total_logos = len(missing_logos)
        if total_logos > 0:
          log(
              f"   [INFO] Found {total_logos} missing channel logos."
              " Downloading..."
          )
          completed = 0
          for cid, path, url in missing_logos:
            try:
              img_data = session.get(url, timeout=10).content
              with open(path, "wb") as handler:
                handler.write(img_data)
            except Exception:
              pass

            completed += 1
            update_iv = max(1, total_logos // 20)
            if completed % update_iv == 0 or completed == total_logos:
              pct = completed / total_logos
              bar_len = 20
              filled = int(bar_len * pct)
              bar = "█" * filled + "-" * (bar_len - filled)
              log(
                  f"   Logo Progress: [{bar}] {pct*100:.1f}%"
                  f" ({completed}/{total_logos})"
              )
        else:
          log("   [INFO] All channel logos are already up to date.")
    except Exception as e:
      log(f"   [WARNING] Failed to fetch channel logos: {e}")

    # PASS 1: Build Schedule
    for day in range(DAYS):
      ts = int((start_of_today + timedelta(days=day)).timestamp())
      try:
        r = session.get(
            f"https://www.freesat.co.uk/api/tv-guide?nid={nid}&start={ts}",
            timeout=15,
        )
        if r.status_code != 200:
          log(f"   [ERROR] Pass 1 Failed on Day {day+1}: HTTP {r.status_code}")
          continue

        day_chans = r.json().get("data", {}).get("programs", [])
        log(
            f"   [INFO] Day {day+1} parsed successfully ({len(day_chans)}"
            " channels)."
        )

        for chan in day_chans:
          cid = str(chan.get("service_id"))
          if cid not in channels:
            channels[cid] = {"name": chan.get("title", "Unknown"), "lcn": ""}

          for ev in chan.get("events", []):
            show_title = ev.get("main_title", "Unknown")
            try:
              crid = ev.get("program_id")
              start_str = ev.get("start_time")
              duration_str = ev.get("duration")

              if not crid or not start_str or not duration_str:
                continue

              start_dt = datetime.strptime(
                  start_str, "%Y-%m-%dT%H:%M:%S%z"
              ).astimezone(timezone.utc)
              s_time = start_dt.strftime("%Y%m%d%H%M%S +0000")

              h_match = re.search(r"(\d+)H", duration_str)
              m_match = re.search(r"(\d+)M", duration_str)
              h = int(h_match.group(1)) if h_match else 0
              m = int(m_match.group(1)) if m_match else 0
              e_time = (start_dt + timedelta(hours=h, minutes=m)).strftime(
                  "%Y%m%d%H%M%S +0000"
              )

              if crid not in meta_cache:
                pid_q = urllib.parse.quote(crid, safe="")
                missing_crids[crid] = (
                    f"https://www.freesat.co.uk/api/program?sid={cid}&nid={nid}&pid={pid_q}"
                )

              show_img = (
                  ev.get("image_url") or ev.get("fallback_image_url") or ""
              )
              show_genre = ev.get("genre") or ""

              progs.append({
                  "cid": cid,
                  "crid": crid,
                  "t": show_title,
                  "img": show_img,
                  "s": s_time,
                  "e": e_time,
                  "genre": show_genre,
              })
            except Exception:
              pass
      except Exception as e:
        log(f"   [CRITICAL] Error parsing day {day+1}: {e}")

    # PASS 2: Metadata
    total_missing_list = list(missing_crids.items())
    total_to_fetch = len(total_missing_list)

    if total_to_fetch > 0:
      log(f"FETCHING {total_to_fetch} metadata items...")
      completed, success_count, blocked_count = 0, 0, 0

      with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [
            executor.submit(fetch_deep_info, c, u, session)
            for c, u in total_missing_list
        ]
        for f in concurrent.futures.as_completed(futures):
          crid, data, status = f.result()
          completed += 1

          if status == 200:
            meta_cache[crid] = data
            success_count += 1
          elif status == 404:
            meta_cache[crid] = {}
            success_count += 1
          elif status in [403, 429]:
            blocked_count += 1

          update_iv = max(1, total_to_fetch // 20)
          if completed % update_iv == 0 or completed == total_to_fetch:
            pct = completed / total_to_fetch
            bar_len = 20
            filled = int(bar_len * pct)
            bar = "█" * filled + "-" * (bar_len - filled)
            log(
                f"   Progress: [{bar}] {pct*100:.1f}% ({completed}/{total_to_fetch})"
                f" | Success: {success_count} | Blocks: {blocked_count}"
            )

          if blocked_count >= 5:
            executor.shutdown(wait=False, cancel_futures=True)
            break

      # SMART CACHE PRUNING (90MB TARGET)
      MAX_BYTES = 90 * 1024 * 1024

      while True:
        cache_str = json.dumps(meta_cache, separators=(",", ":"))
        cache_size = len(cache_str.encode("utf-8"))

        if cache_size <= MAX_BYTES:
          break

        items_to_remove = max(1000, len(meta_cache) // 20)
        meta_cache = dict(list(meta_cache.items())[items_to_remove:])
        log(
            f"   [CACHE WARNING] Size hit {cache_size / (1024*1024):.1f}MB."
            f" Pruned oldest {items_to_remove} items."
        )

      with open(CACHE_FILE, "w", encoding="utf-8") as f:
        f.write(cache_str)

    # PASS 3: Generate XML
    output_file = f"freesat_{region_name.lower()}.xml"
    log(f"Writing {output_file}...")
    with open(output_file, "w", encoding="utf-8") as f:
      f.write('<?xml version="1.0" encoding="UTF-8"?><tv>\n')
      for cid, info in channels.items():
        f.write(f'  <channel id="{cid}">\n')
        f.write(
            f'    <display-name>{html.escape(info["name"])}</display-name>\n'
        )
        if info.get("lcn"):
          f.write(f'    <lcn>{info["lcn"]}</lcn>\n')
        if os.path.exists(os.path.join(LOGO_DIR, f"{cid}.png")):
          f.write(f'    <icon src="{GITHUB_RAW_BASE}{cid}.png" />\n')
        f.write("  </channel>\n")

      for p in progs:
        m = meta_cache.get(p["crid"], {})
        f.write(
            f'  <programme start="{p["s"]}" stop="{p["e"]}"'
            f' channel="{p["cid"]}">\n'
        )
        f.write(f'    <title>{html.escape(clean_xml_text(p["t"]))}</title>\n')
        if m.get("sub"):
          f.write(
              "    <sub-title>"
              f"{html.escape(clean_xml_text(m['sub']))}</sub-title>\n"
          )

        desc = clean_xml_text(m.get("desc", ""))
        if m.get("ad"):
          desc = f"[AD] {desc}" if desc else "[AD]"
        if desc:
          f.write(f"    <desc>{html.escape(desc)}</desc>\n")

        cats = get_freeview_category(p.get("genre") or m.get("genre"))
        if cats:
          for cat in cats:
            f.write(f"    <category>{html.escape(cat)}</category>\n")

        if p["img"]:
          f.write(f'    <icon src="{html.escape(p["img"])}?w=800" />\n')
        if m.get("subs"):
          f.write('    <subtitles type="onscreen" />\n')
        f.write("  </programme>\n")
      f.write("</tv>")


if __name__ == "__main__":
  run(sys.argv[1] if len(sys.argv) > 1 else None)
