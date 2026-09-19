@echo off
chcp 65001 >nul
cd /d "%~dp0"
title SWDScope - tarayici arayuzu
echo SWDScope tarayici arayuzu baslatiliyor...
echo Tarayici kendiliginden acilmazsa: http://127.0.0.1:8730
echo.
where python >nul 2>nul
if %errorlevel%==0 (
  python swdscope.py --web %*
) else (
  py -3 swdscope.py --web %*
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
