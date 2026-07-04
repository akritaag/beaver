"""
============================================================================
 Codex-backed agent for BEAVER text-to-SQL.
 Three generation modes (pick via env), all gold-blind:
   * plain          : one `codex exec` call -> SQL                (default)
   * self-fix       : CODEX_SQL_FIX=1     -> run own SQL, fix execution errors
   * explore+verify : CODEX_SQL_EXPLORE=1 -> run read-only queries against the
                      real tables, see the rows returned, self-check, finalize
============================================================================

All DB access is mediated by THIS process: the model emits queries through a
text protocol and we execute them read-only and feed back the rows. The model
never gets credentials, network, or filesystem access to the gold files. It can
see the INPUT data (tables) and the rows ITS OWN queries return — never the
correct/expected answer (gold SQL/results live in the JSON files, not in MySQL).

  instance fields (built in prompt.py::build_instances):
    id, question, db, tables, prompt (chat messages), hints, record
    -> NOTE: do not read record['sql']; that is the gold answer.

Config (env vars, all optional):
    CODEX_MODEL              Codex model (`codex -m`); unset -> account default.
    CODEX_REASONING_EFFORT   minimal|low|medium|high|xhigh  (default: low)
    CODEX_SANDBOX            read-only|workspace-write|danger-full-access (default: read-only)
    CODEX_TIMEOUT            per codex-call seconds (default: 300)
    CODEX_BIN               path to codex (default: codex)

    CODEX_SQL_FIX           1 -> fix execution errors via error feedback (default 0)
    CODEX_FIX_ATTEMPTS      max fix rounds (default 2)

    CODEX_SQL_EXPLORE       1 -> explore/verify loop (overrides SQL_FIX) (default 0)
    CODEX_EXPLORE_STEPS     max exploratory query rounds (default 4)
    CODEX_EXPLORE_ROWS      max rows returned per exploratory query (default 20)
    CODEX_FIX_TIMEOUT_MS    SELECT execution cap, ms (default 15000)
    MYSQL_HOST/USER/PASSWORD  DB creds (env or nearest .env)
"""
import os
import re
import shutil
import tempfile
import subprocess

CODEX_BIN = os.getenv("CODEX_BIN", "codex")
CODEX_MODEL = os.getenv("CODEX_MODEL")
CODEX_REASONING_EFFORT = os.getenv("CODEX_REASONING_EFFORT", "low")
CODEX_SANDBOX = os.getenv("CODEX_SANDBOX", "read-only")
CODEX_TIMEOUT = int(os.getenv("CODEX_TIMEOUT", "300"))

CODEX_SQL_FIX = os.getenv("CODEX_SQL_FIX", "0") not in ("0", "", "false", "False")
CODEX_FIX_ATTEMPTS = int(os.getenv("CODEX_FIX_ATTEMPTS", "2"))

CODEX_SQL_EXPLORE = os.getenv("CODEX_SQL_EXPLORE", "0") not in ("0", "", "false", "False")
CODEX_EXPLORE_STEPS = int(os.getenv("CODEX_EXPLORE_STEPS", "4"))
CODEX_EXPLORE_ROWS = int(os.getenv("CODEX_EXPLORE_ROWS", "20"))
CODEX_FIX_TIMEOUT_MS = int(os.getenv("CODEX_FIX_TIMEOUT_MS", "15000"))

# Self-decompose the question (and validate each sub-step with SQL) instead of
# relying on a provided decomposition hint.
CODEX_DECOMPOSE = os.getenv("CODEX_DECOMPOSE", "0") not in ("0", "", "false", "False")
# Final subagent pass that reviews the answer against the question for intent capture.
CODEX_REVIEW = os.getenv("CODEX_REVIEW", "0") not in ("0", "", "false", "False")

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
    """Flatten the chat-style prompt into the single string `codex exec` expects."""
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


def _codex_call(prompt: str, model: str) -> str:
    """One headless `codex exec` call -> raw final message text."""
    workdir = tempfile.mkdtemp(prefix="codex_beaver_")
    last_msg = os.path.join(workdir, "_last_message.txt")
    cmd = [
        CODEX_BIN, "exec", "--sandbox", CODEX_SANDBOX, "--skip-git-repo-check",
        "-C", workdir, "-c", f"model_reasoning_effort={CODEX_REASONING_EFFORT}", "-o", last_msg,
    ]
    if CODEX_MODEL:
        cmd += ["-m", CODEX_MODEL]
    cmd.append(prompt)
    try:
        proc = subprocess.run(
            cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=CODEX_TIMEOUT,
        )
        raw = ""
        if os.path.exists(last_msg):
            with open(last_msg) as f:
                raw = f.read()
        if not raw.strip() and proc.returncode != 0:
            tail = (proc.stdout or "") + (proc.stderr or "")
            raise RuntimeError(f"codex exec failed (rc={proc.returncode}): {tail[-800:].strip()}")
        return raw
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"codex exec timed out after {CODEX_TIMEOUT}s")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _codex_generate(prompt: str, model: str) -> str:
    return clean_sql(_codex_call(prompt, model))


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
    return mysql.connector.connect(
        host=_load_db_creds()[0], user=_load_db_creds()[1], password=_load_db_creds()[2],
        database=db, connection_timeout=10,
    )


def _execute_sql(sql: str, db: str):
    """Run read-only with a time cap. Returns (ok, error_str). Never inspects rows."""
    if not _is_read_only(sql):
        return False, f"refusing to execute non-read statement (starts with {sql.split(None,1)[:1]})"
    conn = None
    try:
        conn = _connect(db)
        cur = conn.cursor()
        try:
            cur.execute(f"SET SESSION max_execution_time={CODEX_FIX_TIMEOUT_MS}")
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
    """Run read-only and return a compact text preview of up to max_rows rows.
    Returns feedback string for the model (cols + rows, or the error)."""
    if not _is_read_only(sql):
        return "ERROR: only read-only queries (SELECT/WITH/SHOW/DESCRIBE/EXPLAIN) are allowed here."
    conn = None
    try:
        conn = _connect(db)
        cur = conn.cursor()
        try:
            cur.execute(f"SET SESSION max_execution_time={CODEX_FIX_TIMEOUT_MS}")
        except Exception:
            pass
        cur.execute(sql)
        rows = cur.fetchmany(max_rows)
        cols = [d[0] for d in cur.description] if cur.description else []
        extra = ""
        if len(rows) == max_rows:
            extra = f"\n... (truncated at {max_rows} rows)"
        def fmt(v):
            s = "NULL" if v is None else str(v)
            return s if len(s) <= _MAX_CELL_CHARS else s[:_MAX_CELL_CHARS] + "…"
        lines = [" | ".join(cols)]
        for r in rows:
            lines.append(" | ".join(fmt(v) for v in r))
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
    for _ in range(CODEX_FIX_ATTEMPTS):
        ok, err = _execute_sql(sql, db)
        if ok:
            break
        fixed = _codex_generate(_fix_prompt(base, db, sql, err), model)
        if not fixed or fixed == sql:
            break
        sql = fixed
    return sql


_DECOMPOSE_GUIDANCE = """\

### Approach: decompose, then validate each part with SQL
Break the question into its sub-steps (filters, joins, groupings, aggregations,
top-k/window pieces). For EACH sub-step, write a small SQL query and RUN it to
confirm that piece returns sensible data — spot-check intermediate results (row
counts, sample values, distinct keys, ranges) so each part looks valid before you
compose them. Only after the parts check out, compose the full query, run it, and
confirm the final rows match the question's intent.\
"""


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
        # strip code fences if present
        if "```" in q:
            q = q.split("```")[1] if q.count("```") >= 2 else q.replace("```sql", "").replace("```", "")
            q = q.replace("sql\n", "", 1).strip() if q.lower().startswith("sql") else q.strip()
        # cut at a trailing <ans> if model appended one
        q = q.split("<ans>")[0].strip()
        return "run", q.strip()
    # no protocol token -> treat as a (possibly fenced) final SQL
    return "answer", clean_sql(text)


def _run_explore(base, model, db):
    guidance = _DECOMPOSE_GUIDANCE if CODEX_DECOMPOSE else ""
    transcript = base + guidance + _EXPLORE_PROTOCOL.format(db=db, steps=CODEX_EXPLORE_STEPS)
    last_sql = ""
    for step in range(CODEX_EXPLORE_STEPS):
        resp = _codex_call(transcript, model)
        action, payload = _parse_action(resp)
        if action == "answer" and payload:
            return payload
        if action == "run" and payload:
            last_sql = payload
            result = _query_preview(payload, db, CODEX_EXPLORE_ROWS)
            transcript += (
                f"\n\n### Your query (step {step + 1})\n{payload}\n\n### Result\n{result}\n\n"
                f"Run another RUN_SQL query, or output your final <ans>...</ans>."
            )
        else:
            break
    # out of steps (or no parseable action): force a final answer
    final = _codex_call(
        transcript + "\n\nYou must now output ONLY your final answer as <ans>YOUR MYSQL QUERY</ans>.",
        model,
    )
    return clean_sql(final) or last_sql


def _review(question, sql, db, model):
    """Subagent review: check the candidate query against the question for intent
    capture, grounded in the query's own results (gold-blind). Returns a confirmed
    or corrected query."""
    preview = _query_preview(sql, db, CODEX_EXPLORE_ROWS)
    prompt = (
        "You are a strict reviewer subagent for a text-to-SQL task. Decide whether the candidate "
        "MySQL query fully captures the INTENT of the question. Check for: missing or extra output "
        "columns; wrong/missing filters; wrong aggregation scope (e.g. overall vs windowed/rolling "
        "average); unintended LIMIT/top-k; wrong grouping granularity; missing joins or tables; "
        "rounding when none was requested. You are given the query's ACTUAL result rows to "
        "spot-check — you are NOT given the correct answer.\n\n"
        f"QUESTION:\n{question}\n\nCANDIDATE SQL:\n{sql}\n\nITS RESULT (sample):\n{preview}\n\n"
        "If the query correctly captures the intent, return it unchanged. Otherwise return a "
        "corrected MySQL query that does. Output ONLY the final query wrapped in <ans></ans>."
    )
    revised = _codex_generate(prompt, model)
    return revised or sql


def run_agent(instance: dict, model: str) -> str:
    base = render_prompt(instance)
    db = instance.get("db") or "dw"
    question = instance.get("question", "")
    if CODEX_SQL_EXPLORE:
        sql = _run_explore(base, model, db)
        if CODEX_REVIEW and sql:  # subagent intent review
            sql = _review(question, sql, db, model)
        if CODEX_SQL_FIX:  # final error-repair pass
            sql = _fix_loop(base, model, db, sql)
        return sql
    if CODEX_SQL_FIX:
        return _fix_loop(base, model, db, _codex_generate(base, model))
    return _codex_generate(base, model)
