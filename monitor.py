#!/usr/bin/env python3
"""
Tweede Kamer OCW committee monitor.

Polls the Tweede Kamer Open Data (OData) API for new activities where the
lead committee (voortouwcommissie) is OCW, and pushes any new items to a
Telegram chat.

This is built for following up on a petition handed to the vaste commissie
voor Onderwijs, Cultuur en Wetenschap. Petition handling happens in the
committee's procedurevergaderingen, so the script watches *all* new OCW
committee activities and flags the ones that look relevant to your keywords.

No paid services and nothing to do with Telegram Wallet -- this only uses
the free Telegram Bot API (https://core.telegram.org/bots/api).

Configuration (all via environment variables / GitHub secrets):
  TELEGRAM_BOT_TOKEN   (required)  Bot token from @BotFather
  TELEGRAM_CHAT_ID     (required)  Your chat id (see README)
  COMMITTEE            (optional)  Committee abbreviation, default "OCW"
  KEYWORDS             (optional)  Comma-separated terms to highlight, e.g.
                                   "iran,studenten,collegegeld,sanctie"
  SOORT_FILTER         (optional)  Comma-separated activity types to keep
                                   (e.g. "procedurevergadering,petitie").
                                   Empty = keep everything.
  STRICT_KEYWORDS      (optional)  "1" to send ONLY keyword matches.
                                   Default off (sends all, flags matches).
  LOOKBACK_HOURS       (optional)  How far back to look on the very first
                                   run, default 72.
  STATE_FILE           (optional)  Path to the state file, default state.json
  MAX_MESSAGES         (optional)  Safety cap per run, default 25.

Run modes:
  python monitor.py            normal run
  python monitor.py --test     send a Telegram test message and exit
  python monitor.py --dry-run  query the API and print, but send nothing
"""

import datetime as dt
import html
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import requests

API_BASE = "https://gegevensmagazijn.tweedekamer.nl/OData/v4/2.0"
COMMITTEE_PAGE = "https://www.tweedekamer.nl/kamerleden_en_commissies/commissies/ocw"
TELEGRAM_API = "https://api.telegram.org"
USER_AGENT = "ocw-petition-monitor/1.0 (+github actions)"
REQUEST_TIMEOUT = 45

# --- config ---
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
COMMITTEE = os.environ.get("COMMITTEE", "OCW").strip()
KEYWORDS = [k.strip().lower() for k in os.environ.get("KEYWORDS", "").split(",") if k.strip()]
SOORT_FILTER = [s.strip().lower() for s in os.environ.get("SOORT_FILTER", "").split(",") if s.strip()]
STRICT_KEYWORDS = os.environ.get("STRICT_KEYWORDS", "").strip() in ("1", "true", "yes")
LOOKBACK_HOURS = int(os.environ.get("LOOKBACK_HOURS", "72"))
STATE_FILE = Path(os.environ.get("STATE_FILE", "state.json"))
MAX_MESSAGES = int(os.environ.get("MAX_MESSAGES", "25"))
SEEN_CAP = 800  # how many recent ids to remember for de-duplication


def log(msg: str) -> None:
    print(f"[{dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log(f"WARN could not read state file ({exc}); starting fresh")
    return {}


def save_state(state: dict) -> None:
    state["seen_ids"] = state.get("seen_ids", [])[-SEEN_CAP:]
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def baseline_since() -> str:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=LOOKBACK_HOURS)
    return since.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Tweede Kamer OData API
# --------------------------------------------------------------------------- #
def build_query(since_iso: str) -> str:
    filter_parts = [
        "Verwijderd eq false",
        f"Voortouwafkorting eq '{COMMITTEE}'",
        f"ApiGewijzigdOp gt {since_iso}",
    ]
    filter_str = " and ".join(filter_parts)
    # Expand related zaken/documents so the notification carries useful context.
    # No $select inside the expand to avoid 400s on unfamiliar field names.
    params = (
        f"$filter={quote(filter_str)}"
        "&$orderby=ApiGewijzigdOp asc"
        "&$top=250"
        "&$expand=Zaak($filter=Verwijderd eq false),"
        "Document($filter=Verwijderd eq false)"
        "&$format=application/json;odata.metadata=none"
    )
    return f"{API_BASE}/Activiteit?{params}"


def fetch_activities(since_iso: str) -> list[dict]:
    url = build_query(since_iso)
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    results: list[dict] = []
    page = 0
    while url and page < 20:
        page += 1
        resp = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        if url:
            time.sleep(1)  # be polite to the API between pages
    return results


# --------------------------------------------------------------------------- #
# Formatting
# --------------------------------------------------------------------------- #
def esc(text) -> str:
    return html.escape(str(text)) if text is not None else ""


def matches_keywords(activity: dict) -> bool:
    if not KEYWORDS:
        return False
    haystack = " ".join(
        str(activity.get(field, "") or "")
        for field in ("Onderwerp", "Soort", "Noot")
    ).lower()
    for zaak in activity.get("Zaak", []) or []:
        haystack += " " + " ".join(
            str(zaak.get(field, "") or "")
            for field in ("Titel", "Onderwerp", "Citeertitel")
        ).lower()
    return any(kw in haystack for kw in KEYWORDS)


def format_date(activity: dict) -> str:
    raw = activity.get("Datum") or ""
    out = raw[:10] if raw else "datum onbekend"
    start = activity.get("Aanvangstijd") or ""
    if start and len(start) >= 16:
        out += f" om {start[11:16]}"
    return out


def format_message(activity: dict, flagged: bool) -> str:
    soort = activity.get("Soort") or "Activiteit"
    onderwerp = activity.get("Onderwerp") or "(geen onderwerp)"
    status = activity.get("Status") or ""
    voortouw = activity.get("Voortouwnaam") or COMMITTEE

    header = "🔔 <b>RELEVANT</b> " if flagged else ""
    lines = [
        f"{header}🏛️ <b>{esc(soort)}</b> — {esc(COMMITTEE)}",
        f"<b>Onderwerp:</b> {esc(onderwerp)}",
        f"<b>Datum:</b> {esc(format_date(activity))}",
    ]
    if status:
        lines.append(f"<b>Status:</b> {esc(status)}")
    lines.append(f"<b>Voortouw:</b> {esc(voortouw)}")

    zaken = activity.get("Zaak", []) or []
    if zaken:
        lines.append("\n<b>Onderwerpen op de agenda:</b>")
        for zaak in zaken[:8]:
            titel = zaak.get("Titel") or zaak.get("Onderwerp") or zaak.get("Citeertitel") or "—"
            nummer = zaak.get("Nummer")
            suffix = f" ({esc(nummer)})" if nummer else ""
            lines.append(f"• {esc(titel)}{suffix}")
        if len(zaken) > 8:
            lines.append(f"… en nog {len(zaken) - 8} meer")

    docs = activity.get("Document", []) or []
    if docs:
        lines.append("\n<b>Documenten:</b>")
        for doc in docs[:8]:
            doc_id = doc.get("Id")
            titel = doc.get("Titel") or doc.get("Onderwerp") or doc.get("Soort") or "Document"
            if doc_id:
                link = f"{API_BASE}/Document({doc_id})/resource"
                lines.append(f'• <a href="{link}">{esc(titel)}</a>')
            else:
                lines.append(f"• {esc(titel)}")
        if len(docs) > 8:
            lines.append(f"… en nog {len(docs) - 8} meer")

    lines.append(f'\n<a href="{COMMITTEE_PAGE}">→ Commissiepagina OCW</a>')
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Telegram
# --------------------------------------------------------------------------- #
def telegram_send(text: str) -> None:
    url = f"{TELEGRAM_API}/bot{BOT_TOKEN}/sendMessage"
    # Telegram hard limit is 4096 chars; trim defensively.
    if len(text) > 4000:
        text = text[:3990] + "\n…"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
    if resp.status_code != 200:
        log(f"ERROR Telegram returned {resp.status_code}: {resp.text[:300]}")
        resp.raise_for_status()


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def require_telegram_config() -> None:
    missing = [name for name, val in
               (("TELEGRAM_BOT_TOKEN", BOT_TOKEN), ("TELEGRAM_CHAT_ID", CHAT_ID))
               if not val]
    if missing:
        log(f"ERROR missing required config: {', '.join(missing)}")
        sys.exit(1)


def run_test() -> None:
    require_telegram_config()
    telegram_send(
        "✅ <b>OCW monitor test</b>\n"
        "If you can read this, your bot token and chat id are correct.\n"
        f"Watching committee: <b>{esc(COMMITTEE)}</b>."
    )
    log("Test message sent.")


def main(dry_run: bool = False) -> None:
    if not dry_run:
        require_telegram_config()

    state = load_state()
    seen_ids = set(state.get("seen_ids", []))
    since = state.get("last_api_gewijzigd") or baseline_since()
    log(f"Checking {COMMITTEE} activities changed after {since}")

    try:
        activities = fetch_activities(since)
    except requests.RequestException as exc:
        log(f"ERROR API request failed: {exc}")
        sys.exit(1)

    log(f"API returned {len(activities)} candidate activities")

    # Newest ApiGewijzigdOp we observe becomes the next baseline.
    max_seen_ts = since
    new_items: list[tuple[dict, bool]] = []

    for act in activities:
        ts = act.get("ApiGewijzigdOp") or ""
        if ts > max_seen_ts:
            max_seen_ts = ts
        act_id = act.get("Id")
        if not act_id or act_id in seen_ids:
            continue
        if SOORT_FILTER:
            soort = (act.get("Soort") or "").lower()
            if not any(s in soort for s in SOORT_FILTER):
                seen_ids.add(act_id)  # mark seen so we don't re-evaluate forever
                continue
        flagged = matches_keywords(act)
        if STRICT_KEYWORDS and not flagged:
            seen_ids.add(act_id)
            continue
        new_items.append((act, flagged))

    log(f"{len(new_items)} new item(s) to report")

    sent = 0
    for act, flagged in new_items:
        if sent >= MAX_MESSAGES:
            log(f"Hit MAX_MESSAGES cap ({MAX_MESSAGES}); remaining items deferred")
            # don't mark the rest as seen, so they go out next run
            break
        msg = format_message(act, flagged)
        if dry_run:
            print("---\n" + msg + "\n")
        else:
            telegram_send(msg)
            time.sleep(0.5)  # stay under Telegram rate limits
        seen_ids.add(act.get("Id"))
        sent += 1

    if not dry_run:
        state["last_api_gewijzigd"] = max_seen_ts
        state["seen_ids"] = sorted(seen_ids)
        save_state(state)
        log(f"State saved. Sent {sent} message(s). Next baseline: {max_seen_ts}")
    else:
        log(f"Dry run complete. Would have sent {sent} message(s).")


if __name__ == "__main__":
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--test":
        run_test()
    elif arg == "--dry-run":
        main(dry_run=True)
    else:
        main()
