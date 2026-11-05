# Mika Job Dashboard

**Career dashboard. From Yale, for Yale, beyond Yale.**
Live: https://mika-jobs.onrender.com

Entry-level jobs, internships, and co-op / in-semester / part-time roles, read every day from companies' own career
sites, plus public Yale career events. Built for students, with international students in mind.

- **Original links only.** Every role comes from the employer's own recruiting system (Greenhouse, Lever, Ashby, Workday
  public APIs), and every card links to the original posting. No job boards and no aggregators.
- **H-1B history.** Employers with certified H-1B LCAs at the U.S. Department of Labor are marked, from public DOL OFLC
  data. That is a filing history, not a promise of sponsorship.
- **Nothing invented.** The chat answers from this site's own data, mostly in code. A model only reads the question,
  and the server drops any role it did not find itself. A role a student's search turns up joins the shared pool only
  after code re-reads it on the company's own page.
- **No accounts.** Interests, majors, saved roles and application tracking stay in the visitor's browser. `/stats`
  shows aggregate counts only.
- **Cheap to run.** The chat uses a free model on OpenRouter. The only paid steps are the interview's profile and the web
  search, and both stop at a daily dollar cap (`ELM_DAILY_DOLLAR_BUDGET`).

## Run it

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python elm/crawl.py          # read every board in elm/data/seeds.json (minutes, network, no model)
.venv/bin/python elm/events.py         # public Yale career events
ELM_DATA_DIR=elm/data .venv/bin/python -m uvicorn --app-dir elm app:app --port 8270
```

Open http://localhost:8270. Without `OPENROUTER_API_KEY` everything works except the optional model steps: the chat
answers in code, and web search is off.

## Checks

`bash check.sh` runs everything offline with no keys. It ends with `RESULT: ALL CHECKS PASSED` and covers:
- syntax and unit tests;
- the page's JavaScript;
- a secret scan;
- a red team of 40 attacks on a throwaway server, using hostile postings, oversized requests, path tricks, privacy checks and prompt injection.

GitHub Actions runs the same script on every push.

## Layout

```
elm/            the app: FastAPI server (app.py), crawl, entry-level filter, chat, interview, pool, stats, page (static/)
elm/data/       seeds.json (company boards), sponsors.json (DOL LCA counts), feeds.json (public calendars)
backend/        shared readers: ATS APIs, Workday, safe page fetch (postings.safe_get), HTML sanitiser, sponsor
                signals, Postgres write-through, public Yale event readers
scripts/        red team and helper tools
.github/        check on every push; the daily crawl that uploads to the live site
```

## Deploying (Render, free plan)

- **Build:** `pip install -r requirements.txt`
- **Start:** `uvicorn --app-dir elm app:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'`
- **Health check:** `/healthz`

Environment variables:

| Variable | What it does |
|---|---|
| `OPENROUTER_API_KEY` | Model key for the optional chat, interview and search steps |
| `ELM_CHAT_MODEL` | A free model, e.g. `nvidia/nemotron-3.5-super-vl:free` |
| `SEARCH_MODEL` | The paid search model, e.g. `openai/gpt-5-nano` |
| `ELM_DAILY_DOLLAR_BUDGET` | Daily cap on paid steps (`0.10`) |
| `ELM_UPLOAD_SECRET` | Long random string that lets the daily crawl upload; the same value goes in the GitHub secret |
| `ELM_DATABASE_URL` | Postgres for the shared pool, usage and stats (optional) |

The daily crawl runs in GitHub Actions. It publishes the day's data as `elm-data.json.gz` on this repository's
`data-latest` release, and the server loads that file whenever its disk is empty: a free instance loses its disk when it
sleeps (`elm/snapshot.py`). It also posts the data to `/api/admin/upload` so the site updates at once. That part needs the
repository secrets `ELM_URL` and `ELM_UPLOAD_SECRET`.

## Sources and licenses

**Data sources:**
- Company roles come from the companies' public ATS APIs.
- H-1B data comes from U.S. DOL OFLC disclosure data, which is public domain.
- Yale events come from public pages whose robots.txt allows reading.
- Some board tokens come from [kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers) (MIT).

**What is never used:**
- Handshake, LinkedIn, Indeed, Glassdoor;
- anything behind a login;
- anything a robots.txt disallows.

**License:**
- The code is under the MIT license (see `LICENSE`).
- Mika's drawings (`frontend/mika/`, `frontend/mika-icon.png`) are not covered by it (see `NOTICE.md`).

A student project, not affiliated with Yale University. Mika for Yale SOM students: https://mika-som.onrender.com
