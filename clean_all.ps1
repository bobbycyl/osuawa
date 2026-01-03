#!pwsh
Remove-Item -r ".\logs\" 2> $null
Remove-Item -r ".\output\" 2> $null
Remove-Item -r ".\static\" 2> $null
Remove-Item -r ".\*LCK" 2> $null
Remove-Item -r ".\.streamlit\.oauth\" 2> $null
Remove-Item -r ".\.streamlit\.components\" 2> $null
Remove-Item -r ".\osuawa.db" 2> $null
