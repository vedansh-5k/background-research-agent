# AI Public-Web Background Research Agent

Enter a person's name (plus optional company / location / research scope). The app
searches the live public web, extracts and cross-verifies evidence, and produces a
structured, source-cited report. Nothing about any specific person is hardcoded —
every run is a live search.

## How it works

```
name + company/location
   ↓  15 targeted queries generated (one set per research category,
      each led by your company/location context)
   ↓  live web search, all queries run in parallel  (Tavily → Serper fallback)
   ↓  results ranked: context match → name match → domain authority
   ↓  context matches reserved, then round-robin across categories
   ↓  top pages read and cleaned  (crawl4ai)
   ↓  LLM reasons over that evidence ONLY  (Groq → Gemini fallback)
      · which of the same-named people is the target?
      · one evidence entry per fact, cited by source number
      · conflicts flagged, confidence capped by identity confidence
   ↓  report assembled in code from the evidence list
```

The LLM never answers from its own memory. It only reasons over pages actually
retrieved during that run, and every claim carries the source it came from.

## Prerequisites

- Python 3.10+
- **Tavily** API key (search) — https://tavily.com
- **Groq** API key (LLM) — https://console.groq.com/keys
- Optional but recommended: a **Google AI Studio** key (https://aistudio.google.com/apikey)
  as an automatic fallback when Groq's daily quota runs out

## Install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

The last line installs the browser `crawl4ai` uses to read pages — one-time setup.

## Configure

```powershell
copy .env.example .env
```

Then fill in `TAVILY_API_KEY`, `GROQ_API_KEY`, and (optionally) `GEMINI_API_KEY`.

## Run

```powershell
streamlit run app.py
```

Opens at `http://localhost:8501`.

## Using it well

**Always fill in Company and Location if you know them.** They are not cosmetic —
they are the strongest signal for telling your target apart from everyone else with
the same name. They steer which pages get read, and they lead every category search
("Ayush Bhardwaj **EY** education" rather than "Ayush Bhardwaj education").

Research Type controls which categories are searched. Pick **Custom** to unlock the
free-text box; with any other type, text in that box is ignored (the app tells you).

## Reading the report

- **Identity Resolution** — every distinct person found under that name, never merged
- **The "Who" column** — which candidate each fact belongs to, so a director, an actor
  and an analyst who share a name don't blur into one fictional CV
- **Confidence** — a claim is never more confident than the identity it rests on. An
  official record about *possibly* the right person stays LOW, however authoritative
  the source
- **Source List** — split into sources actually cited vs pages reviewed that yielded
  nothing, so the audit trail is complete without implying false connections
- **Categories With No Findings** — means nothing was found in the sources searched.
  It does **not** mean nothing exists

## Provider fallbacks

Both search and LLM have automatic fallbacks, because free tiers run out mid-demo:

| Layer | Primary | Fallback | Triggers on |
|---|---|---|---|
| Search | Tavily | Serper (if key set) | empty results / rate limit |
| LLM | Groq | Gemini (if key set) | rate limit, daily quota, errors |

A per-minute rate limit is waited out. A per-*day* quota is not — it switches
providers immediately rather than sleeping for 25 minutes.

## Free-tier limits (the real constraint on report depth)

- **Groq free:** 200,000 tokens/day, and ~8,000 tokens per request — input *and*
  reserved output count against it
- **Gemini free:** flagship models cap around 20 requests; `gemini-3.5-flash` has
  more headroom, which is why it's the fallback default

`config/settings.py` computes the evidence budget from what's left after the system
prompt, schema and reserved output, then drops the lowest-ranked sources to fit. If
the log says `Evidence section over budget — dropped lowest-ranked source`, that
ceiling is why reports are shallower than the search results allow. A paid Groq Dev
Tier removes it.

## Project layout

```
app.py                        Streamlit UI
config/settings.py            All limits and keys; token budget is computed here
prompts/research_system_prompt.txt   The evidence-first rules the LLM follows
models/schemas.py             Evidence / identity / conflict shapes
search/                       Tavily, Serper, and the fallback wrapper
extraction/web_extractor.py   URL → clean text (crawl4ai) + disk cache
agents/query_planner.py       Name + context → category-tagged queries
agents/llm_client.py          The ONLY file that talks to an LLM provider
agents/research_agent.py      Orchestrates search → rank → extract → analyse
reporting/report_generator.py Builds the report from the evidence list
```

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Missing configuration" on load | `.env` not created or key missing |
| Report has few findings | Usually the token budget trimming sources — check the log for `over budget`; add Company/Location to sharpen ranking |
| `429 ... tokens per day` | Groq daily quota spent; it falls back to Gemini automatically if that key is set |
| `model ... no longer available` | Provider retired the model; check the error — it usually names the replacement — and update `GEMINI_MODEL` / `GROQ_MODEL` |
| Extraction 0 of N | Re-run `python -m playwright install chromium` |
| Empty LLM response | Reasoning tokens ate the output budget; lower `GROQ_REASONING_EFFORT` or raise `MAX_COMPLETION_TOKENS` |

Every run's full log is in the **Debug log** expander under the report, and also
prints to the terminal.

## Security

`.env` holds live API keys and is git-ignored. Never commit it, and regenerate any
key that has been pasted into a chat, screenshot or shared document.
