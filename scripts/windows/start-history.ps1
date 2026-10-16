param([switch]$CheckOnly, [switch]$Smoke)
$ErrorActionPreference='Stop'
Set-Location (Resolve-Path (Join-Path $PSScriptRoot '..\..'))
$root=(Get-Location).Path
$python=Join-Path $root '.venv\Scripts\python.exe'
$backend=$null
$frontend=$null
function CheckTools {
 if(-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)){throw 'NODE_NPM_MISSING: install Node 22 with npm'}
 if(!(Test-Path $python)){
   if(Get-Command py.exe -ErrorAction SilentlyContinue){& py -3.12 -m venv .venv}
   elseif(Get-Command python.exe -ErrorAction SilentlyContinue){& python -m venv .venv}
   else{throw 'PYTHON_312_MISSING'}
   if($LASTEXITCODE -ne 0){throw 'HISTORY_VENV_BOOTSTRAP_FAILED'}
 }
 & $python -c "import sys; assert sys.version_info[:2]==(3,12), 'Python 3.12 required'"
 if($LASTEXITCODE -ne 0){throw 'PYTHON_312_REQUIRED'}
}
function InstallDeps {
 & $python -m pip install --disable-pip-version-check -r requirements.txt
 if($LASTEXITCODE -ne 0){throw 'HISTORY_PYTHON_INSTALL_FAILED'}
 & npm.cmd ci --no-audit --no-fund
 if($LASTEXITCODE -ne 0){throw 'HISTORY_NODE_INSTALL_FAILED'}
 & $python -c "from app.history.db import HistoryDB; HistoryDB().bootstrap()"
 if($LASTEXITCODE -ne 0){throw 'HISTORY_DATABASE_UNSUPPORTED_OR_FAILED'}
}
function StartApi {
 $p=Start-Process -FilePath $python -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000') -WorkingDirectory $root -PassThru -NoNewWindow
 for($i=0;$i -lt 60;$i++){
   Start-Sleep -Milliseconds 500
   if($p.HasExited){throw 'HISTORY_BACKEND_EARLY_EXIT'}
   try{$health=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 2
     if($health.product -eq 'historical-motion-graphics-studio'){return $p}
   }catch{}
 }
 throw 'HISTORY_BACKEND_NOT_READY'
}
function StopOwned($process) {
 if($null -ne $process){
   & taskkill.exe /PID $process.Id /T /F 2>$null | Out-Null
 }
}
try {
 CheckTools
 if($CheckOnly){Write-Host 'HISTORY_WINDOWS_PREREQUISITES_PASS';exit 0}
 InstallDeps
 $backend=StartApi
 if($Smoke){
   $uid=[guid]::NewGuid().ToString('N').Substring(0,12)
   $channel=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/history/channels' -Method Post -ContentType 'application/json' -Body (@{name="Windows P1 $uid";language='English';niche='History';history_dna=@{}}|ConvertTo-Json)
   $project=Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/history/projects' -Method Post -ContentType 'application/json' -Body (@{name="Restart $uid";channel_id=$channel.channel_id;history_context=@{}}|ConvertTo-Json)
   StopOwned $backend;$backend=$null
   Start-Sleep -Milliseconds 500
   $backend=StartApi
   $reopened=Invoke-RestMethod -Uri ("http://127.0.0.1:8000/api/history/projects/"+$project.project_id) -TimeoutSec 5
   if($reopened.project_id -ne $project.project_id){throw 'HISTORY_PROJECT_RESTART_FAILED'}
   Write-Host 'HISTORY_WINDOWS_START_RESTART_PASS'
   exit 0
 }
 $frontend=Start-Process -FilePath 'cmd.exe' -ArgumentList @('/c','npm','run','dev') -PassThru -WorkingDirectory $root -NoNewWindow
 for($i=0;$i -lt 60;$i++){
   Start-Sleep -Milliseconds 500
   if($frontend.HasExited){throw 'HISTORY_FRONTEND_EARLY_EXIT'}
   try{$response=Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:5173/' -TimeoutSec 2
       if($response.StatusCode -eq 200){break}}catch{}
 }
 Start-Process 'http://127.0.0.1:5173/'
 Write-Host 'History Cockpit active. Press ENTER to stop owned API/UI processes.'
 [void][Console]::ReadLine()
}finally{
 StopOwned $frontend
 StopOwned $backend
 Write-Host 'History-owned processes stopped.'
}
