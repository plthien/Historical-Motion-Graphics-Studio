#!/usr/bin/env python3
"""P0 donor file-ownership audit. Read-only on source; writes only the P0 report."""
from __future__ import annotations
import ast
import json
import re
import subprocess
from pathlib import Path
import argparse
import hashlib

ROOT = Path(__file__).resolve().parents[1]
DONOR_SHA = "0119572d651cb2b1902aea21b6267cca9809a940"
PREFIXES = ("app/", "src/", "contracts/", "alembic/", "tests/", "scripts/", "tools/", ".github/", "kokoro/")
ROOT_FILES = {
    "start.bat", "package.json", "package-lock.json", "requirements.txt",
    "alembic.ini", "pytest.ini", ".gitignore", ".npmrc", "postcss.config.js",
    "tailwind.config.js", "tsconfig.json", "vite.config.ts", "index.html",
    "00_START_HERE.md", "README.md",
}
EXTS = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
parser=argparse.ArgumentParser(description="Immutable donor SHA source ownership inventory")
parser.add_argument("--donor-sha", default=DONOR_SHA)
parser.add_argument("--output", type=Path, default=ROOT/"docs/history/source-ownership-ledger.json")
parser.add_argument("--verify", action="store_true")
parser.add_argument("--emit", action="store_true")
args=parser.parse_args()
sha=args.donor_sha
if not re.fullmatch(r"[0-9a-fA-F]{40}",sha):
    raise SystemExit(f"DONOR_SHA_INVALID: {sha!r}")
try:
    revision=subprocess.run(["git","cat-file","-e",sha+"^{commit}"],cwd=ROOT,capture_output=True,check=False)
    if revision.returncode != 0: raise RuntimeError("missing Git donor commit")
    tree=subprocess.check_output(["git","ls-tree","-r","--name-only",sha],cwd=ROOT,text=True).splitlines()
except (OSError,subprocess.CalledProcessError,RuntimeError) as exc:
    raise SystemExit(f"DONOR_SHA_UNAVAILABLE: {sha} ({exc})")
files = sorted(p for p in tree if p.startswith(PREFIXES) or p in ROOT_FILES)
if not files or not ROOT_FILES.intersection(files):
    raise SystemExit("DONOR_TREE_INCOMPLETE: required root files missing from donor tree")
def immutable_source(filename):
    try:
        return subprocess.check_output(["git","show",sha+":"+filename],cwd=ROOT).decode("utf-8",errors="replace")
    except (OSError,subprocess.CalledProcessError) as exc:
        raise SystemExit(f"DONOR_BLOB_UNREADABLE: {sha}:{filename} ({exc})")
current = set(files)
module_paths = {}
for p in files:
    pp = Path(p)
    if pp.suffix == ".py":
        mod = ".".join(pp.with_suffix("").parts)
        module_paths[mod] = p
        if pp.name == "__init__.py": module_paths[".".join(pp.parent.parts)] = p
def python_deps(filename: str, content: str) -> set[str]:
    result = set()
    try: tree = ast.parse(content)
    except SyntaxError: return result
    package = filename[:-3].replace("/", ".")
    if package.endswith(".__init__"): package = package[:-9]
    parts = package.split(".")
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [x.name for x in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                prefix = parts[:-node.level]
                mod = ".".join(prefix + ([node.module] if node.module else []))
            else: mod = node.module or ""
            names = [mod] + [mod + "." + x.name for x in node.names]
        for name in names:
            if name in module_paths: result.add(module_paths[name])
            else:
                candidate = name.split(".")
                for j in range(len(candidate)-1,0,-1):
                    short = ".".join(candidate[:j])
                    if short in module_paths: result.add(module_paths[short]);break
    return result
def relative_deps(filename: str, content: str) -> set[str]:
    result=set()
    for spec in re.findall(r"(?:from\s*|import\s*\(\s*)['\"](\.[^'\"]+)['\"]",content):
        for ext in (".tsx",".ts",".jsx",".js",".mjs",".cjs","/index.tsx","/index.ts"):
            dest=(Path(filename).parent / (spec+ext)).as_posix()
            parts=[]
            for q in dest.split("/"):
                if q == "..":
                    if parts: parts.pop()
                elif q != ".": parts.append(q)
            resolved="/".join(parts)
            if resolved in current: result.add(resolved);break
    return result
contents={}
direct={}
for p in files:
    if Path(p).suffix not in EXTS: continue
    # Frozen donor object, NEVER a file from the current PR working tree.
    contents[p]=immutable_source(p)
    direct[p]=sorted(python_deps(p,data) if p.endswith(".py") else relative_deps(p,data))

tests=[p for p in files if p.startswith("tests/")]
test_map={}
for p in files:
    if p.startswith("tests/"): test_map[p]=[];continue
    stem=Path(p).stem.lower()
    matched=[t for t in tests if p in direct.get(t,[]) or (
       len(stem)>6 and stem not in ("__init__","services","settings","contracts","models","client") and stem in Path(t).stem.lower()
    )]
    test_map[p]=sorted(set(matched))

def classify(p: str) -> tuple[str,str|None,str,str]:
    low=p.lower()
    if low.startswith("alembic/versions/"):
        return "DELETE",None,"Legacy SQLite migration revision","Remove the full old migration chain only during P1 atomic cut"
    if low.startswith("contracts/"):
        if any(s in low for s in ("schemas/v4/","prompts/v4/","youtube_ai_studio","scene","beat","prompt_bundle","image_generate")):
            return "DELETE",None,"Legacy YTB contract/prompt/schema","Remove dependent routes and generated API clients together"
        return "REWRITE","history.contracts","Legacy shared/core/platform contract","Replace with new strict History IDs and ownership"
    if low.startswith("app/modules/v4/"):
        if any(s in low for s in ("voice","elevenlabs","kokoro","windows_job","windows_subprocess","host_owned","execution_budget")):
            return "EXTRACT","history.voice / history.process","Old V4 voice/runtime utility","Extract neutral algorithm and retest provider/identity; never preserve V4 import"
        return "DELETE",None,"YTB V4 production stage/service","Transitive callers, route schemas and artifact heads must be removed in P1"
    if low.startswith("app/db/") or low.startswith("alembic/"):
        return "REWRITE","history.db","Old YTB SQLite/SQLAlchemy model or migration mechanism","Fresh History DB and migration head; never open or migrate legacy SQLite"
    if low.startswith("app/api/routes/"):
        if any(s in low for s in ("scene","visual_reference","v4","production","voice_pacing")):
            return "DELETE",None,"Old V4/scene/asset route","Unregister before start and remove old DTOs/API clients"
        return "REWRITE","history.api","YTB API route","New history-owned API replacing old project semantics"
    if low.startswith("app/modules/media/"):
        if any(s in low for s in ("durable_workflow","process","ffprobe")):
            return "EXTRACT","history.jobs / history.process","Generic durable process or media primitive","Detach YTB provider/task references and retest leases"
        return "DELETE",None,"Old media generation/provider pipeline","No per-beat still asset generation or legacy workers"
    if low.startswith("app/"):
        if any(s in low for s in ("hashing.py","external_authoring.py","windows","artifacts.py","durable","guard","repository/core","ids.py","time.py")):
            return "EXTRACT","history.core / history.jobs","Potential neutral core/persistence routine","Retain only individually extracted algorithms with new tests"
        return "REWRITE","history.api / history.core / history.jobs","Donor application backend","Replace YTB domain connections, routes, DTOs and runtime"
    if low.startswith("src/"):
        if any(s in low for s in ("cockpitshell","globalsidebar","pipeline stepper","pipelinestepper","pageheader","promptimportworkspace","components/ui/","theme/")):
            return "EXTRACT","history.ui","Reusable neutral Cockpit shell or prompt-import UI","Port isolated UX into new History stage ownership, not legacy queries"
        if any(s in low for s in ("sceneassets","scenebreakdown","assemblypanel","renderpanel","stylebiblevisual")):
            return "DELETE",None,"YTB frontend stage","Remove obsolete stage references, data hooks and UI routes"
        return "REWRITE","history.ui","Donor frontend stage/client/component","Rewire history-only seven stages and typed contracts"
    if low.startswith("kokoro/"):
        return "EXTRACT","history.voice","Donor Kokoro local runtime helper","Port only independent synthesis utilities after runtime calibration tests"
    if "/" not in p:
        if p in ("00_START_HERE.md","README.md"):
            return "DELETE",None,"Old YTB product entrypoint documentation","Replace with history-only entrypoints at P1"
        return "REWRITE","history.startup / history.build","Root startup/build/dependency configuration","Replace donor bootstrap and remove obsolete provider/process entries atomically"
    if low.startswith("tests/"):
        return "REWRITE","tests/history","Donor regression test","Keep only extracted neutral tests, replace YTB production tests"
    if low.startswith("scripts/") or low.startswith("tools/") or low.startswith(".github/"):
        return "REWRITE","history.devtools","Donor launcher, QA tool or CI","Replace obsolete workers/legacy runtime gates after P0"
    return "REWRITE","history","Other donor file","Audit before cutover"

def closure(p: str) -> list[str]:
    seen=set();stack=list(direct.get(p,()))
    while stack:
        q=stack.pop()
        if q == p or q in seen: continue
        seen.add(q)
        stack.extend(direct.get(q,[]))
    return sorted(seen)
def responsibility(p: str) -> str:
    desc=p.split("/")[-1].replace("_"," ").replace("-"," ").split(".")[0]
    if p.startswith("alembic/versions/"):return "YTB Alembic revision: " + desc
    if p.startswith("contracts/"):return "YTB API/prompt/validation contract: " + desc
    if p.startswith("src/"):return "YTB React component or browser utility: " + desc
    if p.startswith("tests/"):return "YTB regression/behavior test: " + desc
    return "Donor service/module/tool: " + desc

entries=[]
for p in files:
    decision,new_owner,task,risk=classify(p)
    deps=direct.get(p,[])
    extract=[d for d in deps if classify(d)[0]=="EXTRACT"]
    entries.append({"path":p,"currentResponsibility":responsibility(p),
      "decision":decision,"newHistoryOwner":new_owner,"dependencyExtractionPreconditions":extract,
      "directDependencies":deps,"transitiveDependencies":closure(p),
      "testsProtectingBehavior":test_map.get(p,[]),
      "requiredP1Test":"Rebuild independent History test" if not test_map.get(p) and not p.startswith("tests/") else "Port mapped tests; verify no legacy imports",
      "p1CleanCutRisk":risk, "p1Action":task})

tables=[]
for p,s in contents.items():
    if p.startswith("app/db/") and p.endswith(".py"):
        for klass,table in re.findall(r"class\s+(\w+)\s*\([^\n]+\)[^\n]*:\s*\n(?:.|\n)*?__tablename__\s*=\s*['\"]([^'\"]+)['\"]",s):
            tables.append({"owner":p,"class":klass,"table":table})
routes=[]
for p,s in contents.items():
    if p.startswith("app/api/routes/"):
        for method,path in re.findall(r"@\w+\.(get|post|put|patch|delete)\s*\(\s*['\"]([^'\"]+)['\"]",s):
            routes.append({"owner":p,"method":method.upper(),"declaredPath":path})
report={"schemaVersion":"history_source_ownership_ledger.v2",
 "donorSha":sha,"scope":"Tracked P0 donor files from exact main commit",
 "completeness":{"trackedFiles":len(files),"sourceAnalyzed":len(contents),
  "sourceSnapshot":"git show <immutable donor commit>:<file>; no working-tree reads",
  "dependencyGraph":"best-effort static import AST/regex; runtime/dynamic routes need independent P1 tests",
  "testsAssociation":"import/name heuristic, not independent runtime proof"},
 "legacyInventory":{"routes":routes,"dbTables":tables,
   "migrations":[p for p in files if p.startswith("alembic/versions/")],
   "uiStages":["Topic Ideas","Script & Analysis","Style Bible","Voice","Scene Breakdown","Scene Assets","Assembly","Render"],
   "workerLaunchers":[p for p in files if p.startswith("scripts/windows/")]},
 "p1Preconditions":["Prove transitive module removal with an import closure test",
  "Extract and retest clean JSON, hashing, CAS, voice, Windows job and durable worker utilities",
  "Replace API/UI/DB/routes and migrations atomically, with no alternate old YTB mode",
  "Prove old user DB/media remains untouched and is never imported or upgraded"],
 "entries":entries}
output=args.output
data=(json.dumps(report,indent=2,ensure_ascii=False)+"\n").encode("utf-8")
if args.verify:
    if not output.exists() or output.read_bytes()!=data:
        raise SystemExit("P0_LEDGER_OUTDATED: frozen donor inventory differs from checked-in report")
    print(f"P0_LEDGER_VERIFY_PASS paths={len(files)} sha256={hashlib.sha256(data).hexdigest()}")
else:
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_bytes(data)
    print(f"P0_LEDGER_PATHS={len(files)} TABLES={len(tables)} ROUTES={len(routes)} sha256={hashlib.sha256(data).hexdigest()}")
if args.emit:
    print("P0_LEDGER_BEGIN")
    print(json.dumps(report,separators=(",",":"),ensure_ascii=False))
    print("P0_LEDGER_END")
