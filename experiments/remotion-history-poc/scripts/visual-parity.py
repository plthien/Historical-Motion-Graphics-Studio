#!/usr/bin/env python3
"""Compare native 1920x1080 Player pixels to decoded MP4 exact-frame pixels.
Only expected H.264/font AA differences are tolerated. Fail on global/color-region drift.
"""
from __future__ import annotations
import json
from pathlib import Path
import subprocess
from PIL import Image, ImageChops, ImageStat

ROOT=Path(__file__).resolve().parents[1]
VIDEO=ROOT/"out/history-poc.mp4"
NATIVE=ROOT/"out/player-native"
OUT=ROOT/"out/parity"
FRAMES=[150,570,750,850,900,1000,1050,1100,1200,1350,1799]
REGIONS={150:(115,275,1575,525),750:(105,240,735,490),1350:(1000,275,1750,510)}
REPORT={"testedFrames":FRAMES,"results":[],"source":"Remotion @remotion/player browser screenshot vs decoded H264 exact frame"}
OUT.mkdir(parents=True,exist_ok=True)
for frame in FRAMES:
    name=f"frame-{frame:04d}.png"
    player_path=NATIVE/name
    if not player_path.is_file():raise RuntimeError(f"Missing Player frame {player_path}")
    video_path=OUT/name
    command=["ffmpeg","-v","error","-y","-i",str(VIDEO),
             "-vf",f"select=eq(n\\,{frame})","-vsync","0","-frames:v","1",str(video_path)]
    subprocess.run(command,check=True)
    with Image.open(player_path) as opened:p=opened.convert("RGB")
    with Image.open(video_path) as opened:v=opened.convert("RGB")
    if p.size!=(1920,1080) or v.size!=(1920,1080):raise RuntimeError(f"Invalid native parity dimensions at {frame}: {p.size} / {v.size}")
    diff=ImageChops.difference(p,v)
    mae=sum(ImageStat.Stat(diff).mean)/3
    region_mae=None
    if frame in REGIONS:
        crop=REGIONS[frame]
        region_mae=sum(ImageStat.Stat(diff.crop(crop)).mean)/3
    status="PASS" if mae<=16 and (region_mae is None or region_mae<=32) else "FAIL"
    REPORT["results"].append({"frame":frame,"meanAbsoluteRgbDifference":round(mae,3),
          "headlineRegionDifference":None if region_mae is None else round(region_mae,3),"status":status})
    if status!="PASS":print("PARITY_FAIL",frame,mae,region_mae)
(OUT/"report.json").write_text(json.dumps(REPORT,indent=2)+"\n",encoding="utf-8")
print("P0_PLAYER_MP4_PARITY "+json.dumps(REPORT))
if any(x["status"]!="PASS" for x in REPORT["results"]):raise SystemExit(1)
