param(
    [string]$Url = "http://127.0.0.1:8000/paper",
    [string]$Output = "results/deliverables/figure_c_publication_dashboard.png"
)

$browserCandidates = @(
    "$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe"
)
$browser = $browserCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) {
    throw "Chrome or Microsoft Edge was not found."
}

$absoluteOutput = [System.IO.Path]::GetFullPath($Output)
$outputDirectory = Split-Path -Parent $absoluteOutput
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$profileDirectory = Join-Path (
    [System.IO.Path]::GetTempPath()
) ("uav-fbg-publication-" + [System.Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $profileDirectory | Out-Null

if (Test-Path $absoluteOutput) {
    [System.IO.File]::Delete($absoluteOutput)
}

$browserArguments = @(
    "--headless"
    "--disable-gpu"
    "--hide-scrollbars"
    "--no-first-run"
    "--no-default-browser-check"
    "--window-size=1800,850"
    "--force-device-scale-factor=1"
    "--virtual-time-budget=5000"
    "--user-data-dir=$profileDirectory"
    "--screenshot=$absoluteOutput"
    $Url
)

try {
    $process = Start-Process `
        -FilePath $browser `
        -ArgumentList $browserArguments `
        -Wait `
        -PassThru

    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if ((Test-Path $absoluteOutput) -and (Get-Item $absoluteOutput).Length -gt 0) {
            break
        }
        Start-Sleep -Milliseconds 250
    }

    if (-not (Test-Path $absoluteOutput) -or (Get-Item $absoluteOutput).Length -eq 0) {
        throw (
            "The browser did not create the screenshot (exit code " +
            $process.ExitCode + "). Confirm that " + $Url + " opens in your browser."
        )
    }
}
finally {
    if (Test-Path $profileDirectory) {
        Remove-Item -LiteralPath $profileDirectory -Recurse -Force
    }
}

Write-Host "Created $absoluteOutput ($((Get-Item $absoluteOutput).Length) bytes)"
