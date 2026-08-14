@echo off
REM Runs the torrent scraper using scraper_config.json next to this script.
REM Any extra arguments are passed through to scraper.py, e.g.:
REM   run_scraper.bat -s fosstorrents -n 5
cd /d "%~dp0"
python scraper.py -s 1337x -k linux -v %*
pause
