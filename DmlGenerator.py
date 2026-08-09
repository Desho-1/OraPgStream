#!/usr/bin/env python3
"""
hr_dml_generator.py
====================
Continuous DML workload generator for Oracle HR schema.
Used to simulate production activity during OraPgStream CDC replication demos.

Simulates a realistic HR system:
  - New employees being hired
  - Salary reviews and updates
  - Employee transfers between departments
  - Job history tracking
  - Employee terminations
  - Department budget updates

Usage:
    pip install oracledb
    python hr_dml_generator.py [options]

Examples:
    # Default: 30 TPS, run forever, print summary every 10s
    python hr_dml_generator.py

    # Custom rate and duration
    python hr_dml_generator.py --tps 50 --duration 300

    # Quiet mode (no per-operation output, only summary)
    python hr_dml_generator.py --quiet

    # Override connection details
    python hr_dml_generator.py --host 192.168.1.10 --port 1521 --service ORCLPDB1

Requirements:
    - Oracle HR schema installed and accessible
    - User with INSERT/UPDATE/DELETE privileges on HR tables
    - python-oracledb >= 1.0 (thin mode, no Oracle client required)
"""
from __future__ import annotations
import oracledb
import random
import time
import argparse
import signal
import sys
import threading
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional

# ---------------------------------------------------------------------------
# Configuration defaults — override via CLI args or environment variables
# ---------------------------------------------------------------------------

DEFAULT_HOST     = "192.168.200.21"
DEFAULT_PORT     = 1522
DEFAULT_SERVICE  = "primaryd"
DEFAULT_USER     = "hr"
DEFAULT_PASSWORD = "hr"
DEFAULT_TPS      = 30          # target transactions per second
DEFAULT_DURATION = 0           # 0 = run forever
SUMMARY_INTERVAL = 10          # print stats every N seconds

# ---------------------------------------------------------------------------
# Realistic data pools — makes generated data look believable in the blog
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael",
    "Linda", "William", "Barbara", "David", "Elizabeth", "Richard", "Susan",
    "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen", "Ahmed",
    "Fatima", "Mohamed", "Aisha", "Omar", "Layla", "Yusuf", "Nour",
    "Liam", "Emma", "Noah", "Olivia", "Ethan", "Sophia", "Lucas", "Mia",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez", "Gonzalez",
    "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Hassan", "Ali", "Khan", "Patel", "Singh", "Chen", "Wang", "Kim",
]

EMAIL_DOMAINS = ["company.com", "corp.org", "enterprise.net"]

PHONE_FORMATS = [
    "515.123.{:04d}", "650.555.{:04d}", "011.44.1344.{:06d}", "603.123.{:04d}"
]

# ---------------------------------------------------------------------------
# Statistics tracker (thread-safe)
# ---------------------------------------------------------------------------

@dataclass
class Stats:
    inserts:    int = 0
    updates:    int = 0
    deletes:    int = 0
    commits:    int = 0
    rollbacks:  int = 0
    errors:     int = 0
    start_time: float = field(default_factory=time.time)
    _lock:      threading.Lock = field(default_factory=threading.Lock)

    def record(self, op: str):
        with self._lock:
            if op == "insert":   self.inserts   += 1
            elif op == "update": self.updates   += 1
            elif op == "delete": self.deletes   += 1
            elif op == "commit": self.commits   += 1
            elif op == "rollback": self.rollbacks += 1
            elif op == "error":  self.errors    += 1

    def summary(self) -> str:
        with self._lock:
            elapsed = time.time() - self.start_time
            total   = self.inserts + self.updates + self.deletes
            tps     = total / elapsed if elapsed > 0 else 0
            return (
                f"[{datetime.now().strftime('%H:%M:%S')}] "
                f"Elapsed: {elapsed:.0f}s | "
                f"TPS: {tps:.1f} | "
                f"INS: {self.inserts} | "
                f"UPD: {self.updates} | "
                f"DEL: {self.deletes} | "
                f"Commits: {self.commits} | "
                f"Rollbacks: {self.rollbacks} | "
                f"Errors: {self.errors}"
            )

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def random_name() -> tuple[str, str]:
    return random.choice(FIRST_NAMES), random.choice(LAST_NAMES)

def random_email(first: str, last: str) -> str:
    domain = random.choice(EMAIL_DOMAINS)
    suffix = random.randint(1, 999)
    return f"{first.lower()}.{last.lower()}{suffix}@{domain}"

def random_phone() -> str:
    fmt = random.choice(PHONE_FORMATS)
    return fmt.format(random.randint(1000, 9999))

def random_salary(job_id: str) -> int:
    """Return a salary appropriate for the job grade."""
    ranges = {
        "AD_PRES":  (20000, 40000),
        "AD_VP":    (15000, 30000),
        "AD_ASST":  (3000,  6000),
        "FI_MGR":   (8000,  16000),
        "FI_ACCOUNT": (4200, 9000),
        "AC_MGR":   (8000,  16000),
        "AC_ACCOUNT": (4200, 9000),
        "SA_MAN":   (10000, 20000),
        "SA_REP":   (6000,  12000),
        "PU_MAN":   (8000,  15000),
        "PU_CLERK": (2500,  5500),
        "ST_MAN":   (5500,  8500),
        "ST_CLERK": (2000,  5000),
        "SH_CLERK": (2500,  5500),
        "IT_PROG":  (4000,  10000),
        "MK_MAN":   (9000,  15000),
        "MK_REP":   (4000,  9000),
        "HR_REP":   (4000,  9000),
        "PR_REP":   (4500,  10500),
    }
    lo, hi = ranges.get(job_id, (3000, 12000))
    return random.randint(lo, hi)

# ---------------------------------------------------------------------------
# Schema introspection — fetch valid IDs on startup so FK constraints hold
# ---------------------------------------------------------------------------

def fetch_reference_data(conn) -> dict:
    """
    Load valid department_ids, job_ids, manager_ids, and location_ids
    from the live HR schema. This ensures every INSERT satisfies FK constraints.
    """
    data = {}
    with conn.cursor() as cur:

        cur.execute("SELECT department_id FROM hr.departments")
        data["department_ids"] = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT job_id FROM hr.jobs")
        data["job_ids"] = [r[0] for r in cur.fetchall()]

        cur.execute(
            "SELECT employee_id FROM hr.employees WHERE employee_id IN "
            "(SELECT DISTINCT manager_id FROM hr.employees WHERE manager_id IS NOT NULL)"
        )
        data["manager_ids"] = [r[0] for r in cur.fetchall()]

        cur.execute("SELECT location_id FROM hr.locations")
        data["location_ids"] = [r[0] for r in cur.fetchall()]

        # Current max employee_id — we'll increment from here
        cur.execute("SELECT NVL(MAX(employee_id), 206) FROM hr.employees")
        data["max_employee_id"] = cur.fetchone()[0]

    print(f"  Departments:  {len(data['department_ids'])} found")
    print(f"  Jobs:         {len(data['job_ids'])} found")
    print(f"  Managers:     {len(data['manager_ids'])} found")
    print(f"  Locations:    {len(data['location_ids'])} found")
    print(f"  Max emp ID:   {data['max_employee_id']}")

    return data

# ---------------------------------------------------------------------------
# DML operations — each returns the operation type for stats tracking
# ---------------------------------------------------------------------------

def op_hire_employee(cur, ref: dict, emp_id_counter: list) -> str:
    """INSERT a new employee. Simulates someone being hired."""
    first, last = random_name()
    emp_id = emp_id_counter[0]
    emp_id_counter[0] += 1

    job_id  = random.choice(ref["job_ids"])
    dept_id = random.choice(ref["department_ids"])
    mgr_id  = random.choice(ref["manager_ids"]) if ref["manager_ids"] else None
    salary  = random_salary(job_id)
    comm    = round(random.uniform(0.1, 0.4), 2) if job_id.startswith("SA") else None

    cur.execute("""
        INSERT INTO hr.employees (
            employee_id, first_name, last_name, email,
            phone_number, hire_date, job_id, salary,
            commission_pct, manager_id, department_id
        ) VALUES (
            :emp_id, :first, :last, :email,
            :phone, SYSDATE, :job_id, :salary,
            :comm, :mgr_id, :dept_id
        )
    """, {
        "emp_id":  emp_id,
        "first":   first,
        "last":    last,
        "email":   random_email(first, last)[:25],  # HR.EMAIL is VARCHAR2(25)
        "phone":   random_phone(),
        "job_id":  job_id,
        "salary":  salary,
        "comm":    comm,
        "mgr_id":  mgr_id,
        "dept_id": dept_id,
    })
    return "insert"

def op_salary_review(cur) -> str:
    """UPDATE salary for a random employee. Simulates annual salary review."""
    adjustment = random.uniform(0.03, 0.15)   # 3%–15% raise
    cur.execute("""
        UPDATE hr.employees
        SET    salary = ROUND(salary * :adj, 0)
        WHERE  employee_id = (
            SELECT employee_id
            FROM   hr.employees
            ORDER  BY DBMS_RANDOM.VALUE
            FETCH  FIRST 1 ROWS ONLY
        )
    """, {"adj": 1 + adjustment})
    return "update"

def op_transfer_employee(cur, ref: dict) -> str:
    """UPDATE department and/or job for a random employee. Simulates a transfer."""
    new_dept = random.choice(ref["department_ids"])
    new_job  = random.choice(ref["job_ids"])
    cur.execute("""
        UPDATE hr.employees
        SET    department_id = :dept,
               job_id        = :job,
               salary        = :sal
        WHERE  employee_id = (
            SELECT employee_id
            FROM   hr.employees
            ORDER  BY DBMS_RANDOM.VALUE
            FETCH  FIRST 1 ROWS ONLY
        )
    """, {
        "dept": new_dept,
        "job":  new_job,
        "sal":  random_salary(new_job),
    })
    return "update"

def op_update_phone(cur) -> str:
    """UPDATE phone number for a random employee. Light single-column update."""
    cur.execute("""
        UPDATE hr.employees
        SET    phone_number = :phone
        WHERE  employee_id = (
            SELECT employee_id
            FROM   hr.employees
            ORDER  BY DBMS_RANDOM.VALUE
            FETCH  FIRST 1 ROWS ONLY
        )
    """, {"phone": random_phone()})
    return "update"

def op_add_job_history(cur, ref: dict) -> str:
    """
    INSERT a job_history record for a random employee.
    JOB_HISTORY has a composite PK (employee_id, start_date) —
    good test for composite key replication.
    """
    job_id  = random.choice(ref["job_ids"])
    dept_id = random.choice(ref["department_ids"])

    # Use a random past date as start_date; end_date a few months later
    start_offset = random.randint(365, 3650)   # 1–10 years ago
    end_offset   = random.randint(30, 364)     # ended before today

    cur.execute("""
        INSERT INTO hr.job_history (
            employee_id, start_date, end_date, job_id, department_id
        )
        SELECT e.employee_id,
               SYSDATE - :start_off,
               SYSDATE - :end_off,
               :job_id,
               :dept_id
        FROM   hr.employees e
        WHERE  NOT EXISTS (
            SELECT 1 FROM hr.job_history jh
            WHERE  jh.employee_id = e.employee_id
            AND    jh.start_date  = SYSDATE - :start_off2
        )
        ORDER  BY DBMS_RANDOM.VALUE
        FETCH  FIRST 1 ROWS ONLY
    """, {
        "start_off":  start_offset,
        "end_off":    end_offset,
        "job_id":     job_id,
        "dept_id":    dept_id,
        "start_off2": start_offset,
    })
    return "insert"

def op_update_department_budget(cur) -> str:
    """
    UPDATE a department's location (simulates department reorganisation).
    Low-frequency operation for variety.
    """
    cur.execute("""
        UPDATE hr.departments
        SET    location_id = (
                   SELECT location_id
                   FROM   hr.locations
                   ORDER  BY DBMS_RANDOM.VALUE
                   FETCH  FIRST 1 ROWS ONLY
               )
        WHERE  department_id = (
            SELECT department_id
            FROM   hr.departments
            ORDER  BY DBMS_RANDOM.VALUE
            FETCH  FIRST 1 ROWS ONLY
        )
    """)
    return "update"

def op_terminate_employee(cur) -> Optional[str]:
    """
    DELETE a recently-inserted employee (employee_id > 206 = generated ones).
    Simulates a termination. Returns None if no generated employees exist yet.
    """
    cur.execute("""
        SELECT employee_id
        FROM   hr.employees
        WHERE  employee_id > 206
        ORDER  BY DBMS_RANDOM.VALUE
        FETCH  FIRST 1 ROWS ONLY
    """)
    row = cur.fetchone()
    if not row:
        return None   # no generated employees to delete yet

    emp_id = row[0]

    # Clean up job_history first (FK constraint)
    cur.execute(
        "DELETE FROM hr.job_history WHERE employee_id = :id",
        {"id": emp_id}
    )
    cur.execute(
        "DELETE FROM hr.employees WHERE employee_id = :id",
        {"id": emp_id}
    )
    return "delete"

# ---------------------------------------------------------------------------
# Workload mix — weighted probability for each operation
# Adjust weights to change the INSERT/UPDATE/DELETE ratio
# ---------------------------------------------------------------------------

OPERATION_WEIGHTS = [
    # (weight, function, needs_ref_data)
    (25, "hire",            True),   # INSERT employees        25%
    (30, "salary_review",   False),  # UPDATE salary           30%
    (15, "transfer",        True),   # UPDATE dept/job         15%
    (10, "phone_update",    False),  # UPDATE phone            10%
    (8,  "job_history",     True),   # INSERT job_history       8%
    (4,  "dept_budget",     False),  # UPDATE departments       4%
    (8,  "terminate",       False),  # DELETE employee          8%
]

TOTAL_WEIGHT = sum(w for w, _, _ in OPERATION_WEIGHTS)

def pick_operation() -> tuple[str, bool]:
    """Weighted random selection of next operation."""
    r = random.randint(1, TOTAL_WEIGHT)
    cumulative = 0
    for weight, name, needs_ref in OPERATION_WEIGHTS:
        cumulative += weight
        if r <= cumulative:
            return name, needs_ref
    return OPERATION_WEIGHTS[-1][1], OPERATION_WEIGHTS[-1][2]

def execute_operation(cur, op_name: str, ref: dict, emp_id_counter: list) -> Optional[str]:
    """Dispatch to the correct operation function."""
    if op_name == "hire":            return op_hire_employee(cur, ref, emp_id_counter)
    elif op_name == "salary_review": return op_salary_review(cur)
    elif op_name == "transfer":      return op_transfer_employee(cur, ref)
    elif op_name == "phone_update":  return op_update_phone(cur)
    elif op_name == "job_history":   return op_add_job_history(cur, ref)
    elif op_name == "dept_budget":   return op_update_department_budget(cur)
    elif op_name == "terminate":     return op_terminate_employee(cur)
    return None

# ---------------------------------------------------------------------------
# Occasional intentional rollback — tests that CDC handles rollbacks correctly
# OraPgStream should NOT replicate rolled-back transactions
# ---------------------------------------------------------------------------

def op_rollback_test(conn, cur, ref: dict, emp_id_counter: list, stats: Stats, quiet: bool):
    """
    Insert an employee then immediately roll back.
    Verifies that OraPgStream correctly ignores rolled-back transactions —
    this row should NEVER appear in PostgreSQL.
    """
    first, last = random_name()
    emp_id = emp_id_counter[0]
    emp_id_counter[0] += 1
    job_id = random.choice(ref["job_ids"])

    cur.execute("""
        INSERT INTO hr.employees (
            employee_id, first_name, last_name, email,
            phone_number, hire_date, job_id, salary, department_id
        ) VALUES (
            :emp_id, :first, :last, :email,
            :phone, SYSDATE, :job_id, :salary, :dept_id
        )
    """, {
        "emp_id":  emp_id,
        "first":   first,
        "last":    last,
        "email":   random_email(first, last)[:25],
        "phone":   random_phone(),
        "job_id":  job_id,
        "salary":  random_salary(job_id),
        "dept_id": random.choice(ref["department_ids"]),
    })

    conn.rollback()   # <-- intentional rollback
    stats.record("rollback")

    if not quiet:
        print(f"  [ROLLBACK] emp_id={emp_id} inserted then rolled back "
              f"— should NOT appear in PostgreSQL")

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run(args):
    stats           = Stats()
    stop_event      = threading.Event()
    emp_id_counter  = [None]   # will be set after ref data fetch

    # Graceful shutdown on Ctrl+C or SIGTERM
    def handle_signal(sig, frame):
        print("\n\nShutting down gracefully...")
        stop_event.set()

    signal.signal(signal.SIGINT,  handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    # Summary printer thread
    def print_summary():
        while not stop_event.is_set():
            time.sleep(SUMMARY_INTERVAL)
            print(stats.summary())

    summary_thread = threading.Thread(target=print_summary, daemon=True)
    summary_thread.start()

    # Connect
    print(f"\nConnecting to Oracle {args.host}:{args.port}/{args.service} as {args.user}...")
    try:
        conn = oracledb.connect(
            user=args.user,
            password=args.password,
            dsn=f"{args.host}:{args.port}/{args.service}",
            # thin mode — no Oracle client installation required
        )
        conn.autocommit = False
        print("Connected.\n")
    except Exception as e:
        print(f"Connection failed: {e}")
        sys.exit(1)

    # Fetch reference data
    print("Fetching HR schema reference data...")
    try:
        ref = fetch_reference_data(conn)
    except Exception as e:
        print(f"Failed to fetch reference data: {e}")
        print("Ensure the HR schema is installed and the user has SELECT privileges.")
        conn.close()
        sys.exit(1)

    emp_id_counter[0] = ref["max_employee_id"] + 1
    sleep_time        = 1.0 / args.tps
    start_time        = time.time()
    op_count          = 0

    print(f"\nStarting DML generator at ~{args.tps} TPS")
    print(f"Duration: {'forever' if args.duration == 0 else f'{args.duration}s'}")
    print(f"Rollback test: every ~50 transactions")
    print("-" * 60)

    while not stop_event.is_set():
        # Check duration limit
        if args.duration > 0 and (time.time() - start_time) >= args.duration:
            print(f"\nDuration limit ({args.duration}s) reached.")
            break

        op_count += 1

        # Every ~50 ops, do an intentional rollback test
        if op_count % 50 == 0:
            try:
                with conn.cursor() as cur:
                    op_rollback_test(conn, cur, ref, emp_id_counter, stats, args.quiet)
            except Exception as e:
                stats.record("error")
                if not args.quiet:
                    print(f"  [ERROR] Rollback test: {e}")
            time.sleep(sleep_time)
            continue

        # Normal operation
        op_name, _ = pick_operation()

        try:
            with conn.cursor() as cur:
                result = execute_operation(cur, op_name, ref, emp_id_counter)

                if result:
                    conn.commit()
                    stats.record(result)
                    stats.record("commit")

                    if not args.quiet:
                        ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
                        print(f"  [{ts}] {op_name:<20} -> COMMIT")
                else:
                    # Operation returned None (e.g. terminate with no rows)
                    conn.rollback()

        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            stats.record("error")
            if not args.quiet:
                print(f"  [ERROR] {op_name}: {e}")

        time.sleep(sleep_time)

    # Final summary
    stop_event.set()
    conn.close()
    print("\n" + "=" * 60)
    print("FINAL SUMMARY")
    print("=" * 60)
    print(stats.summary())
    print("Connection closed.")

# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Continuous DML workload generator for Oracle HR schema",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python hr_dml_generator.py
  python hr_dml_generator.py --tps 50 --duration 300
  python hr_dml_generator.py --host 10.0.0.5 --service ORCLCDB1 --quiet
        """
    )

    # Connection
    conn_group = parser.add_argument_group("Oracle connection")
    conn_group.add_argument("--host",     default=DEFAULT_HOST,     help=f"Oracle host (default: {DEFAULT_HOST})")
    conn_group.add_argument("--port",     default=DEFAULT_PORT,     type=int, help=f"Oracle port (default: {DEFAULT_PORT})")
    conn_group.add_argument("--service",  default=DEFAULT_SERVICE,  help=f"Oracle service name (default: {DEFAULT_SERVICE})")
    conn_group.add_argument("--user",     default=DEFAULT_USER,     help=f"Oracle username (default: {DEFAULT_USER})")
    conn_group.add_argument("--password", default=DEFAULT_PASSWORD, help=f"Oracle password (default: {DEFAULT_PASSWORD})")

    # Workload
    load_group = parser.add_argument_group("Workload")
    load_group.add_argument("--tps",      default=DEFAULT_TPS,      type=int, help=f"Target transactions per second (default: {DEFAULT_TPS})")
    load_group.add_argument("--duration", default=DEFAULT_DURATION, type=int, help="Run duration in seconds, 0 = forever (default: 0)")

    # Output
    out_group = parser.add_argument_group("Output")
    out_group.add_argument("--quiet", action="store_true", help="Suppress per-operation output, show only summary")

    args = parser.parse_args()
    run(args)

if __name__ == "__main__":
    main()
