param(
    [switch]$Check,
    [switch]$Update
)
$cli = Join-Path $PSScriptRoot "living_wiki_cli.py"
$params = @("hash")
if ($Check) { $params += "--check" }
if ($Update) { $params += "--update" }
& python $cli $params
exit $LASTEXITCODE
