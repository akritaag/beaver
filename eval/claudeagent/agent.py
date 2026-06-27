"""
============================================================================
 Claude Code (`claude -p`) backed agent for BEAVER text-to-SQL.
 Three generation modes (pick via env), all gold-blind:
   * plain          : one `claude -p` call -> SQL                 (default)
   * self-fix       : CLAUDE_SQL_FIX=1     -> run own SQL, fix execution errors
   * explore+verify : CLAUDE_SQL_EXPLORE=1 -> run read-only queries against the
                      real tables, see the rows returned, self-check, finalize
============================================================================

All DB access is mediated by THIS process: the model emits queries through a
text protocol and we execute them read-only and feed back the rows. The model
never gets credentials or filesystem access to the gold files. It can see the
INPUT data (tables) and the rows ITS OWN queries return — never the
correct/expected answer (gold SQL/results live in the JSON files, not in MySQL).

The prompt is passed to `claude -p` over stdin (BEAVER prompts are ~30 KB).

  instance fields (built in prompt.py::build_instances):
    id, question, db, tables, prompt (chat messages), hints, record
    -> NOTE: do not read record['sql']; that is the gold answer.

Config (env vars, all optional):
    CLAUDE_BIN              path to the claude binary (default: claude)
    CLAUDE_MODEL            value for `--model` (default: unset -> claude default)
    CLAUDE_TIMEOUT          per claude-call seconds (default: 300)

    CLAUDE_SQL_FIX          1 -> fix execution errors via error feedback (default 0)
    CLAUDE_FIX_ATTEMPTS     max fix rounds (default 2)

    CLAUDE_SQL_EXPLORE      1 -> explore/verify loop (overrides SQL_FIX alone) (default 0)
    CLAUDE_EXPLORE_STEPS    max exploratory query rounds (default 4)
    CLAUDE_EXPLORE_ROWS     max rows returned per exploratory query (default 20)
    CLAUDE_FIX_TIMEOUT_MS   SELECT execution cap, ms (default 15000)
    MYSQL_HOST/USER/PASSWORD  DB creds (env or nearest .env)
"""
import os
import re
import subprocess

CLAUDE_BIN = os.getenv("CLAUDE_BIN", "claude")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL")  # None -> claude's default model; e.g. "opus", "sonnet"
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT")  # None -> default; e.g. "high" (--effort)
CLAUDE_TIMEOUT = int(os.getenv("CLAUDE_TIMEOUT", "300"))

CLAUDE_SQL_FIX = os.getenv("CLAUDE_SQL_FIX", "0") not in ("0", "", "false", "False")
CLAUDE_FIX_ATTEMPTS = int(os.getenv("CLAUDE_FIX_ATTEMPTS", "2"))

CLAUDE_SQL_EXPLORE = os.getenv("CLAUDE_SQL_EXPLORE", "0") not in ("0", "", "false", "False")
CLAUDE_EXPLORE_STEPS = int(os.getenv("CLAUDE_EXPLORE_STEPS", "4"))
CLAUDE_EXPLORE_ROWS = int(os.getenv("CLAUDE_EXPLORE_ROWS", "20"))
CLAUDE_FIX_TIMEOUT_MS = int(os.getenv("CLAUDE_FIX_TIMEOUT_MS", "15000"))

_READ_STMTS = ("select", "with", "show", "describe", "desc", "explain")
_MAX_FEEDBACK_CHARS = 4000
_MAX_CELL_CHARS = 80


def clean_sql(text: str) -> str:
    """Strip <ans></ans> tags and ```sql fences from a model response."""
    if not text:
        return ""
    if "<ans>" in text:
        text = text.split("<ans>")[-1]
    if "</ans>" in text:
        text = text.split("</ans>")[0]
    if "```sql" in text:
        text = text.split("```sql")[-1]
    if "```" in text:
        text = text.split("```")[0]
    return text.strip()


def render_prompt(instance: dict) -> str:
    """Flatten the chat-style prompt into one string for `claude -p`."""
    blocks = []
    user_seen = 0
    for msg in instance["prompt"]:
        role, content = msg["role"], msg["content"]
        if role == "system":
            blocks.append(content)
        elif role == "assistant":
            blocks.append(f"### Example answer\n{content}")
        elif role == "user":
            user_seen += 1
            header = "### Example input" if user_seen == 1 else "### Now answer this"
            blocks.append(f"{header}\n{content}")
    return "\n\n".join(blocks)


def _claude_call(prompt: str, model: str) -> str:
    """One headless `claude -p` call -> raw stdout text."""
    cmd = [CLAUDE_BIN, "-p", "--output-format", "text"]
    if CLAUDE_MODEL:
        cmd += ["--model", CLAUDE_MODEL]
    if CLAUDE_EFFORT:
        cmd += ["--effort", CLAUDE_EFFORT]
    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, timeout=CLAUDE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"claude -p timed out after {CLAUDE_TIMEOUT}s")
    if not (proc.stdout or "").strip() and proc.returncode != 0:
        raise RuntimeError(f"claude -p failed (rc={proc.returncode}): {(proc.stderr or '')[-800:].strip()}")
    return proc.stdout or ""


def _claude_generate(prompt: str, model: str) -> str:
    return clean_sql(_claude_call(prompt, model))


# ----------------------- read-only DB access (gold-blind) -----------------------

def _load_db_creds():
    if not os.getenv("MYSQL_HOST"):
        try:
            from dotenv import load_dotenv, find_dotenv
            load_dotenv(find_dotenv(usecwd=True))
        except Exception:
            pass
    return os.getenv("MYSQL_HOST", "localhost"), os.getenv("MYSQL_USER", "root"), os.getenv("MYSQL_PASSWORD", "")


def _is_read_only(sql: str) -> bool:
    first = (sql.lstrip().split(None, 1)[0].lower() if sql.strip() else "")
    return first in _READ_STMTS


def _connect(db):
    import mysql.connector
    h, u, p = _load_db_creds()
    return mysql.connector.connect(host=h, user=u, password=p, database=db, connection_timeout=10)


def _execute_sql(sql: str, db: str):
    """Run read-only with a time cap. Returns (ok, error_str). Never inspects rows."""
    if not _is_read_only(sql):
        return False, f"refusing to execute non-read statement (starts with {sql.split(None,1)[:1]})"
    conn = None
    try:
        conn = _connect(db)
        cur = conn.cursor()
        try:
            cur.execute(f"SET SESSION max_execution_time={CLAUDE_FIX_TIMEOUT_MS}")
        except Exception:
            pass
        cur.execute(sql)
        cur.fetchmany(1)
        return True, ""
    except Exception as e:
        return False, str(e)
    finally:
        try:
            conn and conn.close()
        except Exception:
            pass


def _query_preview(sql: str, db: str, max_rows: int):
    """Run read-only and return a compact text preview of up to max_rows rows."""
    if not _is_read_only(sql):
        return "ERROR: only read-only queries (SELECT/WITH/SHOW/DESCRIBE/EXPLAIN) are allowed here."
    conn = None
    try:
        conn = _connect(db)
        cur = conn.cursor()
        try:
            cur.execute(f"SET SESSION max_execution_time={CLAUDE_FIX_TIMEOUT_MS}")
        except Exception:
            pass
        cur.execute(sql)
        rows = cur.fetchmany(max_rows)
        cols = [d[0] for d in cur.description] if cur.description else []
        extra = f"\n... (truncated at {max_rows} rows)" if len(rows) == max_rows else ""

        def fmt(v):
            s = "NULL" if v is None else str(v)
            return s if len(s) <= _MAX_CELL_CHARS else s[:_MAX_CELL_CHARS] + "…"

        lines = [" | ".join(cols)] + [" | ".join(fmt(v) for v in r) for r in rows]
        body = "\n".join(lines) + extra
        if len(body) > _MAX_FEEDBACK_CHARS:
            body = body[:_MAX_FEEDBACK_CHARS] + "\n… (output truncated)"
        return f"{len(rows)} row(s) returned:\n{body}"
    except Exception as e:
        return f"ERROR: {e}"
    finally:
        try:
            conn and conn.close()
        except Exception:
            pass


# ------------------------------- modes -------------------------------

def _fix_prompt(base, db, bad_sql, error):
    return (
        base + f"\n\n### Execution feedback\nThe query below was run against the `{db}` MySQL "
        f"database and FAILED TO EXECUTE. Fix it so it runs without error, keeping the intended "
        f"logic.\n\nFailed SQL:\n{bad_sql}\n\nDatabase error:\n{error}\n\n"
        f"Return only the corrected MySQL query wrapped in <ans></ans>."
    )


def _fix_loop(base, model, db, sql):
    """Given a candidate SQL, repair execution errors (error feedback only)."""
    if not sql:
        return sql
    for _ in range(CLAUDE_FIX_ATTEMPTS):
        ok, err = _execute_sql(sql, db)
        if ok:
            break
        fixed = _claude_generate(_fix_prompt(base, db, sql, err), model)
        if not fixed or fixed == sql:
            break
        sql = fixed
    return sql


_EXPLORE_PROTOCOL = """\

### Database access (read-only) — verification is REQUIRED
You have live read-only access to the `{db}` MySQL database. You will be shown the rows YOUR queries return — you will NOT be shown the expected/correct answer.

You MUST run at least one query to CHECK your candidate answer before finalizing: inspect the real tables (sample rows, distinct values, counts, ranges) to confirm your assumptions, then run your candidate query and verify the returned rows make sense for the question (right columns, plausible row count, filters/joins working, not empty when it shouldn't be). Revise if the results look wrong.

Respond with EXACTLY ONE of the following each turn (nothing else):

1) To run a read-only query (SELECT/WITH/SHOW/DESCRIBE/EXPLAIN), output:
RUN_SQL:
<one SQL statement>

2) Only after you have verified, output your final answer as:
<ans>YOUR FINAL MYSQL QUERY</ans>

You have at most {steps} queries. Do not give <ans> until you have run at least one verification query.\
"""


def _parse_action(text: str):
    """Return ('answer', sql) or ('run', query) from a model turn."""
    if "<ans>" in text:
        return "answer", clean_sql(text)
    m = re.search(r"RUN_SQL:\s*", text)
    if m:
        q = text[m.end():].strip()
        if "```" in q:
            q = q.split("```")[1] if q.count("```") >= 2 else q.replace("```sql", "").replace("```", "")
            q = q.replace("sql\n", "", 1).strip() if q.lower().startswith("sql") else q.strip()
        q = q.split("<ans>")[0].strip()
        return "run", q.strip()
    return "answer", clean_sql(text)


def _run_explore(base, model, db):
    transcript = base + _EXPLORE_PROTOCOL.format(db=db, steps=CLAUDE_EXPLORE_STEPS)
    last_sql = ""
    for step in range(CLAUDE_EXPLORE_STEPS):
        resp = _claude_call(transcript, model)
        action, payload = _parse_action(resp)
        if action == "answer" and payload:
            return payload
        if action == "run" and payload:
            last_sql = payload
            result = _query_preview(payload, db, CLAUDE_EXPLORE_ROWS)
            transcript += (
                f"\n\n### Your query (step {step + 1})\n{payload}\n\n### Result\n{result}\n\n"
                f"Run another RUN_SQL query, or output your final <ans>...</ans>."
            )
        else:
            break
    final = _claude_call(
        transcript + "\n\nYou must now output ONLY your final answer as <ans>YOUR MYSQL QUERY</ans>.",
        model,
    )
    return clean_sql(final) or last_sql


def run_agent(instance: dict, model: str) -> str:
    base = render_prompt(instance)
    db = instance.get("db") or "dw"
    if CLAUDE_SQL_EXPLORE:
        sql = _run_explore(base, model, db)
        if CLAUDE_SQL_FIX:  # final error-repair pass on the explored answer
            sql = _fix_loop(base, model, db, sql)
        return sql
    if CLAUDE_SQL_FIX:
        return _fix_loop(base, model, db, _claude_generate(base, model))
    return _claude_generate(base, model)
