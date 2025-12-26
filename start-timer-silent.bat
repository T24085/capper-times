@echo off
cd /d "%~dp0"
start /B python main.py --server wss://web-production-03594.up.railway.app
exit


