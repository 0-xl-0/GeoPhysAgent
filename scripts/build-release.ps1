$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
Push-Location -LiteralPath $ProjectRoot
try {
    $env:PYTHONHASHSEED = "0"
    $env:SOURCE_DATE_EPOCH = "1788192000"
    python -m build --no-isolation
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    python -m zipfile -l (Get-ChildItem -LiteralPath dist -Filter "*.whl" | Select-Object -First 1).FullName
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
