# OCW Petition Monitor

Watches the **vaste commissie voor Onderwijs, Cultuur en Wetenschap (OCW)** of the
Tweede Kamer and sends you a **Telegram** message whenever a new committee activity
appears — procedurevergaderingen, petitie-activiteiten, commissiedebatten,
besluitenlijsten and related documents. No more refreshing the page by hand.

It runs entirely on **free** infrastructure:

- **Tweede Kamer Open Data API** (official, open, no key) for the data.
- **Telegram Bot API** (free) for the notifications. This is *not* Telegram Wallet
  and has no payment/crypto component — it works fine in the EU.
- **GitHub Actions** (free for this kind of light scheduled job) as the runner, so
  you don't need a server running at home.

---

## How it works

Every 3 hours, GitHub Actions runs `monitor.py`, which asks the API:

> *Give me all OCW committee activities whose record changed since my last check.*

New items are pushed to your Telegram chat. A small `state.json` (committed back to
the repo by the workflow) remembers what you've already been told, so you never get
duplicates. Items matching your keywords (Iran, studenten, collegegeld, …) get a 🔔
**RELEVANT** flag so your petition follow-up jumps out.

---

## One-time setup (about 10 minutes)

### 1. Create your Telegram bot
1. Open Telegram and message **@BotFather**.
2. Send `/newbot`, pick a name and a username.
3. BotFather replies with a **token** like `8123456789:AAH...`. Keep it — that's your
   `TELEGRAM_BOT_TOKEN`.

### 2. Get your chat id
1. Send any message (e.g. "hi") to your new bot in Telegram. **This step matters** —
   the bot can't message you until you've messaged it first.
2. Easiest way to find your id: message **@userinfobot** in Telegram; it replies with
   your numeric id. That's your `TELEGRAM_CHAT_ID`.
   *(Alternative: open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a
   browser after messaging your bot, and read `result[].message.chat.id`.)*

### 3. Put the code on GitHub
1. Create a new repository (private is fine).
2. Upload these files, keeping the folder layout:
   ```
   monitor.py
   requirements.txt
   state.json
   .github/workflows/monitor.yml
   ```

### 4. Add your secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**.
Add two:
- `TELEGRAM_BOT_TOKEN` → the token from step 1
- `TELEGRAM_CHAT_ID` → the id from step 2

### 5. Turn it on and test
1. Go to the **Actions** tab; if prompted, enable workflows.
2. Open **OCW Petition Monitor → Run workflow** to trigger it manually.
3. The first run looks back `LOOKBACK_HOURS` (default 72h) and messages you the recent
   OCW activity, then only sends genuinely new items after that.

That's it. From now on it checks automatically every 3 hours.

---

## Verifying your Telegram setup quickly

Before relying on the Action, you can confirm the token + chat id work. With Python
installed locally:

```bash
pip install -r requirements.txt
export TELEGRAM_BOT_TOKEN="...."
export TELEGRAM_CHAT_ID="...."
python monitor.py --test       # sends a one-line test message to your chat
python monitor.py --dry-run    # queries the API and prints, but sends nothing
```

---

## Tuning it

All settings live in the `env:` block of `.github/workflows/monitor.yml`:

| Setting | What it does |
| --- | --- |
| `KEYWORDS` | Comma-separated terms that get a 🔔 RELEVANT flag. Broad is good. |
| `SOORT_FILTER` | Limit to certain activity types. Delete the line to receive **everything** OCW does. |
| `STRICT_KEYWORDS` | Set to `"1"` to receive **only** keyword matches (quieter, but can miss a generically-titled procedurevergadering where your petition is buried in the agenda). Off by default — recommended off for petition follow-up. |
| `LOOKBACK_HOURS` | First-run look-back window. |
| `COMMITTEE` | Committee abbreviation. `OCW` here; change to watch a different one. |

Change the schedule by editing the `cron` line (it's in UTC).

---

## Good to know

- **Near-time, not instant.** The API is "neartime": it can take a little while after a
  meeting or decision before the griffie's update appears. A 3-hour cadence is plenty
  for committee follow-up.
- **Scheduled runs can drift.** GitHub sometimes delays cron jobs under load, and it
  **pauses scheduled workflows after ~60 days of repo inactivity** — a manual run or any
  commit wakes it back up.
- **Where your petition shows up.** Incoming petitions are handled in the committee's
  *procedurevergadering*; the decision lands in that meeting's *besluitenlijst*. Those
  are exactly the items this watches, and the besluitenlijst PDF arrives as a tappable
  download link in the message.
- **Want email or a second channel too?** The `telegram_send()` function is the only
  place that talks to Telegram — easy to add a parallel email/Discord call later.
