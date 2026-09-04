param(
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$OutputDirectory = "results/deliverables"
)

$captureScript = Join-Path $PSScriptRoot "capture_publication_view.ps1"
& $captureScript `
    -Url "$($BaseUrl.TrimEnd('/'))/paper" `
    -Output (Join-Path $OutputDirectory "figure_c_window_examples.png")
& $captureScript `
    -Url "$($BaseUrl.TrimEnd('/'))/paper-timeline" `
    -Output (Join-Path $OutputDirectory "figure_c_flight_timeline.png")

Write-Host "Both publication alternatives were created in $OutputDirectory"
