$cli = Join-Path $PSScriptRoot "living_wiki_cli.py"
& python $cli check-size @args
exit $LASTEXITCODE
