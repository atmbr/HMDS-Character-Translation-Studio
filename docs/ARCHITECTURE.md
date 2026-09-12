# Arquitetura rápida

Este documento é um mapa do source, não uma especificação completa da engine de HMDS.

## Pipeline principal

```text
ROM .nds
  ↓
NitroFS/FAT
  ↓
ScriptS
  ↓
Pointer table
  ↓
RIFF SCR
  ├─ CODE
  ├─ JUMP
  └─ STR
       ↓
texto editável
       ↓
rebuild STR / records / pointers
       ↓
nova ROM
```

## `cts/engine.py`

É a camada de baixo nível. Lê a ROM, localiza ScriptS, parseia registros RIFF, strings e chunks, aplica edições e reconstrói a ROM.

O editor público trabalha em modo de localização: estruturas de evento/mapa que não são necessárias para tradução não entram no fluxo principal.

## `cts/entities.py`

Liga o índice de pesquisa a personagens e falas. Os metadados descrevem relações confirmadas ou conhecidas entre Character IDs, scripts, Face IDs, expressões e lados.

Quando uma relação ainda não foi confirmada, a UI tenta mostrar isso como desconhecido em vez de inventar um nome/contexto.

## `cts/validation.py`

Transforma a representação amigável do editor nos controles reais usados na ROM.

Exemplos:

- linha normal → texto;
- `Enter` → `0A`;
- `{05 0C}` → nova caixa;
- terminador físico `05 00` → preservado automaticamente;
- `[NOME]` → controle dinâmico conhecido;
- `[FF 2A]` → placeholder neutro para um controle ainda sem significado confirmado.

Também mede bytes por linha e quantidade de linhas por caixa.

## `cts/dialogue_preview.py`

Compositor visual do preview. Usa a fonte bitmap, caixa, name bar, background e estados de retrato para aproximar a aparência do jogo.

O renderer é separado da UI para que possa ser reutilizado por outras ferramentas.

## `cts/project.py`

Guarda o estado do projeto de tradução sem modificar a ROM original a cada edição.

## `cts/team.py`

Cuida dos formatos de exportação/importação usados para dividir trabalho. Os pacotes preservam IDs e hash do texto-base para reduzir o risco de aplicar uma fala na base errada.

## `cts/ui.py`

Interface Tkinter. A intenção é esconder detalhes físicos no uso comum e deixá-los acessíveis no Modo técnico.

## Segurança de rebuild

A ferramenta evita sobrescrever a ROM aberta e reabre a ROM gerada depois do build para verificar se a estrutura principal continua legível.

## Exchange files (v0.9+)

The collaboration layer deliberately uses two JSON-based extensions:

- `.ctsdialogue` — exactly one Script/STR snapshot;
- `.ctspack` — multiple Script/STR snapshots.

Each entry stores the base-text SHA-256, the current exported text, status, Script ID, STR index and optional entity metadata. Import never applies rows directly: the UI first compares each entry against the current project, blocks incompatible base hashes and lets the user choose exactly which rows to replace.

Legacy `.ctsexport` and `.ctsteam` files are read-only-compatible for migration.

## API pública para ferramentas externas

A partir da v0.10.0, `cts.api` é a fachada recomendada para integrações. Ela não depende da interface Tkinter e expõe uma `Workspace` com ROM, sessão, projeto e índice de entidades.

Ferramentas externas devem preferir `cts.api` e os formatos versionados de `cts.team`. Métodos privados de `cts.ui` não fazem parte da API estável.

A importação é deliberadamente tolerante por item: uma fala inválida/protegida gera um item em `TeamImportResult.skipped`, mas não interrompe as outras falas do pacote.
