import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(".")
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

GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
]

def ask_gemini(model):
    body = {
        "contents": [{"parts": [{"text": body_base["input"]}]}],
        "generationConfig": {
            "maxOutputTokens": body_base["max_output_tokens"],
            "thinkingConfig": {"thinkingLevel": "medium"},
            "responseFormat": {
                "text": {
                    "mimeType": "application/json",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "decision": {
                                "type": "string",
                                "enum": ["CHANGE", "NO_CHANGE"]
                            },
                            "summary": {"type": "string"},
                            "patch": {"type": "string"}
                        },
                        "required": ["decision", "summary", "patch"]
                    }
                }
            },
        },
    }

    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-goog-api-key": os.environ["GEMINI_API_KEY"].strip(),
            "Content-Type": "application/json",
        },
        method="POST",
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.load(response)

        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            print(f"Gemini {model} HTTP {e.code} attempt {attempt + 1}/3: {raw[:1200]}")

            # Quota exhausted for this model: immediately try another model.
            if e.code == 429:
                raise RuntimeError(f"Gemini {model} quota exhausted") from e

            # Temporary capacity/server failure: retry this model.
            if e.code in (500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (2 ** attempt))
                continue

            raise RuntimeError(
                f"Gemini {model} HTTP {e.code}: {raw[:2000]}"
            ) from e

    raise RuntimeError(f"Gemini {model} retry loop exhausted")

def ask_openrouter():
    body = {
        "model": "openrouter/free",
        "messages": [
            {
                "role": "user",
                "content": body_base["input"]
            }
        ],
        "max_tokens": body_base["max_output_tokens"],
    }

    req = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + os.environ["OPENROUTER_API_KEY"].strip(),
            "Content-Type": "application/json",
            "HTTP-Referer": "https://milanlife.in",
            "X-Title": "MILAN Live UI Update",
        },
        method="POST",
    )

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as response:
                return json.load(response)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            print(f"OpenRouter HTTP {e.code} attempt {attempt + 1}/3: {raw[:1200]}")
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (2 ** attempt))
                continue
            raise RuntimeError(f"OpenRouter HTTP {e.code}: {raw[:2000]}") from e

    raise RuntimeError("OpenRouter retry loop exhausted")

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

if os.environ.get("OPENROUTER_API_KEY"):
    try:
        print("Trying model: openrouter/free")
        data = ask_openrouter()
        print("OpenRouter model selected: openrouter/free")
    except Exception as e:
        print(f"OpenRouter unavailable: {type(e).__name__}: {e}")

if data is None and os.environ.get("GEMINI_API_KEY"):
    for gemini_model in GEMINI_MODELS:
        try:
            print(f"Trying model: {gemini_model}")
            data = ask_gemini(gemini_model)
            print(f"Gemini model selected: {gemini_model}")
            break
        except Exception as e:
            print(f"Gemini unavailable ({gemini_model}): {type(e).__name__}: {e}")

if data is None:
    print("AI unavailable this cycle; safely skipping code generation.")
    sys.exit(0)

if data is None:
    print("AI unavailable this cycle; safely skipping code generation.")
    sys.exit(0)

parts = []

for choice in data.get("choices", []):
    message = choice.get("message", {})
    content = message.get("content")
    if isinstance(content, str):
        parts.append(content)
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])

if isinstance(data.get("output_text"), str):
    parts.append(data["output_text"])

for item in data.get("output", []):
    for content in item.get("content", []):
        if isinstance(content, dict) and isinstance(content.get("text"), str):
            parts.append(content["text"])

for candidate in data.get("candidates", []):
    content = candidate.get("content", {})
    for part in content.get("parts", []):
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            parts.append(part["text"])

text = "\n".join(parts).strip()

summary = ""
patch = ""

try:
    json_text = text
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        json_text = fenced.group(1)
    payload = json.loads(json_text)

    if isinstance(payload, dict):
        decision = str(payload.get("decision", "")).strip().upper()
        summary = str(payload.get("summary", "")).strip()
        patch = str(payload.get("patch", "")).strip()

        if decision == "NO_CHANGE":
            print("NO_CHANGE")
            sys.exit(0)
except Exception:
    pass

if not summary or not patch:
    summary_match = re.search(r"<summary>\s*(.*?)\s*</summary>", text, re.S)
    summary = summary_match.group(1).strip() if summary_match else ""

    match = re.search(r"<patch>\s*(.*?)\s*</patch>", text, re.S)
    patch = match.group(1).strip() if match else ""

if not summary or summary == "Improve MILAN UI":
    print("AI did not provide a specific UI summary; skipping commit.")
    sys.exit(0)

if not patch:
    print("AI returned no valid patch; skipping safely.")
    sys.exit(0)

if patch.startswith("```") and patch.endswith("```"):
    patch = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", patch)
    patch = re.sub(r"\s*```$", "", patch).strip()

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
