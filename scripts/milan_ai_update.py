import json, os, re, subprocess, sys, urllib.request, urllib.error
from pathlib import Path

ROOT = Path("milan")
ALLOWED = re.compile(r"^frontend/.*\.(html|css|js)$", re.I)

def run(*cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)

def source_snapshot():
    files = []
    for p in sorted(ROOT.joinpath("frontend").rglob("*")):
        if p.is_file() and ALLOWED.match(str(p.relative_to(ROOT))):
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
                if len(text) <= 30000:
                    files.append(f"\n===== {p.relative_to(ROOT)} =====\n{text[:8000]}")
            except Exception:
                pass
    return "".join(files)[:70000]

prompt = """You are the autonomous UI engineer for the MILAN production web app.
Repository: Ignite-boy/MILAN
Goal: continuously improve the real user-facing frontend.

Rules:
1. Make ONE small, meaningful, safe UI/UX improvement per cycle.
2. Only modify files under frontend/ ending in .html, .css, or .js.
3. Never modify backend, authentication, database, DWN, deployment config, secrets, package files, or tests.
4. Preserve existing functionality and APIs.
5. Do not make cosmetic noise or pointless rewrites.
6. Avoid huge refactors.
7. If no safe meaningful improvement is available, return exactly NO_CHANGE.
8. Return ONLY a unified git diff inside:
<patch>
...
</patch>
9. The patch must apply cleanly to the current files.
"""

body = {
    "model": os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
    "input": prompt + "\n\nCURRENT MILAN FRONTEND:\n" + source_snapshot(),
    "max_output_tokens": 12000,
}

req = urllib.request.Request(
    "https://api.openai.com/v1/responses",
    data=json.dumps(body).encode(),
    headers={
        "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
        "Content-Type": "application/json",
    },
    method="POST",
)

import time
data = None
for attempt in range(6):
    try:
        with urllib.request.urlopen(req, timeout=180) as r:
            data = json.load(r)
        break
    except urllib.error.HTTPError as e:
        if e.code != 429 or attempt == 5:
            raise
        retry_after = e.headers.get("Retry-After")
        try:
            delay = max(15, min(180, int(retry_after)))
        except (TypeError, ValueError):
            delay = min(180, 15 * (2 ** attempt))
        print(f"OpenAI rate limited; retry {attempt + 1}/5 in {delay}s")
        time.sleep(delay)

if data is None:
    raise RuntimeError("OpenAI request returned no data")


chunks = []
for item in data.get("output", []):
    for c in item.get("content", []):
        if isinstance(c, dict) and isinstance(c.get("text"), str):
            chunks.append(c["text"])

text = "\n".join(chunks).strip()

if text == "NO_CHANGE":
    print("NO_CHANGE")
    sys.exit(0)

m = re.search(r"<patch>\s*(.*?)\s*</patch>", text, re.S)
if not m:
    print("AI did not return a valid patch.")
    sys.exit(2)

patch = m.group(1).strip()
if not patch:
    print("Empty patch.")
    sys.exit(3)

Path("/tmp/milan-ai.patch").write_text(patch + "\n", encoding="utf-8")

check = subprocess.run(
    ["git", "apply", "--check", "/tmp/milan-ai.patch"],
    cwd=ROOT, text=True, capture_output=True
)
if check.returncode != 0:
    print(check.stdout)
    print(check.stderr)
    sys.exit(4)

subprocess.run(["git", "apply", "/tmp/milan-ai.patch"], cwd=ROOT, check=True)

changed = subprocess.check_output(
    ["git", "diff", "--name-only"], cwd=ROOT, text=True
).splitlines()

bad = [x for x in changed if not ALLOWED.match(x)]
if bad:
    print("Blocked unsafe files:", bad)
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=ROOT)
    sys.exit(5)

print("AI PATCH APPLIED:")
print("\n".join(changed))
