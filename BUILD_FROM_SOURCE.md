# Rodar e compilar a partir do source

## Rodar sem compilar

Recomendado: **Python 3.13 x64**.

```powershell
py -3.13 -m pip install -r requirements.txt
py -3.13 HMDS_Character_Translation_Studio.pyw
```

Se você estiver desenvolvendo outra ferramenta em cima do CTS, pode instalar o projeto em modo editável:

```powershell
py -3.13 -m pip install -e .
py -3.13 -m cts
```

A API recomendada para integrações fica em `cts.api`.

## Gerar o EXE no Windows

```powershell
powershell -ExecutionPolicy Bypass -File .\COMPILAR_EXE.ps1
```

O script:

1. procura Python 3.13;
2. procura MSVC Build Tools;
3. instala as dependências de build;
4. executa Nuitka em modo onefile;
5. gera `dist/HMDS_Character_Translation_Studio_v0.10.0.exe`.

Se o MSVC não estiver instalado, o script tenta instalar o Visual Studio Build Tools oficial da Microsoft. Se o Windows pedir reinicialização, reinicie e execute o script novamente.

## Compilar pelo GitHub Actions

O repositório inclui `.github/workflows/build-windows.yml`.

1. envie o source para um repositório;
2. abra **Actions**;
3. escolha **Build Windows EXE**;
4. clique em **Run workflow**;
5. baixe o artifact ao terminar.

## Testes

```powershell
py -3.13 -m unittest discover -s tests -v
```

Os testes públicos não exigem uma ROM comercial. Para integração completa, use localmente uma ROM HMDS compatível que você possua.

## Dependências

Runtime do source:

- Python 3.13;
- Tk/Tkinter;
- Pillow.

Build:

- Nuitka;
- ordered-set;
- zstandard;
- MSVC Build Tools.

O `.exe` onefile incorpora o runtime. O usuário final não precisa instalar Python ou Pillow.
