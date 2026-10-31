function python { & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') @args }
$env:PYTHONIOENCODING = 'utf-8'
function swap     { & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') my_swap.py menu }
function withdraw { & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') my_withdraw.py menu }
function reprice  { & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') my_reprice.py menu }
function balance  { & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') kraken.py balance --account main }
function orders   { & (Join-Path $PSScriptRoot '.venv\Scripts\python.exe') kraken.py open-orders --account main }
Set-Location $PSScriptRoot
$Host.UI.RawUI.WindowTitle = 'Kraken Trades'
Write-Host ''
Write-Host '  KRAKEN TRADES' -ForegroundColor Cyan
Write-Host ''
Write-Host '  Just type one of these and press Enter:' -ForegroundColor Yellow
Write-Host '    swap       swap one coin for another (BTC, XRP, XLM, LUNA, USD)'
Write-Host '    reprice    change the price of an open order'
Write-Host '    withdraw   send coins to your Ledger (same as the EMERGENCY icon)'
Write-Host '    balance    what is on Kraken'
Write-Host '    orders     your open orders'
Write-Host ''
Write-Host '  Real trades always ask you to type CONFIRM first.' -ForegroundColor Yellow
Write-Host ''
