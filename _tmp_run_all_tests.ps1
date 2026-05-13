$files = Get-ChildItem -Path python_claudecode/tests -Filter 'test_*.py' | Sort-Object Name | ForEach-Object { $_.FullName }
& python -m pytest $files -q
