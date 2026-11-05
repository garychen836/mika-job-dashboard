#!/usr/bin/env bash
# Every check, offline, no model, no keys: syntax, tests, the page's JavaScript, a secret scan, and the red team on a
# throwaway server. Ends with "RESULT: ALL CHECKS PASSED". Run: bash check.sh   (PY=python3.13 bash check.sh to pick one)
set -u
cd "$(dirname "$0")"
PY="${PY:-python3}"
fail=0
run() { local name="$1"; shift; if "$@" >/tmp/mjd_check.log 2>&1; then echo "PASS  $name"; else echo "FAIL  $name"; tail -15 /tmp/mjd_check.log; fail=1; fi; }
run "python compiles" "$PY" -m py_compile backend/*.py elm/*.py
run "no undefined names (copied code)" "$PY" scripts/tools/undefined_names.py backend/*.py elm/*.py
for t in test_entry test_no_cdo test_app test_agent_path test_events test_module_names test_upload_size; do run "test $t" "$PY" "elm/tests/$t.py"; done
page_js() { "$PY" - <<'PYX'
import re, pathlib, subprocess, sys
for f in ("elm/static/index.html", "elm/static/stats.html"):
    js = "\n".join(re.findall(r"<script>(.*?)</script>", pathlib.Path(f).read_text(), re.S))
    pathlib.Path("/tmp/mjd_page.js").write_text(js)
    r = subprocess.run(["node", "--check", "/tmp/mjd_page.js"], capture_output=True, text=True)
    if r.returncode:
        print(f, r.stderr); sys.exit(1)
PYX
}
run "page JavaScript syntax" page_js
run "ranking, interests, CSV (node)" node elm/tests/test_rank.mjs
secrets() { ! git ls-files -z 2>/dev/null | xargs -0 grep -nE "sk-or-v1-[A-Za-z0-9]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{40,}|postgres(ql)?://[^ \"']+:[^ \"'@]+@|ghp_[A-Za-z0-9]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY" ; }
run "no secrets in tracked files" secrets
red_team() {
  local port="${RT_PORT:-8279}" d rc=0; d="$(mktemp -d)"
  "$PY" scripts/tools/elm_redteam_data.py "$d" >/dev/null || { rm -rf "$d"; return 1; }
  env -u OPENROUTER_API_KEY -u ELM_CHAT_MODEL -u ELM_DATABASE_URL -u ELM_UPLOAD_SECRET ELM_DATA_DIR="$d" \
      "$PY" -m uvicorn --app-dir elm app:app --port "$port" >"$d/server.log" 2>&1 &
  local pid=$!
  for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$port/healthz" >/dev/null 2>&1 && break; sleep 1; done
  local out; out=$("$PY" scripts/redteam_elm.py "http://127.0.0.1:$port" "$d" 2>&1) || rc=1
  echo "$out" | grep -E "^BROKE|broke," 
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; rm -rf "$d"; return $rc
}
run "red team (hostile roles, limits, privacy, paths; no keys)" red_team
[ $fail -eq 0 ] && echo "RESULT: ALL CHECKS PASSED" || { echo "RESULT: SOME CHECKS FAILED"; exit 1; }
