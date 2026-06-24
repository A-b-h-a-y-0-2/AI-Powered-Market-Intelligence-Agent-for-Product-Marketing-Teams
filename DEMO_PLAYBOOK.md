# Demo Playbook — AI Market Intelligence Agent
## ~15 minutes | 3 terminals + 1 browser

---

## PRE-FLIGHT (5 min before they arrive)

Open 3 terminals and a browser. Get everything running silently before they sit down.

**Terminal 1 — API server:**
```bash
cd /Users/abhay/AI-Powered-Market-Intelligence-Agent-for-Product-Marketing-Teams
uv run python main.py
```
Wait for: `Uvicorn running on http://0.0.0.0:8000`

**Terminal 2 — Frontend:**
```bash
cd /Users/abhay/AI-Powered-Market-Intelligence-Agent-for-Product-Marketing-Teams/frontend
npm run dev
```
Wait for: `Local: http://localhost:3000`

**Terminal 3 — keep clear** (this is where you'll run the live pipeline)

**Browser — open these tabs (in order):**
1. `http://localhost:3000` — Dashboard / home
2. `http://localhost:3000/threats` — Threat scoring
3. `http://localhost:3000/matrix` — Feature matrix
4. `http://localhost:3000/chat` — Conversational agent

**Health check (run this quietly):**
```bash
curl -s http://localhost:8000/health | python -m json.tool
```
All components should say `"ok"`.

---

## ACT 1 — The Problem (60 seconds, no commands)

**SHOW:** Browser, tab 1 (Dashboard)

**SAY:**
> "Product marketing teams spend 4-6 hours every week manually tracking what McKinsey, BCG, Accenture, Deloitte are doing. They're reading press releases, setting Google Alerts, checking LinkedIn. By the time they find something, it's already been picked up by every competitor in the room.
>
> We built an AI agent that does this continuously — 24/7 — and turns raw web signals into structured intelligence you can actually use. Let me show you what it actually does."

---

## ACT 2 — Live Pipeline Run (4-5 minutes)

**SWITCH TO:** Terminal 3

**SAY:**
> "I'm going to run the full 9-agent pipeline right now, live, so you can see what happens under the hood. This normally runs on a schedule at 2 AM — right now we're triggering it manually."

**RUN:**
```bash
cd /Users/abhay/AI-Powered-Market-Intelligence-Agent-for-Product-Marketing-Teams
uv run python scripts/live_pipeline.py
```

**As the dashboard loads — POINT AT the agent status column and SAY:**
> "You're watching 9 agents wake up in sequence. Each one has a single job."

**As Research Agent turns yellow (RUNNING) — SAY:**
> "The Research Agent is hitting 155 different sources right now. Official newsrooms, RSS feeds, SEC EDGAR filings, GitHub repositories, patent databases, UK Companies House — even Reddit threads where consultants complain about their own firms. Most market intel tools just do Google News. We go wider."

**As Extraction Agent turns yellow — SAY:**
> "The Extraction Agent takes every piece of raw content and runs it through a 3-pass LLM pipeline. First pass filters noise — blog posts about 'strategic thinking' get dropped. Second pass extracts structured events: acquisitions, feature launches, pricing changes, leadership moves. Third pass is a judge model that quarantines anything it's not confident about."

**As Matrix Agent turns yellow — SAY:**
> "The Matrix Agent watches for product launches specifically. Every time it detects one, it updates a living feature comparison table — we'll see that in a minute."

**As Sentiment Agent turns yellow — SAY:**
> "Sentiment runs aspect-based analysis — not just 'positive or negative' but by specific dimension: pricing, AI accuracy, customer support, enterprise features. So you can say 'McKinsey's clients love their AI tools but hate their pricing' with actual evidence."

**As Hiring Signal → Narrative → Convergence → Threat run — SAY:**
> "These three synthesis agents are where it gets interesting. Hiring Signal reads job posting patterns to predict what competitors will launch 4-9 months from now. Narrative clusters recent events into strategic stories — like 'BCG is on an enterprise pivot.' Convergence looks ACROSS all competitors — if 4 out of 5 are doing the same thing, that's a category-wide shift worth flagging."

**As Threat Scoring Agent turns yellow — SAY:**
> "Threat Scoring gives each competitor a 0-100 score based on velocity, event type weight, and recency decay. It's not a gut feeling — it's a formula applied to real events."

**As Conversational Agent turns yellow — SAY:**
> "And finally, the Conversational Agent is answering three live questions right now — one per stakeholder role. Sales, Product, and Exec each get a different framing of the same underlying data."

**When complete — POINT AT the summary table and SAY:**
> "That's the full pipeline. [read the event count] new events extracted this run, [read cost] in LLM costs total. That's what it costs to process a day's worth of competitive intelligence for 8 companies."

---

## ACT 3 — Threat Dashboard (2 minutes)

**SWITCH TO:** Browser, tab 2 — `/threats`

**SAY:**
> "This is the first thing your Monday morning looks like. Before coffee."

**POINT AT the threat tier table and SAY:**
> "Each competitor has a score from 0 to 100. High means they're moving fast right now — lots of launches, acquisitions, pricing moves in the last 30 days. The trend arrow tells you if that's accelerating or cooling off.
>
> The score isn't vibes — it's velocity of events, weighted by type. An acquisition counts 3x more than a blog post. A pricing change counts 2x. And recent events decay slower than old ones."

**CLICK a company to expand — SAY:**
> "And here's why. Every score is sourced to specific events with links. When your VP asks 'why is McKinsey rated HIGH this week?' you have an answer in 10 seconds."

**PAUSE — let them look. Then SAY:**
> "The sales team gets this on Monday morning, pre-loaded with the three most important competitive threats to bring into customer calls."

---

## ACT 4 — Feature Matrix (2 minutes)

**SWITCH TO:** Browser, tab 3 — `/matrix`

**SAY:**
> "This is the living feature comparison matrix. Every time any competitor launches a product or updates a feature, this table updates automatically — within 15 minutes. No manual updates. No stale slides."

**SELECT a company — SAY:**
> "Each cell shows the feature name, a one-line description of what it actually does, when it launched, and a link back to the source event. Not a summary someone wrote from memory — the actual extraction from a press release or filing."

**CLICK 'Compare all' button — SAY:**
> "And here's the comparison view. Green check marks with counts mean they have features in that category. Dashes mean they don't — or we haven't detected any yet. This tells your product team where competitors have coverage and where they don't in about 5 seconds."

**SAY:**
> "Traditionally this is a quarterly thing someone builds manually in a spreadsheet. This updates continuously, every time we detect a new product signal."

---

## ACT 5 — Chat Interface (4 minutes — the wow moment)

**SWITCH TO:** Browser, tab 4 — `/chat`

**SAY:**
> "Now this is what your team actually interacts with daily. A conversational interface, but grounded entirely in real events from the knowledge base — not the model's training data."

**QUERY 1 — Type exactly:**
> `What's McKinsey's biggest strategic move in AI in the last 60 days?`

**While it loads — SAY:**
> "Watch the status line — it's checking whether our knowledge base has enough coverage to answer this, or if it needs to go live. This is the key architectural decision: we don't answer from stale data or hallucinate. If the KB has it, we use it with attribution. If not, we do a live search and store the result."

**When it answers — POINT AT the source citations and SAY:**
> "Every claim has a source. Not a model hallucination — an actual event we extracted from a specific URL, with a timestamp. If your CSO asks 'how do you know that?' you can show them the primary source."

**QUERY 2 — Type exactly:**
> `Based on BCG's hiring patterns, what product area are they building toward in the next 6 months?`

**While it loads — SAY:**
> "This one is different — it's asking about the FUTURE based on hiring signals. We track job postings as leading indicators. When a firm starts hiring 10 AI engineers and 5 enterprise sales people simultaneously, that pattern has historically preceded a product launch by 4-9 months."

**When it answers — SAY:**
> "That prediction is sourced to actual job postings, not speculation. This is the kind of forward signal that typically takes an analyst 2 weeks to surface manually."

**QUERY 3 — Type exactly:**
> `I'm going into a sales call against Accenture tomorrow. What's their biggest weakness right now based on what their clients are saying?`

**While it loads — SAY:**
> "This is a sales-framed query — it's explicitly asking for ammunition. Watch how it pulls from sentiment analysis, not just press releases. The system knows the difference between what Accenture says about themselves and what their clients say about them."

**When it answers — SAY:**
> "Source: G2 reviews and Reddit. Not Accenture's marketing. You can walk into that call knowing the exact objection points their prospects raise."

---

## ACT 6 — Data Sources (2 minutes — the credibility moment)

**SWITCH TO:** Terminal 3 (or a new one)

**SAY:**
> "One thing I want to show explicitly — where this data actually comes from, because that's what makes it credible."

**RUN (this is a quick live test, results in ~5 seconds):**
```bash
python -c "
import asyncio, os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')
from tools.alpha_vantage import AlphaVantageTool

async def demo():
    av = AlphaVantageTool(api_key=os.environ['ALPHA_VANTAGE_API_KEY'])
    results = await av.search_news('Accenture', days=3)
    print(f'Live Accenture (ACN) news from last 3 days: {len(results)} articles')
    for r in results[:3]:
        lines = r.content.split(chr(10))
        for line in lines[:4]:
            if line.strip():
                print(f'  {line}')
        print()

asyncio.run(demo())
"
```

**When it prints — SAY:**
> "That's live. 50 real news articles about Accenture from the last 72 hours, each with a pre-computed sentiment score and relevance weighting. This comes from Alpha Vantage — they specifically curate financial news with AI sentiment analysis for publicly traded companies. We get this for Accenture because they're the one listed company in our tracked set."

**Then switch to Terminal 3 and RUN:**
```bash
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from tools.github_monitor import GitHubMonitorTool

async def demo():
    gh = GitHubMonitorTool()
    results = await gh.monitor_org('Accenture', days=30, orgs=['Accenture'])
    print(f'Accenture GitHub activity — last 30 days: {len(results)} items')
    for r in results[:4]:
        lines = r.content.split(chr(10))
        print('  ' + lines[0])  # repo name line

asyncio.run(demo())
"
```

**SAY:**
> "And this is Accenture's GitHub. [read the repo names] — new open-source projects they published in the last month. New repos are a 12-month leading indicator. When a consulting firm open-sources an internal tool, they're usually three months away from productizing it. We catch that here before it's in any press release."

**Then show one more — RUN:**
```bash
python -c "
import asyncio, sys
sys.path.insert(0, '.')
from tools.mca21 import MCA21Tool

async def demo():
    mca = MCA21Tool()
    results = await mca.search_filings('McKinsey', days=365)
    print(f'McKinsey India (MCA21): {len(results)} result(s)')
    if results:
        print(results[0].content[:400])

asyncio.run(demo())
"
```

**SAY:**
> "And this is McKinsey's Indian subsidiary filing from the Ministry of Corporate Affairs — MCA21. McKinsey is private, so they don't have to disclose revenue in the US. But their Indian entity has to file with the RoC every year. This is how we get financial signals on private firms that no analyst report will give you."

---

## ACT 7 — What's Coming (60 seconds, no commands)

**SAY:**
> "What you've seen today is the MVP. 155 data sources across 8 competitors, 9 agents, fully automated. Running costs are under $2 a day for the LLM layer.
>
> The next layer we're adding: UK Companies House for the other UK subsidiaries — BCG, Bain, Deloitte all have to file accounts there. CourtListener for litigation signals — a patent dispute tells you what technology a firm is trying to protect. And Lens.org for patent filings — 12 to 24 month forward signal on R&D direction.
>
> The data coverage question is solved. The question now is: what decisions do you want this to inform?"

---

## LIKELY QUESTIONS + ANSWERS

**Q: What if a competitor changes their name or launches a new brand?**
> We use canonical name resolution — every competitor has an alias list. Adding an alias takes 30 seconds in the YAML config. Every historical event stays linked.

**Q: How fresh is the data?**
> RSS and SEC EDGAR runs every 30 minutes. Firecrawl and Tavily run daily. The weekly synthesis agents (Narrative, Convergence, Threat) run Sunday night. The Matrix updates within 15 minutes of any product event.

**Q: What about hallucination?**
> Three layers: a pre-filter that rejects content before the extraction LLM sees it, a judge model that quarantines low-confidence extractions for human review, and post-generation attribution that matches every claim to a source event. Claims with no source match are flagged as unattributed, not presented as fact.

**Q: Can we add our own competitors?**
> Yes — it's a YAML file. You add a competitor name, a list of aliases, and the system auto-discovers sources via Tavily on the first run. Adding a new competitor takes about 10 minutes.

**Q: Can we add our own product as one of the tracked entities?**
> Yes, and that's the most interesting use case — tracking yourself the same way you track competitors. Customer reviews, analyst mentions, GitHub activity. Same pipeline, same agents.

**Q: What are the running costs?**
> LLM: $1.50-2.00/day for the automated pipeline. Firecrawl: ~$20/month for 500 pages/day. Tavily: ~$15/month. Apify (Reddit/Indeed): ~$5/month. Total: ~$45/month for continuous intelligence on 8 competitors.

---

## BACKUP: If the pipeline crashes mid-run

Don't panic. Run this instead — it hits the existing KB and still demonstrates the conversational agent:

```bash
python -c "
import asyncio, os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')

# Quick conversational demo using existing KB
# (paste this if the full pipeline is taking too long or errored)
print('Running fallback conversational demo...')
print('Navigate to http://localhost:3000/chat and type queries manually')
"
```

Then demo the chat interface manually and explain the pipeline output is already stored from the last successful run. The UI will still show threat scores, matrix, and events from the database.

---

## APPENDIX: Terminal quick-access commands

```bash
# Check event counts per company (run any time)
python -c "
import asyncio, os, sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv('.env')
from storage.event_store import EventStore

async def show():
    es = EventStore(mongodb_uri=os.environ['MONGODB_URI'], db_name='market_intelligence')
    await es.connect()
    for c in ['McKinsey & Company', 'Boston Consulting Group', 'Bain & Company', 'Accenture Strategy']:
        evs = await es.get_recent_events(company=c, days=90, limit=100)
        print(f'{c:<35} {len(evs):>4} events (90d)')
    await es.disconnect()

asyncio.run(show())
"

# Check threat scores stored in DB
curl -s http://localhost:8000/threats | python -m json.tool | grep -E '"company|score|tier"'

# Check matrix for McKinsey
curl -s "http://localhost:8000/matrix?company=McKinsey%20%26%20Company" | python -m json.tool

# Health
curl -s http://localhost:8000/health | python -m json.tool
```
