@echo off
chcp 65001 >nul
title 코스트코핫딜 - 배포 패키지 만들기
PowerShell -ExecutionPolicy Bypass -File "%~dp0배포패키지_만들기.ps1" -AppDir "%~dp0."
pause
