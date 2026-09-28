@echo off
rem Local grader replay on Windows cmd (no Harbor). Run from anywhere: tools\local_check.cmd
rem One-time: python -m pip install pydantic "jsonpath-ng>=1.6,<2" "tenacity>=9,<10" pytest pytest-json-ctrf tzdata
setlocal
pushd "%~dp0.."
python solution\compute_gold.py >nul || goto :fail
fc /b tests\verifier.json tests\manifest.json >nul || (echo verifier.json and manifest.json differ & goto :fail)
set "WS=%TEMP%\b7a4_ws"
if exist "%WS%" rmdir /s /q "%WS%"
mkdir "%WS%"
copy /y solution\files\* "%WS%" >nul
echo --- score.py on gold
set "HARBOR_TASK_WORKSPACE=%WS%"
python tests\score.py > "%WS%\..\b7a4_score.json"
python -c "import json,os;d=json.load(open(os.path.join(os.environ[\"TEMP\"],\"b7a4_score.json\")));print(\"reward\",d[\"reward\"],\"core_failures\",d[\"core_failures\"],d[\"passed\"],\"/\",d[\"total\"])"
echo --- probes
python tools\probes.py || goto :fail
rmdir /s /q "%WS%"
popd
exit /b 0
:fail
echo LOCAL CHECK FAILED
popd
exit /b 1
