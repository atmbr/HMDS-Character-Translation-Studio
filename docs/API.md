# API pública para outras ferramentas

Se você quer criar outra ferramenta em cima do HMDS Character Translation Studio, **evite importar classes da interface (`cts.ui`)**. A interface muda mais rápido e é a parte mais sujeita a reorganizações.

Use `cts.api` como ponto de entrada.

## Abrindo uma ROM

```python
from cts.api import Workspace

ws = Workspace.open("Harvest Moon DS.nds")
print(ws.model.game_code)
print(ws.model.script_count)
```

A `Workspace` reúne:

- `model`: parser/rebuilder da ROM e `ScriptS`;
- `session`: alterações ainda não gravadas na ROM;
- `project`: estados de tradução;
- `entities`: índice de personagens/falas.

## Lendo uma fala

```python
fala = ws.get_dialogue(52, 8)
print(fala.current_text)
print(fala.write_protected)
```

`current_text` usa a mesma representação amigável do editor. Controles conhecidos continuam explícitos.

## Alterando uma fala

```python
ws.set_dialogue(
    52,
    8,
    "Primeira linha\nSegunda linha\n\nNova caixa",
    status="translated",
)
```

A API aplica a mesma conversão e validação do editor. Um ScriptRecord protegido não é escrito silenciosamente: a API retorna um erro claro.

## Personagens e falas

```python
for personagem in ws.characters():
    print(personagem.display_name, personagem.character_id)

linhas = ws.dialogue_rows("character:cid:2")
```

Os IDs de entidade são metadata de pesquisa; não assuma que todo Character ID já possui um nome confirmado.

## Importar e exportar

```python
ws.export_dialogue("fala.ctsdialogue", 52, 8)
resultado = ws.import_file("celia.ctspack")
print(resultado.applied, resultado.incompatible)
```

Arquivos com uma fala usam `.ctsdialogue`; pacotes usam `.ctspack`. CSV também pode ser lido pelo núcleo de importação.

Uma entrada incompatível ou protegida **não interrompe o restante do pacote**. Confira `resultado.skipped` para saber o que foi ignorado e por quê.

## Gerar a ROM

```python
ws.build_rom("HMDS_mod.nds")
```

A ROM original não é sobrescrita. A ROM gerada é reaberta pelo parser antes da função retornar.

## Compatibilidade

A API pública é pequena de propósito. Em forks e ferramentas externas, prefira:

```python
from cts.api import Workspace, DialogueSnapshot
```

em vez de importar métodos privados de `cts.ui` ou acessar atributos prefixados com `_`.

Mudanças incompatíveis nos formatos `.ctsdialogue` e `.ctspack` devem criar uma nova versão de formato, em vez de alterar silenciosamente `HMDS-CTS-DIALOGUE-1` ou `HMDS-CTS-PACK-1`.
