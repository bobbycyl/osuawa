#!pwsh
Remove-Item -r ".\logs\" 2> $null
Remove-Item -r ".\output\" 2> $null
Remove-Item -r ".\static\" 2> $null
Remove-Item -r ".\*LCK" 2> $null
Remove-Item -r ".\.streamlit\*.pickle" 2> $null
