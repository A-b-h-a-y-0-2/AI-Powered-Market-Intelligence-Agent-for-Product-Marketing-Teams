# CLAUDE.md — AI Agent Operating Manual

> **This file governs how you think, navigate, and act in this repository.**
> Read it fully before writing a single line of code or opening a single file.

---

## PRIME DIRECTIVE

This repository is **fully indexed via GitNexus**.
A graph database (Neo4j/FalkorDB) contains the complete intelligence layer:
symbols, call graphs, dependency edges, file relationships, and semantic tags.

**Your default mode is QUERY, not READ.**

Before opening any file, ask yourself:
> *Can GitNexus answer this faster and cheaper?*

The answer is almost always yes.
Fall back to direct file reads only when you need raw content
(e.g. reading a specific function body after GitNexus told you where it lives).

---

## TOOL PRIORITY ORDER

When performing any task, follow this hierarchy strictly:

```
1. GitNexus MCP query        ← always try first
2. Targeted file read        ← only the specific file/lines GitNexus identified
3. bash grep/find            ← only when GitNexus + file read both fall short
4. Full file scan            ← last resort, flag it when you do this
```

Never do a broad `cat` or full directory scan when a GitNexus query
can give you the answer in one call.

---

## GITNEXUS TOOL REFERENCE

> ⚠️ **Replace the tool names below with your actual GitNexus MCP tool names.**
> The categories and intent are correct — map them to your real API.

### Graph Queries

| Intent | GitNexus Tool | Example Usage |
|--------|--------------|---------------|
| Find a symbol (function/class/variable) | `gitnexus_find_symbol` | `gitnexus_find_symbol("OrderService")` |
| Find all usages of a symbol | `gitnexus_find_usages` | `gitnexus_find_usages("process_payment")` |
| Get call graph for a function | `gitnexus_call_graph` | `gitnexus_call_graph("handle_request", depth=3)` |
| Find all imports of a module | `gitnexus_find_importers` | `gitnexus_find_importers("utils/auth")` |
| Get all exports from a file | `gitnexus_file_exports` | `gitnexus_file_exports("src/services/order.py")` |
| Find files by semantic tag/topic | `gitnexus_semantic_search` | `gitnexus_semantic_search("payment processing")` |
| Get dependency graph of a file | `gitnexus_file_deps` | `gitnexus_file_deps("src/api/routes.py")` |
| Trace data flow between two points | `gitnexus_trace_flow` | `gitnexus_trace_flow("UserInput", "Database")` |
| Find all implementations of an interface | `gitnexus_implementations` | `gitnexus_implementations("BaseRepository")` |
| Get change impact of editing a file | `gitnexus_impact_analysis` | `gitnexus_impact_analysis("src/models/user.py")` |
| List all entry points | `gitnexus_entry_points` | `gitnexus_entry_points()` |
| Get repo-level summary | `gitnexus_repo_summary` | `gitnexus_repo_summary()` |
| Find all tests for a module | `gitnexus_find_tests` | `gitnexus_find_tests("src/services/order.py")` |
| Get all environment variable usages | `gitnexus_env_vars` | `gitnexus_env_vars()` |
| Get schema/model definitions | `gitnexus_data_models` | `gitnexus_data_models()` |

---

## MANDATORY STARTUP SEQUENCE

**Run this at the start of EVERY new task — no exceptions.**

```
STEP 1  →  gitnexus_repo_summary()
           Understand what this repo is before touching anything.

STEP 2  →  gitnexus_entry_points()
           Know where execution begins.

STEP 3  →  gitnexus_semantic_search("<task keywords>")
           Find the relevant slice of the codebase for THIS task.

STEP 4  →  Read only the files GitNexus identified as relevant.
```

Do NOT skip to Step 4 directly. The graph query in Step 3 prevents
you from reading the wrong files and wasting context window.

---

## TASK PLAYBOOKS

Use the right playbook for each task type.
These are not optional — they define the correct sequence of operations.

---

### PLAYBOOK: Understanding the Codebase

```
1.  gitnexus_repo_summary()
    → Get: language, architecture pattern, top-level modules, line count

2.  gitnexus_entry_points()
    → Get: all mains, CLI handlers, server starts, Lambda handlers

3.  gitnexus_data_models()
    → Get: all entities, schemas, relationships

4.  gitnexus_env_vars()
    → Get: all required environment variables and their locations

5.  Read README.md and top-level config files (package.json / pyproject.toml / etc.)
    → Only AFTER steps 1-4 give you the map

6.  For each major module identified in step 1:
    gitnexus_file_exports("<module_path>")
    → Understand what each module exposes publicly
```

---

### PLAYBOOK: Adding a New Feature

```
1.  gitnexus_semantic_search("<feature domain>")
    → Find existing code in the same domain

2.  gitnexus_find_symbol("<related class or function>")
    → Find where to anchor the new feature

3.  gitnexus_implementations("<relevant interface or base class>")
    → Understand the pattern to follow

4.  Read the 1-3 most relevant existing implementations
    → Mirror their pattern exactly

5.  gitnexus_find_tests("<anchor file>")
    → Find the test pattern for this module

6.  Write feature code → Write tests → Run tests

7.  gitnexus_impact_analysis("<file you modified>")
    → Check what else might be affected before committing
```

---

### PLAYBOOK: Fixing a Bug

```
1.  gitnexus_semantic_search("<error message or symptom>")
    → Find likely files

2.  gitnexus_find_symbol("<function or class in stack trace>")
    → Pin the exact location

3.  gitnexus_call_graph("<suspect function>", depth=2)
    → Understand what calls this and what it calls

4.  gitnexus_find_usages("<suspect function>")
    → Find all callers — the bug may need to be fixed in the caller, not the function

5.  Read the specific function body (targeted read, not full file)

6.  Fix → Run tests for this module → Run full test suite

7.  gitnexus_impact_analysis("<file you changed>")
    → Confirm the fix doesn't silently break something else
```

---

### PLAYBOOK: Refactoring

```
1.  gitnexus_find_usages("<symbol to refactor>")
    → Get the FULL list of call sites before touching anything

2.  gitnexus_impact_analysis("<file containing symbol>")
    → Know the blast radius

3.  gitnexus_find_tests("<file containing symbol>")
    → Run these tests to establish the baseline

4.  Make changes in dependency order:
    → Deepest dependency first, consumers last

5.  After each file change:
    gitnexus_find_usages("<changed symbol>")
    → Confirm all usages are updated

6.  Run tests. Fix regressions before moving to the next file.
```

---

### PLAYBOOK: Code Review / PR Understanding

```
1.  For each file in the diff:
    gitnexus_impact_analysis("<changed file>")
    → Surface non-obvious ripple effects

2.  gitnexus_find_usages("<any renamed/changed symbol>")
    → Verify no call sites were missed

3.  gitnexus_call_graph("<changed function>", depth=2)
    → Check if the change alters any important execution path

4.  gitnexus_find_tests("<changed file>")
    → Verify tests exist and were updated

5.  Flag anything GitNexus reveals that the diff doesn't show.
```

---

### PLAYBOOK: Tracing a Request / Execution Flow

```
1.  gitnexus_entry_points()
    → Identify the right entry point for this flow

2.  gitnexus_call_graph("<entry function>", depth=5)
    → Get the full execution chain

3.  gitnexus_trace_flow("<input source>", "<output destination>")
    → Trace data transformation end-to-end

4.  For each layer in the chain, read only the function bodies
    that are unclear from the graph alone.

5.  Produce a written trace: Function A → B → C → D
    with a one-line description of what each step does.
```

---

### PLAYBOOK: Understanding Data Models

```
1.  gitnexus_data_models()
    → Get all entities and their fields

2.  For each entity: gitnexus_find_usages("<ModelName>")
    → Understand where it's created, read, updated, deleted (CRUD map)

3.  gitnexus_trace_flow("<ModelName>", "database")
    → Trace persistence path

4.  Read migration files / schema files only for column-level detail
    that the graph doesn't expose.
```

---

## CONTEXT WINDOW RULES

Treat your context window as a scarce resource.
GitNexus exists specifically to protect it.

**DO:**
- Query GitNexus first to identify the exact 2-5 files relevant to the task
- Read only the relevant functions within those files, not the whole file
- Use `view_range` when reading files to fetch specific line ranges
- Keep your working set to the minimum needed to complete the task

**DO NOT:**
- Open files "just to see what's in them" — query first
- Read config files, READMEs, or unrelated modules unless the task requires it
- Run broad `find` or `grep` commands across the repo — GitNexus does this better
- Re-read files you've already read in this session

---

## CODE CONVENTIONS

> GitNexus will surface the actual patterns in use.
> These rules govern how YOU write code in this repo.

### General Rules

- **Mirror existing patterns.** Before writing anything, use
  `gitnexus_implementations()` to find the closest existing example.
  Match its structure, naming, and error handling style exactly.

- **No orphan code.** Every new function, class, or module must be
  discoverable via GitNexus. If you add something, it must be:
  - Exported from its module (if public)
  - Imported where it's used (no dead code)
  - Tested (no untested new logic)

- **No inline magic values.** Config goes in config files.
  Secrets go in environment variables (flag their addition in the task summary).

- **Touch the minimum.** Only modify files required for the task.
  If you find yourself editing 5+ files for a simple change,
  stop and re-read the task — you're probably in the wrong place.

### Naming

Follow whatever `gitnexus_semantic_search()` reveals is already in use.
Do not introduce new naming patterns without flagging it.

### Error Handling

Use `gitnexus_semantic_search("error handling")` to find the existing
error strategy. Do not invent a new one.

---

## IMPACT ANALYSIS — MANDATORY BEFORE EVERY COMMIT

Run this before marking any task complete:

```
gitnexus_impact_analysis("<every file you modified>")
```

Report the findings in your task summary:
- Which downstream files are affected
- Whether tests cover the affected paths
- Whether any API contracts changed

If impact analysis reveals risk, flag it. Do not suppress warnings.

---

## TEST REQUIREMENTS

Every code change must have test coverage.

```
1.  gitnexus_find_tests("<file you changed>")
    → Find existing tests

2.  Run them before your change to establish baseline

3.  Write new tests if:
    - New functions were added (unit test each one)
    - New API routes were added (integration test)
    - A bug was fixed (regression test for the exact bug)

4.  Run the full test suite before declaring the task done

5.  Never skip tests because "it's a small change"
```

---

## ENVIRONMENT & SECRETS

```
1.  gitnexus_env_vars()
    → Always run this first when environment-related issues arise

2.  Never hardcode secrets, API keys, or credentials
3.  Never log secret values even in debug statements
4.  If a new env var is required, add it to:
    - .env.example (with a placeholder value and comment)
    - This document under the Environment section (see below)
    - Any deployment/CI config that provisions it
```

### Known Environment Variables

> Run `gitnexus_env_vars()` for the live list.
> Manually maintain critical ones here for quick reference:

| Variable | Purpose | Required | Default |
|----------|---------|----------|---------|
| *(populate from gitnexus_env_vars output)* | | | |

---

## WHAT TO DO WHEN GITNEXUS CAN'T ANSWER

If a GitNexus query returns empty or unhelpful results:

1. Try a rephrased semantic search with different keywords
2. Try querying a related symbol you DO know exists
3. Fall back to a targeted `grep` with a specific pattern
4. Read only the most likely file — not the whole directory
5. **Flag in your response** that you fell back to direct file reading
   and why GitNexus didn't cover it. This helps the team improve the index.

---

## RESPONSE FORMAT FOR EVERY TASK

Structure your output as follows — no exceptions:

```
### Task: <what you were asked to do>

**GitNexus Queries Run:**
- gitnexus_X("...") → <what it returned in one line>
- gitnexus_Y("...") → <what it returned in one line>

**Files Read:**
- <file path> (lines X–Y) → <why>

**Changes Made:**
- <file path>: <what changed and why>

**Impact Analysis:**
- <findings from gitnexus_impact_analysis>

**Tests:**
- Existing tests run: <pass/fail>
- New tests written: <yes/no, file path>

**Open Questions / Flags:**
- <anything ambiguous, risky, or requiring human review>
```

---

## THINGS YOU MUST NEVER DO

- Never modify files outside the task scope without explicit instruction
- Never delete files — flag them for deletion instead
- Never commit secrets or credentials
- Never silently skip impact analysis
- Never assume a function does what its name suggests — verify via GitNexus
- Never read an entire large file when a line range will do
- Never invent environment variables without documenting them
- Never say "I think this is how it works" — query GitNexus and be certain

---

## QUICK REFERENCE CARD

```
NEW TASK?          → gitnexus_repo_summary() + gitnexus_entry_points()
FIND CODE?         → gitnexus_semantic_search("<keywords>")
FIND A FUNCTION?   → gitnexus_find_symbol("<name>")
WHO CALLS THIS?    → gitnexus_find_usages("<name>")
WHAT DOES IT CALL? → gitnexus_call_graph("<name>", depth=N)
WHAT BREAKS?       → gitnexus_impact_analysis("<file>")
WHERE ARE TESTS?   → gitnexus_find_tests("<file>")
WHAT'S THE SCHEMA? → gitnexus_data_models()
WHAT ENV VARS?     → gitnexus_env_vars()
```

---

*This file is the source of truth for AI agent behavior in this repo.
If instructions here conflict with a comment in code or a prompt in a task,
this file wins. Update it when conventions change.*