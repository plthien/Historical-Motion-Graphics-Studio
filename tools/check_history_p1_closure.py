#!/usr/bin/env python3
"""Strict P1 source/runtime closure; exclude P0 experiment and historical evidence docs."""
from __future__ import annotations
import ast
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ALLOWED_APP={"app/main.py","app/__init__.py"}
ALLOWED_TS={"src/App.tsx","src/main.tsx","src/style.css","src/features/history/api.ts"}
RETIRED=("app/modules/","app/db/","app/repositories/","app/api/","src/features/studio/","src/pages/",
         "src/api/","src/layout/","src/components/","contracts/schemas/","contracts/prompts/",
         "kokoro/","scripts/windows/start_durable_")
FORBIDDEN=( "app.modules.v4","app.modules.platform","app.render_coordinator_runner",
            "youtube_ai_studio","scene_asset","scene_breakdown","visual_reference",
            "prompt_bundle","ffmpeg_slideshow","backend_worker")
errors=[]
def fail(s):errors.append(s)
for folder in ("app","src","contracts","tests"):
 for p in (ROOT/folder).rglob("*"):
  if not p.is_file() or "__pycache__" in p.parts or p.suffix==".pyc":continue
  rel=p.relative_to(ROOT).as_posix()
  if any(rel.startswith(prefix) for prefix in RETIRED):fail("Legacy path "+rel)
  if folder=="app" and not (rel in ALLOWED_APP or rel.startswith("app/history/")):fail("Non-History Python "+rel)
  if folder=="src" and not (rel in ALLOWED_TS or rel.startswith("src/features/history/")):fail("Non-History UI "+rel)
  if folder=="contracts" and not rel.startswith("contracts/history/"):fail("Non-History contract "+rel)
  if folder=="tests" and not (rel.startswith("tests/history/") or rel.startswith("tests/history-ui/")):fail("Legacy test "+rel)
for p in (ROOT/"app").rglob("*.py"):
 try:tree=ast.parse(p.read_text(encoding="utf-8"))
 except SyntaxError as e:fail("Python syntax "+str(p)+": "+str(e));continue
 for node in ast.walk(tree):
  if isinstance(node,ast.Import): names=[x.name for x in node.names]
  elif isinstance(node,ast.ImportFrom): names=[node.module or ""]
  else:continue
  if any(n.startswith(("app.modules","app.api","app.services","app.db","app.repositories")) for n in names):
   fail("Legacy import "+p.relative_to(ROOT).as_posix()+": "+str(names))
  if any(token in n.lower() for token in ("openai","anthropic","ollama","langchain","gemini") for n in names):fail("Model SDK import "+str(names))
migrations=list((ROOT/"alembic/versions").glob("*.py"))
if sorted(p.name for p in migrations)!=["001_history_initial.py","002_history_authoring_p2.py"]:fail("Old migration chain present")
for entry in ("MANIFEST.json","docs/32_CODING_RULES.md","docs/99_SOURCE_OF_TRUTH.md",
              ".github/workflows/scene-assets-v2-candidate-audit.yml","tools/package_manifest.py"):
 if (ROOT/entry).exists():fail("Forbidden file "+entry)
for config in ("requirements.txt","package.json"):
 content=(ROOT/config).read_text(encoding="utf-8").lower()
 for token in ("openai","anthropic","ollama","langchain","gemini","youtube-ai"):
  if token in content:fail("Forbidden config dependency "+config+":"+token)
src=(ROOT/"src/features/history/api.ts").read_text(encoding="utf-8")
import re
keys=re.findall(r'key:"([^"]+)",label:',src)
expected=["topic","script_analysis","voice","assets","visual_director","motion_timeline","final_video"]
if keys!=expected:fail("History UI stage IDs mismatch")
if errors:
 for e in errors:print("P1_CLOSURE_FAILED "+e,file=sys.stderr)
 raise SystemExit(1)
print("P1_SOURCE_CLOSURE_PASS: History app/ui/contracts/tests/migration only; old YTB runtime absent")
