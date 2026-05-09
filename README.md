# Costco Hotdeal - Installation Guide

## Quick Install
1. Unzip the archive
2. Run `install.bat`  (auto-installs Python packages + desktop shortcut)
3. Run `start_server.bat`
4. Open browser: http://localhost:8501
5. Sign up and wait for admin approval

## Daily Use
- Double-click the desktop shortcut: Costco Hotdeal Management
- Or run `start_server.bat` directly

## Boot-time Auto Start (optional)
- Run `setup_server_boot.bat` as Administrator

## API Setup Guide
- See the [Settings Guide] tab inside the app
  - Naver Commerce API (order sync + auto-ship)
  - KakaoTalk notifications (shopping list alerts)
  - Naver Open API (keyword rank tracking)

## Automation
- Configure tasks in the [Automation] tab
  - Task 1: Daily shopping list via KakaoTalk
  - Task 2: CJ auto-ship + Naver dispatch
  - Task 4: Keyword rank auto-check

## Requirements
- Windows 10 or later
- Python 3.8+ (install.bat will guide you if missing)
- Internet connection

## Troubleshooting
| Issue | Fix |
|---|---|
| Port 8501 conflict | Run stop_server.bat then start_server.bat |
| Package install error | Run install.bat as Administrator |
| API error | See FAQ in the Settings Guide tab |

---
Build: 20260507
