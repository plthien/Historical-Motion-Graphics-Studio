Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$KokoroDir = Split-Path -Parent $PSCommandPath
$RepoRoot = (Resolve-Path (Join-Path $KokoroDir ".." )).Path
$VenvDir = Join-Path $KokoroDir ".venv"
$VenvPython = Join-Path $VenvDir "Scripts\python.exe"
$RequirementsPath = Join-Path $KokoroDir "requirements.txt"
$RequirementsStamp = Join-Path $VenvDir ".yas3_kokoro_requirements.sha256"
$ReadyMarkerPath = Join-Path $KokoroDir ".yas3_kokoro_ready.json"
$CliPath = Join-Path $KokoroDir "kokoro_cli.py"
$RuntimeResponseSourcePath = Join-Path $KokoroDir "runtime_response_calibration.py"

$ModelName = "kokoro-v1.0.onnx"
$VoicesName = "voices-v1.0.bin"
$ModelPath = Join-Path $KokoroDir $ModelName
$VoicesPath = Join-Path $KokoroDir $VoicesName
$ModelUrl = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx"
$VoicesUrl = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin"
$ModelSha256 = "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5"
$VoicesSha256 = "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"

foreach ($Name in @("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONUSERBASE")) {
  Remove-Item "Env:$Name" -ErrorAction SilentlyContinue
}
$env:PYTHONNOUSERSITE = "1"

function Write-Step([string]$Message) {
  Write-Host "[KOKORO] $Message"
}

function Get-Sha256([string]$Path) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    return $null
  }
  return (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
}

function Invoke-Checked([string]$Exe, [string[]]$Arguments) {
  & $Exe @Arguments
  if ($LASTEXITCODE -ne 0) {
    throw "Command failed with exit code ${LASTEXITCODE}: $Exe $($Arguments -join ' ')"
  }
}

function Remove-ReadyMarker {
  if (Test-Path -LiteralPath $ReadyMarkerPath -PathType Leaf) {
    Remove-Item -LiteralPath $ReadyMarkerPath -Force
  }
}

function Find-Python312 {
  $Check = "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') * 8 == 64 else 1)"
  $Candidates = @(
    @{ Exe = "py.exe"; Args = @("-3.12") },
    @{ Exe = "python.exe"; Args = @() }
  )
  foreach ($Candidate in $Candidates) {
    $Command = Get-Command $Candidate.Exe -ErrorAction SilentlyContinue
    if ($null -eq $Command) {
      continue
    }
    & $Command.Source @($Candidate.Args + @("-c", $Check)) *> $null
    if ($LASTEXITCODE -eq 0) {
      return @($Command.Source) + $Candidate.Args
    }
  }
  throw "Python 3.12 x64 was not found. Install it, reopen this folder, then run kokoro\setup_kokoro.bat again. Suggested: winget install -e --id Python.Python.3.12"
}

function Ensure-Venv {
  if (Test-Path -LiteralPath $VenvPython -PathType Leaf) {
    & $VenvPython -c "import struct, sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) and struct.calcsize('P') * 8 == 64 else 1)" *> $null
    if ($LASTEXITCODE -eq 0) {
      Write-Step "Using existing isolated Kokoro venv."
      return
    }
    throw "Existing kokoro\.venv is not Python 3.12 x64. Move or delete kokoro\.venv, then rerun setup."
  }
  Write-Step "Creating isolated Kokoro Python 3.12 venv."
  $Python = Find-Python312
  $Exe = $Python[0]
  $Args = @()
  if ($Python.Count -gt 1) {
    $Args = $Python[1..($Python.Count - 1)]
  }
  Invoke-Checked $Exe ($Args + @("-m", "venv", $VenvDir))
  if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
    throw "kokoro\.venv was created without Scripts\python.exe."
  }
}

function Ensure-Dependencies {
  if (-not (Test-Path -LiteralPath $RequirementsPath -PathType Leaf)) {
    throw "Missing kokoro\requirements.txt."
  }
  $ExpectedHash = Get-Sha256 $RequirementsPath
  $InstalledHash = if (Test-Path -LiteralPath $RequirementsStamp -PathType Leaf) { (Get-Content -LiteralPath $RequirementsStamp -TotalCount 1).Trim() } else { "" }
  $ImportsReady = $false
  if ($InstalledHash -eq $ExpectedHash) {
    & $VenvPython -c "import kokoro_onnx, onnxruntime" *> $null
    $ImportsReady = ($LASTEXITCODE -eq 0)
  }
  if ($ImportsReady) {
    Write-Step "Kokoro Python dependencies are current."
    return
  }
  Write-Step "Installing isolated Kokoro Python dependencies."
  Invoke-Checked $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "pip", "setuptools", "wheel")
  Invoke-Checked $VenvPython @("-m", "pip", "install", "--disable-pip-version-check", "-r", $RequirementsPath)
  Invoke-Checked $VenvPython @("-c", "import kokoro_onnx, onnxruntime")
  Set-Content -LiteralPath $RequirementsStamp -Value $ExpectedHash -Encoding ASCII
}

function Ensure-ModelFile([string]$Name, [string]$Path, [string]$Url, [string]$ExpectedSha256) {
  $ExistingHash = Get-Sha256 $Path
  if ($ExistingHash -eq $ExpectedSha256) {
    Write-Step "$Name is present."
    return
  }
  if ($null -ne $ExistingHash) {
    Write-Step "$Name exists but hash is not expected; downloading a verified replacement."
  } else {
    Write-Step "Downloading $Name."
  }
  $TempPath = "$Path.download-$([Guid]::NewGuid().ToString('N')).tmp"
  try {
    Invoke-WebRequest -Uri $Url -OutFile $TempPath -UseBasicParsing
    $DownloadedHash = Get-Sha256 $TempPath
    if ($DownloadedHash -ne $ExpectedSha256) {
      throw "$Name download hash mismatch. Expected $ExpectedSha256, got $DownloadedHash."
    }
    Move-Item -LiteralPath $TempPath -Destination $Path -Force
  } finally {
    if (Test-Path -LiteralPath $TempPath) {
      Remove-Item -LiteralPath $TempPath -Force
    }
  }
}

function Invoke-SmokeTest {
  Write-Step "Running real Kokoro synthesis smoke test."
  if (-not (Test-Path -LiteralPath $CliPath -PathType Leaf)) {
    throw "Missing kokoro\kokoro_cli.py."
  }
  $SmokeId = [Guid]::NewGuid().ToString("N")
  $SmokeInput = Join-Path ([IO.Path]::GetTempPath()) "yas3_kokoro_smoke_$SmokeId.txt"
  $SmokeOutput = Join-Path ([IO.Path]::GetTempPath()) "yas3_kokoro_smoke_$SmokeId.wav"
  try {
    [IO.File]::WriteAllText($SmokeInput, "Hello from YouTube AI Studio Kokoro.", [Text.UTF8Encoding]::new($false))
    Invoke-Checked $VenvPython @(
      $CliPath,
      "--input-file", $SmokeInput,
      "--output-file", $SmokeOutput,
      "--voice", "af_heart",
      "--language", "en",
      "--speaking-rate", "1.0",
      "--sample-rate", "48000",
      "--output-format", "wav_pcm_s16le"
    )
    $Validate = "import sys, wave; p=sys.argv[1]; w=wave.open(p, 'rb'); ok=(w.getnchannels()==1 and w.getsampwidth()==2 and w.getframerate()==48000 and w.getnframes()>0 and w.getcomptype()=='NONE'); w.close(); raise SystemExit(0 if ok else 1)"
    Invoke-Checked $VenvPython @("-c", $Validate, $SmokeOutput)
    Write-Step "Running Kokoro voice catalog discovery smoke test."
    $CatalogJson = (& $VenvPython $CliPath "--list-voices" "--format" "json")
    if ($LASTEXITCODE -ne 0) {
      throw "Kokoro voice catalog discovery failed."
    }
    $Catalog = $CatalogJson | ConvertFrom-Json
    if ($null -eq $Catalog.voices -or $Catalog.voices.Count -lt 1) {
      throw "Kokoro voice catalog discovery returned no voices."
    }
    if (-not ($Catalog.voices | Where-Object { $_.voice_id -eq "af_heart" })) {
      throw "Kokoro voice catalog discovery did not include af_heart."
    }
  } finally {
    Remove-Item -LiteralPath $SmokeInput -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $SmokeOutput -Force -ErrorAction SilentlyContinue
  }
}

function Invoke-RuntimeResponseCalibration {
  Write-Step "Measuring Kokoro runtime speed response."
  $CalibrationJson = (& $VenvPython $CliPath "--measure-runtime-response" "--format" "json")
  if ($LASTEXITCODE -ne 0) {
    throw "Kokoro runtime response calibration failed."
  }
  try {
    $Calibration = (($CalibrationJson -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop)
  } catch {
    throw "Kokoro runtime response calibration returned invalid JSON."
  }
  if ($null -eq $Calibration) {
    throw "Kokoro runtime response calibration returned no payload."
  }
  return $Calibration
}

function Get-KokoroPythonVersion {
  $Version = (& $VenvPython -c "import platform; print(platform.python_version())")
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to read Kokoro Python version."
  }
  return ([string]$Version).Trim()
}

function Get-KokoroRuntimePackages {
  $Code = "import importlib.metadata as m, json, re; norm=lambda n:re.sub(r'[-_.]+','-',n.lower()); pairs=[(norm(d.metadata['Name']),d.version) for d in m.distributions() if d.metadata.get('Name')]; items=dict(pairs); assert len(items)==len(pairs),'duplicate distribution'; print(json.dumps(items, sort_keys=True))"
  $PayloadJson = (& $VenvPython -c $Code)
  if ($LASTEXITCODE -ne 0) {
    throw "Unable to read Kokoro runtime package versions."
  }
  try {
    return (($PayloadJson -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop)
  } catch {
    throw "Kokoro runtime package metadata is invalid."
  }
}

function Write-ReadyMarker([object]$RuntimeResponseCalibration) {
  $RequirementsHash = Get-Sha256 $RequirementsPath
  $PythonVersion = Get-KokoroPythonVersion
  $PythonExecutableSha256 = Get-Sha256 $VenvPython
  $WrapperSourceSha256 = Get-Sha256 $CliPath
  $RuntimeResponseSourceSha256 = Get-Sha256 $RuntimeResponseSourcePath
  $RuntimePackages = Get-KokoroRuntimePackages
  $FlowVersion = $RuntimeResponseCalibration.flow_version
  if ($null -eq $PythonExecutableSha256 -or $null -eq $WrapperSourceSha256 -or $null -eq $RuntimeResponseSourceSha256 -or $null -eq $RuntimePackages -or [string]::IsNullOrWhiteSpace([string]$FlowVersion)) {
    throw "Kokoro runtime identity metadata is unavailable."
  }
  $Marker = [ordered]@{
    flow_version = $FlowVersion
    python_version = $PythonVersion
    python_executable_sha256 = $PythonExecutableSha256
    requirements_sha256 = $RequirementsHash
    wrapper_source_sha256 = $WrapperSourceSha256
    runtime_response_calibration_source_sha256 = $RuntimeResponseSourceSha256
    runtime_packages = $RuntimePackages
    model_sha256 = $ModelSha256
    voices_sha256 = $VoicesSha256
    wrapper_contract = "kokoro_command_v3"
    canonical_sample_rate_hz = 48000
    output_format = "wav_pcm_s16le"
    runtime_response_calibration = $RuntimeResponseCalibration
  }
  $TempPath = "$ReadyMarkerPath.tmp-$([Guid]::NewGuid().ToString('N'))"
  try {
    [IO.File]::WriteAllText($TempPath, (($Marker | ConvertTo-Json -Depth 8) + "`n"), [Text.UTF8Encoding]::new($false))
    Move-Item -LiteralPath $TempPath -Destination $ReadyMarkerPath -Force
  } finally {
    if (Test-Path -LiteralPath $TempPath) {
      Remove-Item -LiteralPath $TempPath -Force
    }
  }
}

try {
  Write-Step "Repository: $RepoRoot"
  Remove-ReadyMarker
  Ensure-Venv
  Ensure-Dependencies
  Ensure-ModelFile $ModelName $ModelPath $ModelUrl $ModelSha256
  Ensure-ModelFile $VoicesName $VoicesPath $VoicesUrl $VoicesSha256
  Invoke-SmokeTest
  $RuntimeResponseCalibration = Invoke-RuntimeResponseCalibration
  Write-ReadyMarker $RuntimeResponseCalibration
  Write-Host ""
  Write-Host "[KOKORO] Setup complete. Add or confirm these app .env values:"
  Write-Host "TTS_PROVIDER_MODE=enabled"
  Write-Host "KOKORO_BASE_PATH=./kokoro"
  Write-Host "KOKORO_COMMAND=./.venv/Scripts/python.exe kokoro_cli.py"
  Write-Host "TTS_PROVIDER_TIMEOUT_SECONDS=180"
  Write-Host "ELEVENLABS_API_KEY=<required for Forced Alignment timing>"
  Write-Host ""
  Write-Host "Kokoro Local will appear as a Voice generation provider when runtime readiness and voice catalog checks pass."
  Write-Host "Then start the app normally with start.bat and check the Voice workspace provider selector."
  exit 0
} catch {
  Remove-ReadyMarker
  Write-Error $_.Exception.Message
  exit 1
}
