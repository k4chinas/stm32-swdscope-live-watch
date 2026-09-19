@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SWDScope - terminal
where python >nul 2>nul
if %errorlevel%==0 (
  python swdscope.py %*
) else (
  py -3 swdscope.py %*
)

if errorlevel 1 (
  echo.
  echo ---------------------------------------------
  echo Bir sorun olustu. Yukaridaki mesaji oku.
  echo Python kurulu degilse: python.org uzerinden kur
  echo ve kurulumda 'Add to PATH' secenegini isaretle.
  echo ---------------------------------------------
  pause
)
