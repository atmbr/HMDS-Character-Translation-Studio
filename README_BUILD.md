# Build do Windows — v0.10.0

Este pacote gera um executável único do HMDS Character Translation Studio.

## Jeito mais simples

Abra PowerShell na pasta do projeto e execute:

```powershell
powershell -ExecutionPolicy Bypass -File .\COMPILAR_EXE.ps1
```

O script procura Python 3.13 e MSVC Build Tools, instala as dependências de build e chama Nuitka em modo `onefile`.

Resultado esperado:

```text
dist\HMDS_Character_Translation_Studio_v0.10.0.exe
```

Se o instalador do Visual Studio Build Tools pedir reinicialização, reinicie o Windows e rode o mesmo comando novamente.

## GitHub Actions

Também é possível compilar sem configurar o seu PC. Envie o source para um repositório, abra **Actions → Build Windows EXE → Run workflow** e baixe o artifact gerado.

O usuário final do `.exe` não precisa instalar Python, Pillow ou Nuitka.
