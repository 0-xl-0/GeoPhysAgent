param(
    [ValidateSet("check", "format")]
    [string]$Mode = "check"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location -LiteralPath $ProjectRoot
try {
    if ($Mode -eq "format") {
        python -m ruff format src tests
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        python -m ruff check --fix src tests
        exit $LASTEXITCODE
    }

    python -m ruff format --check src tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    python -m ruff check src tests
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    python -m unittest discover -s tests -v
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
