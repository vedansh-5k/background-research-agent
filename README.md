# AI Open-Source Research Agent

An evidence-first research tool with two modes:

| Mode | Input | Output |
|---|---|---|
| **Adverse Media Check (AMC)** | A research brief (free text or JSON) naming a company, its identifiers and what to check | Risk-rated AMC report covering the company and each director, with match classification and full disclaimers |
| **Person Background Research** | A person's name, plus optional company / location / scope | Structured background report separating people who share the same name |

Both produce **source-cited reports in Markdown and MS Word**, built entirely from
evidence retrieved live from the public web. Nothing about any specific person or
company is hardcoded — every run is a live search.

---

## Contents
1. [What it does](#1-what-it-does)
2. [How it works](#2-how-it-works)
3. [Setup](#3-setup)
4. [Using the Adverse Media Check](#4-using-the-adverse-media-check)
5. [Using Person Background Research](#5-using-person-background-research)
6. [Design principles](#6-design-principles)
7. [Configuration](#7-configuration)
8. [Project structure](#8-project-structure)
9. [Known limitations](#9-known-limitations)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What it does

You give it a research brief. It plans the research, searches the live web, reads
the best sources, works out which entity or person the results actually refer to,
and produces a report where **every finding carries its source and a confidence
level**.

The language model never answers from memory. It only reasons over pages
retrieved during that run, and it cites each source by number — the URLs are
resolved in code afterwards, so a citation cannot be fabricated.

### Example — Adverse Media Check

**Input brief:**
> Conduct a structured, open source-based Adverse Media Check for JYOTI STRIPS PRIVATE
> LIMITED CIN: U51101HR2007PTC138314 PAN: AABCJ8090K and all its directors, using
> publicly available credible information, watchoutinvestors.com, RBI defaulter list and
> other credible sources...

**What the system did, unprompted:**
- Extracted the CIN and PAN and searched them directly to confirm the right entity
- Generated `site:watchoutinvestors.com` queries because the brief named that source
- Added Indian corporate registries (zaubacorp, tofler) on its own initiative
- Discovered **6 directors** from public sources and ran a separate check on each
- Produced a risk rating, match classifications, disclaimers and a Word document

No part of that was hardcoded. Change the brief and the research changes.

---

## 2. How it works

```
                      ┌──────────────────────────────┐
   research brief ──▶ │  LLM reads the brief and     │
                      │  writes the research plan    │   ← nothing hardcoded
                      └──────────────┬───────────────┘
                                     ▼
                      search queries (identifiers, named
                      sources, topics from the brief)
                                     ▼
                      ┌──────────────────────────────┐
                      │  Web search — Tavily         │   parallel, with Serper fallback
                      └──────────────┬───────────────┘
                                     ▼
                      rank: identifier match ▸ name match ▸ domain authority
                                     ▼
                      ┌──────────────────────────────┐
                      │  Page extraction — crawl4ai  │   HTML ▸ clean text
                      └──────────────┬───────────────┘
                                     ▼
                      ┌──────────────────────────────┐
                      │  LLM analyses THAT EVIDENCE  │   Groq, with Gemini fallback
                      │  ONLY — confirms the entity, │
                      │  extracts findings, cites by │
                      │  source number               │
                      └──────────────┬───────────────┘
                                     ▼
                      source numbers ▸ real URLs (in code)
                                     ▼
                      ┌──────────────────────────────┐
                      │  Report assembled in Python  │   Markdown + .docx
                      └──────────────────────────────┘
```

**AMC mode runs this twice:** once for the company — which also discovers who the
directors are — then once per director, because you cannot search for directors
until you know their names.

### Where the AI is and isn't used

| Stage | AI involved? |
|---|---|
| Planning the research from the brief | **Yes** (AMC mode) |
| Building queries in person mode | No — rule-based templates |
| Web search | No |
| Ranking and selecting sources | No |
| Reading web pages | No |
| Analysing evidence, confirming identity, extracting findings | **Yes** |
| Resolving citations to URLs | No — done in code, so URLs can't be invented |
| Building report tables | No |
| Writing summary prose | **Yes** |

---

## 3. Setup

### Prerequisites
- Python 3.10 or newer
- A **Tavily** API key — https://tavily.com
- A **Groq** API key — https://console.groq.com/keys
- Recommended: a **Google AI Studio (Gemini)** key — https://aistudio.google.com/apikey
  Used as an automatic fallback, and it reads far more evidence per report.

### Install

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

The last command installs the browser crawl4ai drives. It is a one-time step.

### Configure

```powershell
copy .env.example .env
```

Open `.env` and fill in your keys. At minimum: `TAVILY_API_KEY` and one LLM key.

### Run

```powershell
streamlit run app.py
```

Opens at `http://localhost:8501`.

---

## 4. Using the Adverse Media Check

Select **Adverse Media Check** and paste your brief. Free text or JSON both work.

**Include identifiers wherever you have them** (CIN, PAN, GST, registration
number). An identifier match is the strongest possible confirmation that a source
refers to your subject and not to a similarly-named company — the ranking weights
it above everything else.

**Name specific sources if you want them searched.** Writing "check
watchoutinvestors.com and the RBI defaulter list" produces site-targeted queries
for exactly those.

### Reading the report

| Section | What it tells you |
|---|---|
| **Subject** | Whether the entity was confirmed in sources, and what they establish about it |
| **Scope of Search** | Topics investigated, sources requested, number of queries run |
| **Adverse Media — Company** | Each finding: issue type, what the source reports, match classification, severity |
| **Adverse Media — Directors** | The same, per director discovered |
| **Overall Risk Impression** | LOW / MEDIUM / HIGH with the reasoning |
| **Sources** | Sources supporting findings, listed separately from pages reviewed that produced nothing |
| **Disclaimers and Limitations** | What could not be checked, plus the search date |

### Match classification

| | Meaning |
|---|---|
| **STRONG** | Identifiers match, or several specifics line up |
| **WEAK** | The name matches but nothing else confirms the same entity |
| **UNCERTAIN** | Cannot be determined from the sources |

A serious allegation about an **unconfirmed** entity is never classified STRONG,
however credible the source. This is deliberate: attributing another company's
enforcement action to your client is the most damaging error this tool could make.

---

## 5. Using Person Background Research

Enter a name, and **fill in Company and Location whenever you know them.** They
are not cosmetic — they are the strongest signal for distinguishing your subject
from everyone else with the same name, they steer which pages get read, and they
lead every category search (`"Ayush Bhardwaj EY education"`, not
`"Ayush Bhardwaj education"`).

`Research Type` controls which categories are searched. Choose **Custom** to
unlock the free-text box; with any other type, text there is ignored and the app
tells you so.

In the report, the **"Who" column** shows which identity candidate each fact
belongs to — so a company director, an actor and an analyst who happen to share a
name never blur into one fictional biography.

---

## 6. Design principles

**1. Evidence is the source of truth, not the model.**
The model is never asked "who is this person?" It is asked "here are eight web
pages — what do they say?"

**2. Citations cannot be fabricated.**
The model cites a source *number*. Code maps that number to the real URL. It never
writes a URL, so it cannot invent one.

**3. Same-named entities are never merged.**
Each becomes a separate candidate with its own confidence, and every finding is
tagged with which one it belongs to.

**4. Confidence is capped by identity confidence.**
An authoritative record about *possibly* the right entity stays LOW confidence. A
trustworthy source does not make an uncertain match certain.

**5. "Nothing found" is never reported as "nothing exists".**
The report states what was searched and that nothing was found in those sources.
It never says a subject is clean or has no record — those are different claims,
and the difference matters legally.

**6. Allegation is not guilt.**
Convictions, charges, arrests, investigations, penalties and allegations are
distinguished and attributed: *"According to [source], X was charged with Y"* —
never *"X did Y"*.

---

## 7. Configuration

All settings live in `.env`. The defaults are tuned for free-tier API limits.

| Setting | Default | Purpose |
|---|---|---|
| `LLM_PROVIDER` | `groq` | Primary LLM — `groq` or `gemini`. The other becomes the automatic fallback. |
| `SEARCH_PROVIDER` | `tavily` | Primary search — `tavily` or `serper` |
| `MAX_QUERIES` | 15 | Search queries per run |
| `MAX_SOURCES_TO_EXTRACT` | 8 | Pages opened and read per run |
| `MAX_DIRECTORS_TO_CHECK` | 6 | Directors individually researched in AMC mode |
| `PROVIDER_TOKEN_BUDGET` | 8000 | Groq's per-request ceiling |
| `GEMINI_TOKEN_BUDGET` | 30000 | Gemini's larger allowance |
| `MAX_CHARS_PER_SNIPPET` | 3000 | Per-source search content sent to the model |

### Provider fallbacks

Free tiers run out mid-demo, so both layers fail over automatically:

| Layer | Primary | Fallback | Triggers on |
|---|---|---|---|
| Search | Tavily | Serper | empty results, rate limit |
| LLM | Groq | Gemini | rate limit, daily quota, schema failure |

A per-minute rate limit is waited out. A per-**day** quota is not — it switches
provider immediately rather than sleeping for 25 minutes.

### Which provider to use

**Gemini reads roughly four times more evidence per report** (40,000 characters
versus Groq's 9,444), because Groq's free tier counts input *and* reserved output
against one 8,000-token ceiling. Groq is faster and has a generous daily token
allowance. Use Groq for iteration, Gemini when report depth matters.

---

## 8. Project structure

```
app.py                          Streamlit UI — both modes, Word/Markdown download
config/settings.py              All limits and keys; token budget computed here

agents/
  brief_parser.py               LLM reads a brief -> research plan (AMC mode)
  amc_agent.py                  AMC orchestrator: company, then each director
  research_agent.py             Person-research orchestrator; shared search/rank/extract
  query_planner.py              Rule-based query templates (person mode)
  llm_client.py                 The ONLY file that talks to an LLM provider

search/
  base.py, tavily_provider.py, serper_provider.py, fallback_provider.py

extraction/web_extractor.py     URL -> clean text (crawl4ai) with disk caching

models/
  schemas.py                    Person-research data shapes
  amc_schemas.py                Research plan, adverse findings, AMC analysis

prompts/research_system_prompt.txt   Evidence-first rules for person mode

reporting/
  report_generator.py           Person report
  amc_report.py                 AMC report
  word_export.py                Markdown -> .docx

utils/                          Run logging, disk cache
```

### Third-party code

Only **crawl4ai** is a dependency of the research logic. It is a library built for
this purpose: give it a URL, it drives a real browser and returns clean text.

Other open-source projects were studied and their patterns reimplemented here
rather than imported, because they are complete applications rather than
libraries: entity disambiguation and conflict resolution (Popsonn's adverse-media
screening system), source credibility scoring and failure resilience (tarun7r's
deep-research-agent), multi-source citation aggregation (gpt-researcher), and
provider-agnostic design (LangChain's open_deep_research). Perplexica was
evaluated and rejected as a poor fit — it is a general question-answering search
engine requiring self-hosted infrastructure.

---

## 9. Known limitations

**Open sources only.** This is a first-pass open-source screen, not a substitute
for a paid AML/KYC database such as World-Check or Dow Jones. Paid registries,
subscription databases and login-protected sites are not accessed.

**Login-protected content is not bypassed.** LinkedIn profiles, for example, are
read only through search-engine indexed content, never by circumventing a login.

**Absence of findings is not absence of events.** It means nothing was found in
the sources searched on that date.

**Free-tier budgets constrain depth.** The system regularly finds more credible
sources than it can afford to read; the log records every source dropped. Raising
`GEMINI_TOKEN_BUDGET`, or moving to a paid tier, increases depth directly.

**Small private companies produce thin reports**, simply because little about them
is published. That is a correct result, not a failure.

---

## 10. Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Missing configuration" on load | `.env` not created, or a key is blank |
| Report has few findings | Check the log for `Evidence over budget` — sources were dropped to fit. Switch to Gemini or raise the token budget. |
| `429 ... tokens per day` | Daily quota spent; it falls back to the other provider automatically |
| `model ... no longer available` | The provider retired that model. The error usually names the replacement — update `GEMINI_MODEL` or `GROQ_MODEL`. |
| Extraction reports 0 of N pages | Re-run `python -m playwright install chromium` |
| Empty response from the model | Reasoning tokens consumed the output budget — lower `GROQ_REASONING_EFFORT` or raise `MAX_COMPLETION_TOKENS` |

Every run's full log appears in the **Debug log** expander beneath the report, and
also prints to the terminal.

---

## Security

`.env` holds live API keys and is git-ignored — never commit it. The `cache/`
directory holds scraped page content about real people and companies and is also
git-ignored; clear it before sharing the project. Regenerate any key that has been
pasted into a chat, screenshot or shared document.
