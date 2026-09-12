BUILD FIX 2

O erro -1978335230 (0x8A150002) era do WinGet: argumentos de linha de comando invalidos.
Esta versao NAO usa WinGet para instalar o Visual Studio Build Tools.
Ela baixa diretamente o bootstrapper oficial da Microsoft:
https://aka.ms/vs/17/release/vs_BuildTools.exe

e instala o workload Microsoft.VisualStudio.Workload.VCTools com privilegios de administrador.

Para compilar:
  powershell -ExecutionPolicy Bypass -File .\COMPILAR_EXE.ps1

Se a instalacao retornar codigo 3010, reinicie o Windows e execute o comando novamente.
