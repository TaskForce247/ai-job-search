param([switch]$SkipFrontend)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or
    [Runtime.InteropServices.RuntimeInformation]::ProcessArchitecture -ne "X64") {
    throw "This portable build supports Windows x64 only."
}

$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $root "app\backend\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Create app\backend\.venv and install app\backend[dev] first. See app\README.md."
}
$dist = Join-Path $PSScriptRoot "dist"
$work = Join-Path $PSScriptRoot "work"
& $python (Join-Path $PSScriptRoot "bundle_resources.py")
if ($LASTEXITCODE -ne 0) { throw "The reviewed resource check failed. No package was produced." }
if (-not $SkipFrontend) {
    & npm.cmd --prefix (Join-Path $root "app\frontend") run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
}
& $python -m PyInstaller --noconfirm --distpath $dist --workpath (Join-Path $work "pyinstaller") (Join-Path $PSScriptRoot "local-app.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

$folder = Join-Path $dist "AIJobSearch"
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "portable-readme.txt") -Destination (Join-Path $folder "README.txt")
Copy-Item -LiteralPath (Join-Path $root "LICENSE") -Destination (Join-Path $folder "LICENSE.txt")
$report = Join-Path $work "packaged-smoke.json"
if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report }
$process = Start-Process -FilePath (Join-Path $folder "AIJobSearch.exe") -ArgumentList @("--smoke-test", "--smoke-report", "`"$report`"") -PassThru
if (-not $process.WaitForExit(120000)) {
    Stop-Process -Id $process.Id -ErrorAction SilentlyContinue
    throw "The packaged smoke check timed out. Check for a startup error dialog; no release archive was created."
}
if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $report)) {
    throw "The packaged smoke check failed. Inspect $report before distributing."
}
$result = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
if (-not $result.passed) { throw "The packaged smoke check reported a failure: $($result.error_code)." }

$version = & $python -c "from ai_job_search import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0) { throw "Could not read the application version." }
$archive = Join-Path $dist "AIJobSearch-$version-windows-x64.zip"
Compress-Archive -LiteralPath $folder -DestinationPath $archive -Force
$hash = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$archive.sha256", "$hash  $([IO.Path]::GetFileName($archive))`n")
Write-Output "Portable local-website package: $archive"
Write-Output "Smoke evidence: $report"
