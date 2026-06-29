$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
& "D:\anaconda3\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8010
