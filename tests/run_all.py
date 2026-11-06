"""
Runs every test and reports a one-line result for each.

    python tests/run_all.py

The tests use fake local servers and throwaway folders: nothing touches the
real WildFind data, photos or the internet. Two packages are needed that the
app itself does not use:

    python -m pip install osmium pillow
"""
import os, sys, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
TESTS = ["test_extract.py", "test_kinds.py", "test_real_data.py", "test_retry.py", "test_photos.py", "test_end_to_end.py"]

failed = []
for t in TESTS:
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(HERE, t)], capture_output=True, text=True)
    lines = (r.stdout or "").splitlines()
    passes = sum(1 for l in lines if l.startswith("PASS"))
    fails = [l for l in lines if l.startswith("FAIL")]
    status = "ok  " if r.returncode == 0 else "FAIL"
    print("%s  %-22s %2d passed, %d failed  (%.0fs)" % (status, t, passes, len(fails), time.time() - t0))
    for l in fails:
        print("        " + l)
    if r.returncode != 0:
        failed.append(t)
        if not fails:
            print("        " + "\n        ".join((r.stderr or r.stdout).strip().splitlines()[-8:]))

print()
print("all tests passed" if not failed else "%d of %d test files failed" % (len(failed), len(TESTS)))
sys.exit(1 if failed else 0)
