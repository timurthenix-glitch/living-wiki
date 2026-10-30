$cli = Join-Path $PSScriptRoot "living_wiki_cli.py"
& python $cli check-version
exit $LASTEXITCODE
