$cli = Join-Path $PSScriptRoot "living_wiki_cli.py"
& python $cli sync-pointers
exit $LASTEXITCODE
