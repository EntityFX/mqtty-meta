@echo off
dotnet build "%~dp0mqtty\src\EntityFX.MqttSimulator\EntityFX.MqttY.Web.App\EntityFX.MqttY.Web.App.csproj" --nologo %*
if errorlevel 1 exit /b %ERRORLEVEL%
echo BUILD_OK
