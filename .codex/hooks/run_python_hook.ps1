param(
    [Parameter(Mandatory = $true)]
    [string]$Script,
    [switch]$FailClosed
)

$ErrorActionPreference = "Stop"
$repoRoot = (git rev-parse --show-toplevel).Trim()
$scriptPath = Join-Path $repoRoot ".codex/hooks/$Script"
$python = $null
$pythonArgs = @()

$venvPython = Join-Path $repoRoot ".venv/Scripts/python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $python = $venvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $python = $pythonCommand.Source
    } else {
        $pyCommand = Get-Command py -ErrorAction SilentlyContinue
        if ($pyCommand) {
            $python = $pyCommand.Source
            $pythonArgs = @("-3")
        } else {
            $runtimePattern = Join-Path $env:USERPROFILE ".cache/codex-runtimes/*/dependencies/python/python.exe"
            $runtime = Get-ChildItem -Path $runtimePattern -File -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($runtime) {
                $python = $runtime.FullName
            }
        }
    }
}

if (-not $python) {
    if ($FailClosed) {
        [Console]::Error.WriteLine("BLOQUEADO: no se encontró Python para ejecutar $Script.")
        exit 2
    }
    [Console]::Error.WriteLine("Aviso: no se encontró Python; se omitió $Script.")
    exit 0
}

$hookInput = [Console]::In.ReadToEnd()
$hookInput | & $python @pythonArgs -X utf8 $scriptPath
exit $LASTEXITCODE
