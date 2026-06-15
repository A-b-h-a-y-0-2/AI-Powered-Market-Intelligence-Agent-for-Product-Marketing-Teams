### Problem Statement: AI-Powered Market Intelligence Agent for Product Marketing Teams

#### Background

Modern startups ship features faster than ever, but product, sales, marketing, and leadership teams struggle to keep up with:

* Competitor launches
* Pricing changes
* New partnerships
* Industry news
* Customer sentiment shifts
* Market trends

Information is scattered across websites, blogs, LinkedIn posts, changelogs, press releases, funding announcements, and social media.

Today, employees manually search for information, monitor competitors, read newsletters, and compile reports. This process is slow, repetitive, and often results in missed opportunities.

---

## Objective

Build a conversational AI Market Intelligence Agent that continuously monitors a company, its competitors, and the broader market landscape.

The system should collect information automatically, transform it into structured knowledge, and provide stakeholder-specific insights through a conversational interface.

---

## Inputs

### Company Information

* Company Name
* Company Website
* Product Description

Example:

```text
Company: Shiplog
Website: shiplog.ai
Description:
Agentic GTM platform helping companies transform product updates into revenue.
```

---

### Competitors

```text
Competitor A
Competitor B
Competitor C
...
```

Each competitor may have:

* Website
* Blog
* Changelog
* LinkedIn
* Twitter/X
* Product announcements

---

### Stakeholder Profiles

Different users care about different information.

Example:

```text
CEO
Sales Team
Marketing Team
Product Team
Customer Success
```

---

## Core Requirements

### 1. Continuous Research

The system should continuously monitor:

* Company updates
* Competitor updates
* Industry news
* Funding announcements
* Product launches
* Pricing changes
* Partnerships
* Hiring trends

---

### 2. Knowledge Normalization

Raw web content should not be stored directly.

Convert findings into structured events.

Example:

```json
{
  "company": "Competitor A",
  "event_type": "feature_launch",
  "timestamp": "2026-06-15",
  "summary": "Launched AI-powered lead scoring",
  "source": "blog_url"
}
```

Supported event types:

* Feature Launch
* Pricing Change
* Funding Event
* Acquisition
* Partnership
* Hiring Trend
* Product Update
* Market Trend

---

### 3. Stakeholder-Specific Intelligence

The same event should generate different insights for different teams.

Example:

#### Event

Competitor launches AI lead scoring.

#### CEO

Potential market shift toward automated qualification.

#### Sales

New objection likely to appear in sales calls.

#### Marketing

Messaging should be updated to address competitor claims.

#### Product

Evaluate whether similar functionality should be prioritized.

---

### 4. Conversational Interface

Users should be able to ask:

```text
What happened this week?

What are our competitors focusing on?

Which competitor is shipping fastest?

How does Competitor A compare to us?

What should the sales team know today?

Show evidence for this insight.
```

The agent should retrieve relevant events and generate grounded responses.

---

### 5. Source Attribution

Every insight must be traceable.

Example:

```text
Competitor A launched a new pricing tier.

Sources:
- Company Blog
- Product Changelog
- LinkedIn Announcement
```

Users must be able to inspect the evidence.

---

## Non-Functional Requirements

### Reliability

* Retry failed crawls
* Handle website failures
* Recover from tool failures

---

### Observability

Track:

* Crawl success rate
* Failed sources
* Extraction quality
* Agent reasoning steps
* Tool execution logs

---

### Scalability

Support:

* Multiple companies
* Multiple competitors
* Multiple stakeholders
* Continuous monitoring

---

### Cost Optimization

* Cache crawled content
* Avoid duplicate research
* Minimize unnecessary LLM calls

---

## Expected Outcome

The final system should function as a continuously updating market intelligence platform where:

* Research happens automatically.
* Market knowledge is stored as structured events.
* Different stakeholders receive relevant insights.
* Users can interact conversationally with the accumulated knowledge.
* Every answer is explainable and backed by sources.

### Key Insight

**The goal is not to build a chatbot that searches the web.**

The goal is to build a system that continuously transforms external market activity into actionable business intelligence, with the chatbot acting as the interface to that intelligence.
