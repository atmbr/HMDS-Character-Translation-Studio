# Formatos de troca

O Character Translation Studio usa dois formatos JSON simples para colaboração:

- `.ctsdialogue`: exatamente uma fala;
- `.ctspack`: várias falas.

Cada entrada carrega pelo menos:

```json
{
  "script_id": 52,
  "string_index": 8,
  "entity_id": "...",
  "entity_name": "Celia",
  "original_sha256": "...",
  "original": "...",
  "translation": "...",
  "status": "translated"
}
```

`original_sha256` protege contra a aplicação silenciosa da fala em outra base incompatível.

## Regra para importadores externos

Um importador deve tratar cada entrada de forma independente. Se uma fala estiver protegida, ausente ou incompatível, ela deve ser ignorada com um motivo, sem cancelar as outras entradas do arquivo.

Isso é importante em ROMs HMDS antigas nas quais o último ScriptRecord pode usar uma anomalia histórica de ponteiro e permanecer somente leitura.
