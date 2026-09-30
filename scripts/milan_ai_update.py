import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("milan")
ALLOWED = re.compile(r"^frontend/.*\.(html|css|js)$", re.I)

def run(*cmd, cwd=None, check=True):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, check=check)

def snapshot():
    files = []
    frontend = ROOT / "frontend"
    priority = [
        frontend / "app.html",
        frontend / "assets/js/app-page-actions.js",
        frontend / "assets/js/milan-ui-actions.js",
        frontend / "assets/css/app.css",
        frontend / "assets/css/app-layout.css",
        frontend / "assets/css/milan-visual-engine.css",
    ]
    seen = set()

    for p in priority:
        if p.is_file():
            seen.add(p)
            text = p.read_text(encoding="utf-8", errors="ignore")
            files.append(f"\n===== {p.relative_to(ROOT)} =====\n{text[:12000]}")

    for p in sorted(frontend.rglob("*")):
        if p in seen or not p.is_file():
            continue
        rel = str(p.relative_to(ROOT))
        if not ALLOWED.match(rel):
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        files.append(f"\n===== {rel} =====\n{text[:5000]}")
        if sum(len(x) for x in files) > 85000:
            break

    return "".join(files)[:85000]

PROMPT = """You are the autonomous UI engineer for MILAN.
Repository: Ignite-boy/MILAN
Only improve the production frontend.

Rules:
- Make ONE small, meaningful UI/UX improvement per cycle.
- Only modify files under frontend/ ending in .html, .css, or .js.
- Never modify backend, auth, DWN, database, secrets, package files, Vercel config, or infrastructure.
- Preserve all existing APIs and functionality.
- No giant refactors, rewrites, or formatting-only changes.
- Prefer targeted UX, accessibility, responsiveness, visual polish, performance, or interaction improvements.
- Before choosing the change, inspect the current frontend and identify the next concrete user-facing improvement opportunity.
- Inspect recent git history/diff context when available so you do not repeat a recently completed improvement.
- Prefer a different UI area each cycle when a safe opportunity exists: buttons, typography, spacing, cards, forms, navigation, responsive behavior, empty states, loading states, accessibility, visual hierarchy, or interaction feedback.
- Prioritize real user-facing improvements over arbitrary cosmetic changes.
- Do not repeatedly change the same selector, component, text, or spacing unless it still clearly needs improvement.
- If no safe meaningful improvement exists, return exactly NO_CHANGE.
- Return a short human-readable change summary in <summary>...</summary>.
- Then return the unified git diff enclosed in <patch>...</patch>.
- The summary must describe exactly what UI/UX was improved, e.g. "Improve publish button spacing".
- Do not return NO_CHANGE unless a safe UI change is genuinely impossible.
- The patch must apply cleanly to the current files.
"""

body_base = {
    "input": PROMPT + "\n\nCURRENT MILAN FRONTEND:\n" + snapshot(),
    "max_output_tokens": 6000,
}

models = [
    os.getenv("OPENAI_MODEL", "gpt-5.6-luna"),
    "gpt-5.3-codex",
]

def ask(model):
    body = dict(body_base)
    body["model"] = model

    req = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            print(f"OPENAI_HTTP_{e.code}: {raw[:800]}")
            if e.code != 429 or attempt == 1:
                raise
            time.sleep(20)

    raise RuntimeError("OpenAI request failed")

data = None

for model in models:
    try:
        print(f"Trying model: {model}")
        data = ask(model)
        break
    except Exception as e:
        print(f"Model unavailable: {type(e).__name__}")
        continue

if data is None:
    print("AI unavailable this cycle; safely skipping code generation.")
    sys.exit(0)

parts = []

if isinstance(data.get("output_text"), str):
    parts.append(data["output_text"])

for item in data.get("output", []):
    for content in item.get("content", []):
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            parts.append(content["text"])

text = "\n".join(parts).strip()

if text == "NO_CHANGE":
    print("NO_CHANGE")
    sys.exit(0)

summary_match = re.search(r"<summary>\s*(.*?)\s*</summary>", text, re.S)
summary = summary_match.group(1).strip() if summary_match else "Improve MILAN UI"

match = re.search(r"<patch>\s*(.*?)\s*</patch>", text, re.S)
if not match:
    print("AI returned no valid patch; skipping safely.")
    sys.exit(0)

patch = match.group(1).strip()
if not patch:
    print("Empty patch; skipping safely.")
    sys.exit(0)

patch_file = Path("/tmp/milan-ai.patch")
patch_file.write_text(patch + "\n", encoding="utf-8")

check = subprocess.run(
    ["git", "apply", "--check", str(patch_file)],
    cwd=ROOT,
    text=True,
    capture_output=True,
)

if check.returncode != 0:
    print("Patch rejected by git --check; skipping.")
    print(check.stderr[:1500])
    sys.exit(0)

subprocess.run(["git", "apply", str(patch_file)], cwd=ROOT, check=True)

changed = subprocess.check_output(
    ["git", "diff", "--name-only"],
    cwd=ROOT,
    text=True,
).splitlines()

bad = [x for x in changed if not ALLOWED.match(x)]
if bad:
    print("Unsafe files detected; reverting AI patch:", bad)
    subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=ROOT, check=True)
    sys.exit(0)

if not changed:
    print("NO_CHANGE")
    sys.exit(0)

Path("/tmp/milan-ai-summary").write_text(summary[:180], encoding="utf-8")
print("AI CHANGE:", summary)
print("AI PATCH APPLIED:")
print("\n".join(changed))
