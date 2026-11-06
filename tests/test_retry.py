"""
fetch.py restarts the photo stage by itself when it stops (a lost connection
or a crash), so a run left overnight keeps going without anyone there. Uses a
stand-in photos.py that fails a set number of times, so it runs in seconds.

    python tests/test_retry.py             (no extra packages needed)
"""
import os, sys, shutil, subprocess, tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILS = []
def ok(label, cond, extra=""):
    if not cond: FAILS.append(label)
    print(("PASS  " if cond else "FAIL  ") + label + (("   " + str(extra)) if extra else ""))

STUB = r'''
import os, sys
state = sys.argv[1]
if "--needs-dump" in sys.argv:
    print("no"); sys.exit(0)
n_path = os.path.join(state, "runs.txt")
n = int(open(n_path).read()) + 1 if os.path.exists(n_path) else 1
open(n_path, "w").write(str(n))
fail_until = int(os.environ.get("STUB_FAIL_RUNS", "0"))
if n <= fail_until:
    if n == 1:
        raise RuntimeError("a crash")      # an unexpected error: exit code 1
    sys.exit(3)                            # the connection was lost
print("stub photo stage finished")
'''

def project(fail_runs):
    d = tempfile.mkdtemp(prefix="wildfind-retry-")
    os.makedirs(os.path.join(d, "tools", "state")); os.makedirs(os.path.join(d, "data"))
    shutil.copy(os.path.join(ROOT, "tools", "fetch.py"), os.path.join(d, "tools", "fetch.py"))
    open(os.path.join(d, "tools", "photos.py"), "w").write(STUB)
    open(os.path.join(d, "tools", "state", "sites.tsv"), "w").write("n1\tA\t51.0\t-1.0\truins\t\n")
    env = {**os.environ, "STUB_FAIL_RUNS": str(fail_runs)}
    return d, env

def runs(d):
    p = os.path.join(d, "tools", "state", "runs.txt")
    return int(open(p).read()) if os.path.exists(p) else 0

# fails twice (a crash, then a lost connection), then finishes
d, env = project(2)
r = subprocess.run([sys.executable, os.path.join(d, "tools", "fetch.py"), d, "--stage", "photos",
                    "--retry-wait", "0.2"], capture_output=True, text=True, env=env, timeout=120)
ok("photo stage restarted after a crash and after a lost connection", runs(d) == 3, "%d runs" % runs(d))
ok("then finished normally", r.returncode == 0 and "Done." in r.stdout, r.returncode)
ok("says it is trying again, and how to stop it", r.stdout.count("Trying again") == 2 and "Close this window" in r.stdout)
shutil.rmtree(d, ignore_errors=True)

# never recovers: gives up after the allowed retries and says how to carry on
d, env = project(99)
r = subprocess.run([sys.executable, os.path.join(d, "tools", "fetch.py"), d, "--stage", "photos",
                    "--retry-wait", "0.1", "--retries", "3"], capture_output=True, text=True, env=env, timeout=120)
ok("gives up after the allowed number of retries", runs(d) == 4, "%d runs" % runs(d))
ok("and says so, with a non-zero exit code", r.returncode == 3 and "has not finished" in r.stdout, r.returncode)
shutil.rmtree(d, ignore_errors=True)

sys.exit(1 if FAILS else 0)
