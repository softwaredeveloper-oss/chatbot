"""Oracle database questions: text-to-SQL, strictly READ-ONLY.

Flow:  question -> LLM writes ONE SELECT -> validator -> read-only Oracle session -> table answer.

Safety layers (the configured account may be a powerful one, so the app enforces read-only itself):
  1. Only tables listed (enabled) in db_config.yaml can be queried; hidden columns can never be used.
  2. The validator accepts a single SELECT / WITH ... SELECT only: no DML/DDL/PL-SQL, no comments,
     no quoted identifiers, no DB links, no dictionary views, no SELECT *, only whitelisted functions.
  3. Every query runs inside "SET TRANSACTION READ ONLY" (the database itself rejects any write),
     with a row cap and a call timeout, and is rolled back afterwards.
Settings (credentials, tables, limits) live in db_config.yaml (git-ignored).
"""
import os
import re
import threading
import time
from datetime import date, datetime
from html import escape

import yaml

try:
    import oracledb
except ImportError:  # the app must still start without the driver
    oracledb = None

CONFIG_PATH = os.getenv("DB_CONFIG_PATH") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db_config.yaml"
)

# --------------------------------------------------------------------------- validation
FORBIDDEN = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|exec|execute|call|begin|declare|"
    r"commit|rollback|savepoint|lock|rename|comment|analyze|purge|flashback|into|dbms_\w*|utl_\w*|"
    r"xmltype|httpuritype|sys_context|sys_\w*|connect_by_root|nextval|currval)\b"
    r"|\bfor\s+update\b|\bsys\.|\b(v|gv)\$|\b(dba|all|user)_\w+|\btable\s*\(|\bdatabase\s+link\b",
    re.IGNORECASE,
)
ALLOWED_FUNCTIONS = {
    "count", "sum", "avg", "min", "max", "round", "trunc", "ceil", "floor", "abs", "mod", "power", "sqrt",
    "to_char", "to_date", "to_number", "to_timestamp", "cast", "nvl", "nvl2", "coalesce", "nullif", "decode",
    "upper", "lower", "initcap", "substr", "instr", "length", "trim", "ltrim", "rtrim", "replace", "lpad", "rpad",
    "concat", "add_months", "months_between", "last_day", "next_day", "extract", "greatest", "least",
    "row_number", "rank", "dense_rank", "ntile", "lag", "lead", "first_value", "last_value", "median",
    "stddev", "variance", "listagg", "sign", "regexp_like", "regexp_substr", "regexp_replace",
}
# words that may legitimately be followed by "(" without being functions
KEYWORDS_BEFORE_PAREN = {
    "in", "from", "join", "on", "and", "or", "not", "exists", "as", "over", "partition", "by", "select", "where",
    "when", "then", "else", "end", "like", "between", "union", "all", "minus", "intersect", "having", "any", "some",
    "distinct", "with", "order", "group", "using", "case", "is", "null", "asc", "desc", "inner", "left", "right",
    "outer", "full", "cross", "keep", "within", "rows", "range", "unbounded", "preceding", "following", "current",
    "row", "fetch", "first", "next", "only", "interval", "year", "month", "day", "values",
}
_STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")


class SqlRejected(Exception):
    pass


def _strip_literals(sql: str) -> str:
    return _STRING_LITERAL.sub("''", sql)


def validate_sql(sql: str, allowed_tables: set, hidden_columns: set, owner: str):
    """Return the cleaned single SELECT, or raise SqlRejected with a reason the LLM can act on."""
    s = (sql or "").strip()
    s = re.sub(r";\s*$", "", s).strip()
    if not s:
        raise SqlRejected("empty query")
    bare = _strip_literals(s)
    low = bare.lower()
    if ";" in bare:
        raise SqlRejected("only one statement is allowed")
    if "--" in bare or "/*" in bare or "*/" in bare:
        raise SqlRejected("comments are not allowed")
    if '"' in bare or "@" in bare or "&" in bare or ":" in bare or "\\" in bare:
        raise SqlRejected("quoted identifiers, binds, db links and special characters are not allowed")
    if not re.match(r"^\s*(select|with)\b", low):
        raise SqlRejected("only SELECT queries are allowed")
    m = FORBIDDEN.search(bare)
    if m:
        raise SqlRejected(f"'{m.group(0).strip()}' is not allowed")
    no_count = re.sub(r"count\s*\(\s*\*\s*\)", "count(1)", low)
    if re.search(r"select\s+(distinct\s+)?\*|,\s*\*|\.\*", no_count):
        raise SqlRejected("SELECT * is not allowed: list the columns you need")

    # hidden / sensitive columns can never be referenced
    for col in hidden_columns:
        if re.search(rf"\b{re.escape(col.lower())}\b", low):
            raise SqlRejected(f"column {col.upper()} is restricted and cannot be used")

    # functions: any name followed by "(" must be a keyword or a whitelisted function
    for fm in re.finditer(r"\b([a-z_][a-z0-9_$#]*)\s*\(", low):
        name = fm.group(1)
        if name not in KEYWORDS_BEFORE_PAREN and name not in ALLOWED_FUNCTIONS:
            raise SqlRejected(f"function {name.upper()} is not allowed")

    # tables: comma joins are not allowed; every FROM / JOIN target must be an allowed table (or a CTE / DUAL)
    if re.search(r"\bfrom\s+[a-z_][\w$#.]*(\s+(as\s+)?[a-z_]\w*)?\s*,", low):
        raise SqlRejected("use explicit JOIN ... ON instead of comma-separated tables")
    ctes = set(re.findall(r"\bwith\s+([a-z_]\w*)\s+as\s*\(", low)) | set(re.findall(r",\s*([a-z_]\w*)\s+as\s*\(", low))
    allowed = {t.lower() for t in allowed_tables}
    for tm in re.finditer(r"\b(?:from|join)\s+([a-z_][\w$#.]*)", low):
        target = tm.group(1)
        if "." in target:
            schema, _, target = target.partition(".")
            if schema != owner.lower() or "." in target:
                raise SqlRejected(f"schema {schema.upper()} is not allowed")
        if target not in allowed and target not in ctes and target != "dual":
            raise SqlRejected(f"table {target.upper()} is not allowed")
    return s


# --------------------------------------------------------------------------- service
class OracleService:
    def __init__(self, llm=None, config_path: str = CONFIG_PATH):
        self.llm = llm
        self.cfg = None
        self.error = ""
        self._pool = None
        self._pool_lock = threading.Lock()
        self._meta = None  # (timestamp, {table: schema text}, {table: (note, newest date)})
        self._load(config_path)

    # ---- configuration ----------------------------------------------------------
    def _load(self, path: str):
        if oracledb is None:
            self.error = "python-oracledb is not installed"
            return
        if not os.path.exists(path):
            self.error = f"{os.path.basename(path)} not found"
            return
        try:
            cfg = yaml.safe_load(open(path, encoding="utf-8")) or {}
            o = cfg.get("oracle") or {}
            if not (o.get("user") and o.get("password") and o.get("dsn")) or "YOUR_" in str(o.get("user", "")):
                self.error = "oracle credentials are not filled in"
                return
            tables = [t for t in cfg.get("tables", []) if t.get("enabled", True) and t.get("columns")]
            if "SCHEMA_NAME" in str(tables[:1]) or not tables:
                self.error = "no tables are enabled"
                return
            cfg["tables"] = tables
            self.cfg = cfg
        except Exception as e:
            self.error = f"config could not be read: {e}"

    @property
    def available(self) -> bool:
        return self.cfg is not None and self.llm is not None

    def _owner(self) -> str:
        return str(self.cfg["oracle"]["user"]).upper()

    def _hidden(self) -> set:
        return {str(h).upper() for t in self.cfg["tables"] for h in (t.get("hidden_columns") or [])}

    # ---- database ----------------------------------------------------------------
    def _get_pool(self):
        with self._pool_lock:
            if self._pool is None:
                o = self.cfg["oracle"]
                lib = o.get("client_lib_dir")
                if lib and oracledb.is_thin_mode():
                    try:  # only needed for very old servers (11g); 12c and newer use the built-in Thin mode
                        oracledb.init_oracle_client(lib_dir=lib)
                    except Exception as e:
                        print(f"[WARN] Oracle client library not used ({e}); staying in Thin mode")
                self._pool = oracledb.create_pool(
                    user=o["user"], password=o["password"], dsn=o["dsn"], min=1, max=4, increment=1,
                    getmode=oracledb.POOL_GETMODE_TIMEDWAIT, wait_timeout=10000,
                    tcp_connect_timeout=float(o.get("connect_timeout_seconds", 60)),
                )
            return self._pool

    def _run(self, sql: str):
        """Run one validated SELECT in a READ ONLY transaction. Returns (columns, rows)."""
        limits = self.cfg.get("limits") or {}
        max_rows = int(limits.get("max_rows", 200))
        timeout_ms = int(float(limits.get("query_timeout_seconds", 15)) * 1000)
        conn = self._get_pool().acquire()
        try:
            conn.call_timeout = timeout_ms
            cur = conn.cursor()
            cur.execute("SET TRANSACTION READ ONLY")
            cur.arraysize = min(max_rows, 500)
            cur.execute(f"SELECT * FROM ({sql}) WHERE ROWNUM <= :max_rows", max_rows=max_rows)
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
            return cols, rows
        finally:
            try:
                conn.rollback()  # ends the read-only transaction
            finally:
                self._get_pool().release(conn)

    def _fetch_meta(self):
        """Column types and the date range of the data (cached for an hour)."""
        if self._meta and time.time() - self._meta[0] < 3600:
            return self._meta[1], self._meta[2]
        types = {}
        data_notes = {}  # table -> (note for the LLM, newest date)
        conn = self._get_pool().acquire()
        try:
            cur = conn.cursor()
            names = [t["name"].upper() for t in self.cfg["tables"]]
            binds = ",".join(f":{i}" for i in range(len(names)))
            cur.execute(
                f"SELECT table_name, column_name, data_type FROM all_tab_columns "
                f"WHERE owner = :o AND table_name IN ({binds})", [self._owner()] + names,
            )
            for t, c, d in cur.fetchall():
                types[(t, c)] = d
            for dr in self._data_ranges():
                try:
                    tbl, col = dr["table"].upper(), dr["column"]
                    cur.execute(
                        f"SELECT TO_CHAR({col}, 'YYYY'), COUNT(*), MAX({col}) FROM {tbl} "
                        f"WHERE {col} IS NOT NULL GROUP BY TO_CHAR({col}, 'YYYY') ORDER BY 1"
                    )
                    rows = cur.fetchall()
                    if rows:
                        newest = max(r[2] for r in rows)
                        per_year = "; ".join(f"{y}: {n:,}" for y, n, _ in rows)
                        data_notes[tbl] = (
                            f"DATA PERIOD of {tbl}.{col} - rows per year: {per_year}. Newest date: {newest:%d-%b-%Y}. "
                            f"The data is NOT live: 'recent', 'latest', 'this month', 'last month', 'this year' mean "
                            f"relative to the newest date, not SYSDATE.",
                            newest,
                        )
                except Exception as e:
                    print(f"[WARN] Data range for {dr} failed: {e}")
        finally:
            conn.rollback()
            self._get_pool().release(conn)

        # one compact text block per table: "COLUMN(TYPE) description" items, comma separated
        hidden = self._hidden()
        tables_text = {}
        for t in self.cfg["tables"]:
            tn = t["name"].upper()
            items = []
            for c in t["columns"]:
                cn = str(c["name"]).upper()
                if cn in hidden:
                    continue
                typ = types.get((tn, cn)) or c.get("type") or ""
                item = f"{cn}({typ})" if typ else cn
                if c.get("description"):
                    item += f" [{c['description']}]"
                items.append(item)
            tables_text[tn] = f"TABLE {tn}: {t.get('description', '')}\n  COLUMNS: " + ", ".join(items)
        self._meta = (time.time(), tables_text, data_notes)
        return tables_text, data_notes

    def _data_ranges(self) -> list:
        dr = self.cfg.get("data_range") or []
        dr = [dr] if isinstance(dr, dict) else dr
        return [d for d in dr if d.get("table") and d.get("column")]

    def _select_tables(self, question: str, tables_text: dict) -> list:
        """The tables relevant to the question (keeps the LLM prompt small): by each table's keywords."""
        q = question.lower()
        picked = []
        for t in self.cfg["tables"]:
            kw = t.get("keywords")
            if kw and re.search(r"\b(?:%s)" % kw, q):
                picked.append(t["name"].upper())
        if not picked:  # nothing matched: the main table only (keeps the prompt small)
            ranges = self._data_ranges()
            main = (ranges[0]["table"] if ranges else "").upper()
            picked = [main] if main in tables_text else list(tables_text)[:1]
        return picked[:3]

    # ---- LLM ---------------------------------------------------------------------
    def _prompt(self, question: str, tables_text: dict, data_notes: dict) -> str:
        picked = self._select_tables(question, tables_text)
        schema_text = "\n".join(tables_text[t] for t in picked)
        data_note = "\n".join(data_notes[t][0] for t in picked if t in data_notes)
        joins = [j for j in (self.cfg.get("joins") or [])
                 if all(any(t in part.upper() for t in picked) for part in j.split("="))]
        if joins:
            schema_text += "\nJOINS: " + "; ".join(joins)
        # only examples that use the selected tables (at most 3)
        examples = "\n".join(
            f"Q: {e['question']}\nSQL: {e['sql']}"
            for e in [e for e in (self.cfg.get("examples") or []) if e.get("sql")
                      and any(t in e["sql"].upper() for t in picked)][:3]
        )
        return f"""You are an expert Oracle SQL developer for TCI Express logistics data.
Write ONE read-only Oracle SELECT statement that answers the user's question.

RULES
1. Output ONLY the SQL inside a single ```sql block. If the question cannot be answered from the tables below, output exactly: NO_SQL
2. Use ONLY the tables and columns listed below. Never use SELECT *: list the columns. No semicolon, no comments.
3. Oracle syntax: limit rows with ROWNUM or FETCH FIRST n ROWS ONLY; dates with TO_DATE('2024-10-01','YYYY-MM-DD');
   compare text with UPPER(col) = 'VALUE'; use explicit JOIN ... ON; give every selected expression a clear alias.
4. Docket numbers (DWBNO / DWB_NO) are text: compare with a quoted string such as '041294971'.
5. For listings return at most 50 rows (ORDER BY something meaningful). Aggregates (COUNT/SUM/AVG) need no limit.
6. Numeric code columns (such as BASIS or DIV_CODE) have no documented meaning: return the code as is.
7. To add names from a master table (customers, branches) to a total: aggregate FIRST in a subquery, then JOIN the small
   result to the master table. Never join a master table to the big table before summing (it multiplies the numbers).
   CM_CUST is not unique per customer: always join it as (SELECT CUST_CODE, MAX(CUST_NAME) AS CUST_NAME FROM CM_CUST GROUP BY CUST_CODE).
8. Branch states are 2-letter codes (GJ, MH, DL ...): translate state names to the code.
9. Quarters are calendar quarters (Q1 = Jan-Mar, Q2 = Apr-Jun, Q3 = Jul-Sep, Q4 = Oct-Dec) unless the question says FY / financial
   year (then Indian FY: Q1 = Apr-Jun, Q2 = Jul-Sep, Q3 = Oct-Dec, Q4 = Jan-Mar). Include the year and quarter in the result columns.
   "Revenue" means SUM(TFRT) (total freight).
10. A company / person NAME in the question (for example "from Maruti") is matched against the consignor / consignee NAME columns
   (CNOR, CNEE, CNOR_NAME, CNEE_NAME) with UPPER(col) LIKE '%NAME%' - "from X" means consignor, "to X" means consignee.
   CUSTCODE / CUST_CODE hold numeric codes, NEVER compare them with a name.
{data_note}

TABLES
{schema_text}

EXAMPLES
{examples}

Question: {question}"""

    def _generate(self, prompt: str) -> str:
        res = self.llm.invoke(prompt)
        text = (res.content or "").strip()
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()
        if text.upper().startswith("NO_SQL"):
            return "NO_SQL"
        m = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
        return (m.group(1) if m else text).strip()

    # ---- public ------------------------------------------------------------------
    def _keyword_match(self, question: str) -> bool:
        """Does the question mention anything that belongs to one of the tables (free check, no LLM, no database)?"""
        q = question.lower()
        return any(
            t.get("keywords") and re.search(r"\b(?:%s)" % t["keywords"], q) for t in self.cfg["tables"]
        )

    def ask(self, question: str, strict: bool = False) -> dict:
        """{'type': 'table'|'no_sql'|'error', ...}. Never raises for database / LLM-format problems.
        strict=True: if the question mentions nothing from the tables, say no_sql without calling the LLM."""
        if not self.available:
            return {"type": "error", "message": "The database is not configured."}
        if strict and not self._keyword_match(question):
            return {"type": "no_sql"}
        try:
            tables_text, data_notes = self._fetch_meta()
        except Exception as e:
            print(f"[ERROR] Oracle metadata failed: {e}")
            return {"type": "error", "message": "I couldn't reach the database right now. Please try again later."}

        prompt = self._prompt(question, tables_text, data_notes)
        allowed = {t["name"].upper() for t in self.cfg["tables"]}
        hidden = self._hidden()
        last_err = ""
        for attempt in range(2):
            raw = self._generate(prompt if not last_err else
                                 f"{prompt}\n\nYour previous SQL was rejected: {last_err}\nWrite a corrected query.")
            if raw == "NO_SQL":
                return {"type": "no_sql"}
            try:
                sql = validate_sql(raw, allowed, hidden, self._owner())
                print(f"[INFO] Oracle SQL: {sql}")
                cols, rows = self._run(sql)
            except SqlRejected as e:
                print(f"[WARN] SQL rejected ({e}): {raw[:200]}")
                last_err = str(e)
                continue
            except Exception as e:  # database error: let the LLM correct it once
                text = str(e).splitlines()[0][:200]
                print(f"[WARN] Oracle error: {text}")
                if "DPY-4024" in text or "ORA-03156" in text or "ORA-01013" in text:
                    return {"type": "error", "message": "That query took too long. Please narrow it down (for example add a date or branch)."}
                if "DPY-6" in text or "DPY-4011" in text or "ORA-12" in text or "timed out" in text.lower():
                    return {"type": "error", "message": "I couldn't reach the database right now. Please try again later."}
                last_err = text
                continue
            if any(c.upper() in hidden for c in cols):
                last_err = "the result includes a restricted column"
                continue
            # footer: the newest date of the table the SQL actually read
            used = [t for t in data_notes if re.search(rf"\b{t.lower()}\b", sql.lower())]
            newest = data_notes[used[0]][1] if used else None
            footer = f"From the booking database (newest record in this data: {newest:%d-%b-%Y})." if newest else ""
            return self._format(cols, rows, footer)
        return {"type": "error", "message": "I couldn't work that out from the database. Please rephrase, for example by adding a date, branch or customer."}

    # ---- formatting --------------------------------------------------------------
    @staticmethod
    def _cell(v, col: str = ""):
        if v is None:
            return "-"
        if isinstance(v, (datetime, date)):
            return f"{v:%d-%b-%Y}" if not isinstance(v, datetime) or (v.hour, v.minute, v.second) == (0, 0, 0) else f"{v:%d-%b-%Y %H:%M}"
        if isinstance(v, float):
            return f"{v:,.2f}"
        if isinstance(v, int):
            parts = set(re.split(r"[^A-Z]+", col.upper()))
            if parts & {"YEAR", "QUARTER", "MONTH", "DAY", "CODE", "NO", "NUMBER", "PIN", "PINCODE", "ID", "RANK"}:
                return str(v)  # identifiers and calendar parts: no thousands separator (2024, not 2,024)
            return f"{v:,}"
        return str(v)

    def _format(self, cols, rows, footer: str = "") -> dict:
        max_rows = int((self.cfg.get("limits") or {}).get("max_rows", 200))
        shown = rows[:50]
        if not rows:
            return {"type": "text", "message": "No records found for that question.", "html": ""}
        tail = f"<p style='margin:4px 0;'><em>{escape(footer)}</em></p>" if footer else ""
        n = len(rows)
        if n == 1 and len(cols) == 1:  # a single value: say it, no table needed
            return {"type": "table", "message": f"**{cols[0]}:** {self._cell(rows[0][0], cols[0])}", "html": tail, "rows": 1}
        head = "".join(f"<th>{escape(c)}</th>" for c in cols)
        body = "".join("<tr>" + "".join(f"<td>{escape(self._cell(v, cols[i]))}</td>" for i, v in enumerate(r)) + "</tr>" for r in shown)
        table = (
            "<div class='chat-table-wrapper'><table class='dataframe table table-hover table-striped table-sm'>"
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>"
        )
        if n == 1:
            title = "**1 record found:**"
        else:
            more = f" (showing the first {len(shown)})" if n > len(shown) else ""
            cap = f" Results are limited to {max_rows} rows." if n >= max_rows else ""
            title = f"Found **{n}** records{more}.{cap}"
        return {"type": "table", "message": title, "html": table + tail, "rows": n}
