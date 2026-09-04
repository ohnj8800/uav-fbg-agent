param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$OutputDirectory = "results/deliverables"
)

$captureScript = Join-Path $PSScriptRoot "capture_publication_view.ps1"
& $captureScript `
    -Url "$($BaseUrl.TrimEnd('/'))/paper" `
    -Output (Join-Path $OutputDirectory "figure_c_window_examples.png") `
    -ViewportHeight 650 `
    -Scale 2
& $captureScript `
    -Url "$($BaseUrl.TrimEnd('/'))/paper-timeline" `
    -Output (Join-Path $OutputDirectory "figure_c_flight_timeline.png") `
    -ViewportHeight 580 `
    -Scale 2

Write-Host "Both publication alternatives were created in $OutputDirectory"
