# Harvest Moon DS — Referência Técnica de ROM Hacking

> Documento consolidado para estudo, tradução, criação de ferramentas e pesquisa da engine de **Harvest Moon DS (Nintendo DS)**.
>
> Este material reúne descobertas obtidas por análise estática da ROM, depuração em runtime, breakpoints, dumps de memória, comparação de saves, reconstrução de ScriptS e protótipos usados apenas como instrumentos de pesquisa.
>
> **Pesquisa, testes e validações:** Atm  
> **Estado:** documentação técnica em evolução  
> **Escopo principal validado:** família de ROMs de Harvest Moon DS usada na tradução PT-BR, originalmente derivada da versão europeia/espanhol. Outras revisões/regiões podem deslocar arquivos, tabelas, endereços ou semânticas.

---

## 1. Objetivo deste documento

Este arquivo não é um tutorial de tradução simples. Ele serve como uma **referência de engenharia reversa** para quem quiser entender como o jogo organiza:

- textos e diálogos;
- scripts/eventos;
- escolhas e branches;
- criação de novos eventos;
- áreas de interação (hotspots / `InteractionRect`);
- objetos da casa;
- mapas físicos e mapas lógicos;
- warps/teleportes;
- gráficos, tiles, paletas e compressão LZ10;
- retratos, Face IDs e expressões;
- UI própria de objetos como calendário/TV/telefone;
- save, CRC e extensão persistente HMST;
- hooks ARM9 e native calls;
- reconstrução/realocação segura de arquivos dentro da ROM;
- arquitetura de ferramentas de ROM hacking em cima dessas estruturas.

A intenção é deixar registrado **o que foi confirmado**, **o que é inferência forte** e **o que ainda está pendente**, evitando que pesquisas futuras precisem começar do zero.

---

## 2. Regra de ouro: separar as camadas da engine

Uma das descobertas mais importantes durante a depuração é que várias coisas que parecem ser “o mesmo evento” na tela do jogo são, internamente, estruturas diferentes.

```text
Mapa / cena
   ↓
Objeto visual
   ↓
Colisão / estado de construção
   ↓
InteractionRect / área de interação
   ↓
Script ID
   ↓
ScriptRecord (RIFF/SCR)
   ├─ CODE
   ├─ JUMP
   └─ STR
        ↓
Native calls / ARM9
        ↓
Estado do jogo / save
```

Mover ou alterar uma camada **não implica** mover ou alterar as outras.

Exemplos:

- mover um `InteractionRect` não move o gráfico do móvel;
- um `InteractionRect` existente não garante que o Action Button possa alcançá-lo;
- um Script ID não é necessariamente um “evento completo”; alguns eventos usam vários ScriptRecords;
- uma entrada STR não é necessariamente uma fala visível;
- um objeto pode existir graficamente, mas não estar construído/ativo no estado atual da casa;
- um mapa lógico pode resolver para mapas físicos diferentes conforme estado do jogo.

Essa separação é essencial para evitar bugs como “mobília fantasma”, hotspots ativando no chão vazio ou eventos chamando lógica de outro objeto.

---

# Parte I — Estrutura geral da ROM

## 3. NitroFS, FAT e arquivos internos

Harvest Moon DS usa o filesystem do Nintendo DS. Durante a análise foram acessadas diretamente as seguintes estruturas:

```text
NDS header
FNT (File Name Table)
FAT (File Allocation Table)
NitroFS
ARM9
arquivos internos
```

Arquivos importantes identificados durante a pesquisa incluem:

```text
/map/map.bin
/console/face.bin
/console/background.bin
/console/cons_bg_S.bin
/console/television.bin
/font/font.bin
ScriptS (arquivo apontado pela FAT no perfil estudado)
```

Na família de ROMs estudada, o `ScriptS` é referenciado como **FAT[66]**. Isto deve ser tratado como dado de perfil, não como verdade universal para toda revisão do jogo.

### Compatibilidade recomendada

Ao abrir uma ROM modificada, não é suficiente validar apenas SHA-256. Um editor robusto deve validar as estruturas reais:

```text
header NDS plausível
FAT válida
arquivo ScriptS acessível
pointer table coerente
ScriptRecords reconhecíveis
RIFF / SCR / CODE / JUMP / STR plausíveis
CODE alinhado
/map/map.bin reconhecível
tabelas conhecidas de InteractionRect coerentes
Script IDs referenciados dentro da faixa real
```

A abordagem estrutural permite reabrir ROMs geradas pelo próprio editor sem aceitar cegamente qualquer `.nds`.

---

# Parte II — ScriptS e VM de eventos

## 4. Organização do ScriptS

A base estudada possui uma tabela de ponteiros seguida pelos ScriptRecords.

Na ROM de referência:

```text
pointer_count = 1296
scripts reais = 1295
Script IDs    = 0..1294
```

A relação conceitual é:

```text
pointer table
   pointer[0]
   pointer[1]
   ...
   pointer[N]

ScriptRecord 0
ScriptRecord 1
...
ScriptRecord N-1
```

Normalmente espera-se `N records + 1 pointer`. Porém, na base original foi encontrada uma **anomalia real na cauda**: o último ponteiro da base não é um EOF sentinel convencional e há bytes úteis do record final depois dele.

Consequência prática:

> Não trate cegamente `pointer[last]` como EOF ao reconstruir o ScriptS original.

O Script 1294 deve ser tratado de forma conservadora enquanto a ROM mantém essa convenção de cauda.

ROMs expandidas pelo editor podem usar um EOF sentinel convencional; o parser precisa reconhecer os dois casos.

---

## 5. ScriptRecord RIFF/SCR

Os eventos são armazenados em records no formato RIFF-like:

```text
RIFF
 └─ SCR 
     ├─ CODE
     ├─ JUMP
     └─ STR
```

### CODE

Contém instruções da VM do jogo. A análise já decodificou uma quantidade suficiente de opcodes para:

- disassembly;
- basic blocks;
- branches;
- calls;
- choices;
- inserção de textos;
- relocação de fluxo em casos testados.

Uma regra importante observada no compilador:

```text
CODE size % 4 == 0
```

Depois de inserir ou remover instruções:

```text
recalcular labels/branches
recalcular targets JUMP
inserir padding até CODE % 4 == 0
serializar JUMP/STR
```

Falhar no alinhamento pode levar a branches errados, travamentos ou execução de caminhos incorretos.

### JUMP

O chunk `JUMP` é usado por estruturas de controle como switches/choices em determinados scripts.

Representação útil para ferramentas:

```text
JUMP index
  case value -> CODE target
  default    -> CODE target
```

Ao mudar o tamanho do CODE, os targets precisam ser realocados.

### STR

O chunk STR é um banco físico de strings usado pelo ScriptRecord.

**Importante:** STR não deve ser confundido com uma lista linear de falas do evento.

Um Script pode conter várias STR usadas por:

- caminhos diferentes;
- opções de Choice;
- mensagens internas;
- branches que nem sempre são alcançados;
- outros recursos do mesmo ScriptRecord.

Um editor visual deve separar:

```text
Fluxo lógico reconhecido
    -> interface comum

Banco físico STR
    -> inspector avançado / reverse engineering
```

---

## 6. Strings, terminadores e controles

Durante os testes de texto, uma forte dominância foi observada para mensagens exibidas por `call 45`/`0x2D`: grande parte termina com `0x05` antes do NUL.

Para **novos blocos lógicos de mensagem**, o template validado usa:

```text
encoded_text + 05 + 00
```

Isso não significa que toda STR física deva receber `05`, porque labels de Choice e outras strings podem ter regras diferentes.

Controles importantes encontrados durante a tradução e a análise dos scripts:

```text
0A       quebra de linha
05 0C    nova caixa/página de diálogo
05 00    fim físico comum de mensagem
FF xx    placeholders/controles dinâmicos
81 xx    controles/ícones/efeitos da engine
```

Exemplo conhecido no pipeline de tradução:

```text
FF 24 -> nome do jogador
```

Outros `FF xx` só devem receber nomes semânticos quando confirmados.

---

## 7. VM, chamadas e semântica elevada

A análise semântica do bytecode permitiu elevar alguns padrões confirmados para blocos compreensíveis.

Exemplos:

```text
call 45  -> inicia/exibe texto quando STR é resolvível
call 54  -> Choice binário em padrão conhecido
call 57  -> helper estrutural pós-Choice
call 134 -> SET_INTERACTION_COUNTER_4
call 136 -> BATHROOM_ACTION
call 137 -> LAVABO_ACTION
```

Branches estruturais identificados incluem:

```text
b
blt
ble
beq
bne
bge
bgt
cmp
switch
```

Calls sem semântica confirmada devem continuar aparecendo como:

```text
Native Call 0xNN
```

Nunca invente nomes “amigáveis” para um opcode/call ainda não comprovado.

---

## 8. Mostrar Texto não é apenas `call 45`

Um erro importante descoberto na evolução do editor foi considerar:

```text
push STR
call 45
```

como equivalente a um comando completo “Mostrar Texto”.

Em runtime, textos novos eram mostrados, mas o script podia continuar automaticamente sem esperar confirmação do jogador.

O bloco lógico validado para mensagem inclui inicialização e polling/espera. Para índice STR <= 255, uma forma usada pelo compilador foi:

```text
push8 STR
call 0x2D
push8 6
push8 0
push8 0
call 0x59
poll:
    push8 0
    call 0x5A
    beq done
    b poll
done:
```

O ponto principal é arquitetural:

> Um comando visual de alto nível pode corresponder a várias instruções físicas da VM.

---

# Parte III — Eventos existentes e criação de novos eventos

## 9. Eventos lógicos podem abranger vários scripts

Casos confirmados:

```text
Banheiro
  Script 521 -> início/interação
  Script 522 -> retorno/finalização

Lavabo
  Script 540 -> início/interação
  Script 541 -> retorno/finalização
```

Esses pares são **seeds confirmadas**, não uma regra universal `N -> N+1`.

Isso levou à descoberta de que convém tratar certos conjuntos de ScriptRecords como um **grupo lógico de evento**: vários records físicos podem fazer parte de uma única interação percebida pelo jogador.

---

## 10. Choice e branches

O modelo lógico testado para Choice é hierárquico:

```text
Event
  Command[]

ChoiceCommand
  prompt
  Branch[]

Branch
  Command[]

TextCommand
  text

OpaqueCommand
  bytes / opcode / native id preservados
```

O Script 521 foi usado como caso de teste real:

```text
Choice "Vai tomar um banho?"
├─ Sim
│  ├─ Text "Ramo SIM: comando 1."
│  ├─ Text "Ramo SIM: comando 2."
│  └─ fluxo original do Banheiro
└─ Não
   ├─ Text "Ramo NÃO: comando 1."
   └─ fluxo original do ramo Não
```

O runtime confirmou:

- comandos no ramo correto;
- múltiplos textos no mesmo branch;
- ordem preservada;
- retorno do ramo Sim à ação original;
- realocação de branches/JUMP no caso testado;
- STR nova terminada com `0x05`.

---

## 11. Inserção, reordenação, duplicação e remoção

Os testes de reconstrução mostraram que o número original de STR não pode limitar o número de comandos lógicos de um evento.

Modelo correto:

```text
Event Command List      <- nível autoral
      ↓
CODE / JUMP             <- fluxo físico
      ↓
STR pool                <- armazenamento de strings
```

Operações que foram implementadas e testadas em diferentes etapas da pesquisa incluem:

- inserir Texto antes/depois de boundaries reconhecidos;
- inserir Texto em cada ramo de Choice;
- mover/reordenar textos;
- duplicar;
- copiar;
- remover;
- eliminar Choice em casos testados e direcionar para um ramo fixo;
- reconstruir STR com tamanho variável;
- realocar branches;
- realocar JUMP targets;
- preservar blocos opacos/desconhecidos.

---

## 12. Safety Zones

Para reduzir corrupção durante os testes, os trechos passaram a ser classificados em categorias de segurança, por exemplo:

```text
SAFE_VISUAL
SAFE_LOGIC
ENGINE_TRANSITION
UNKNOWN
```

Uma interface visual equivalente usa cores para deixar claro:

- editável;
- protegido/transição da engine;
- informação estrutural;
- semântica ainda não confirmada.

A filosofia é conservar os trechos desconhecidos **byte-exatos** sempre que possível.

---

## 13. Criando novos eventos

Protótipos antigos provaram o fluxo básico:

```text
+ Novo Evento
 -> alocar novo Script ID
 -> criar novo ScriptRecord RIFF
 -> aumentar pointer_count
 -> reconstruir pointers
 -> criar/ligar InteractionRect custom
 -> executar evento
```

Na base original:

```text
novo 1 -> Script 1295
novo 2 -> Script 1296
novo 3 -> Script 1297
...
```

Uma arquitetura melhor, compatível com ROM já expandida, usa:

```text
new_script_id = model.script_count + novos_da_sessão
```

Exemplos:

```text
ROM com 1295 scripts -> próximo 1295
ROM com 1296 scripts -> próximo 1296
ROM com 1298 scripts -> próximo 1298
```

### Templates recomendados para um criador seguro

Começar por estruturas já conhecidas:

```text
Evento de texto:
  Mostrar Texto
  End

Evento Choice binário:
  Prompt
  Opção 1
  Opção 2
  comandos suportados
  End
```

Cada ScriptRecord novo deve nascer com:

```text
RIFF válido
CODE alinhado em 4
JUMP recalculado/alinhado quando usado
STR serializado pelo codec atual
```

### Teste mínimo para considerar criação de eventos estável

```text
1. criar Evento A na ROM base
2. executar A em gameplay
3. salvar ROM A
4. reabrir ROM A no editor
5. confirmar script_count incrementado
6. criar Evento B
7. executar A e B
8. reabrir ROM B
9. editar A
10. confirmar eventos/áreas oficiais intactos
```

### 13.1 Como a pointer table é calculada

No `ScriptS`, os primeiros 4 bytes armazenam `pointer_count` como `u32 little-endian`. Em seguida vêm `pointer_count` ponteiros de 32 bits. Portanto:

```text
header_size = 4 + pointer_count * 4
script_count = pointer_count - 1
```

Na base estudada:

```text
pointer_count = 1296
header_size   = 4 + 1296*4
              = 5188 bytes
              = 0x1444
script_count  = 1295
```

Ao adicionar um ScriptRecord novo:

```text
pointer_count = 1297
header_size   = 4 + 1297*4
              = 5192 bytes
              = 0x1448
script_count  = 1296
```

Ou seja: **adicionar um único ScriptRecord também aumenta o cabeçalho em 4 bytes**. Se a pointer table for reconstruída no início do arquivo, todos os ScriptRecords posteriores passam a começar 4 bytes depois, mesmo antes de considerar o tamanho do evento novo.

O primeiro ponteiro deve bater com o fim do cabeçalho:

```text
pointer[0] == 4 + pointer_count*4
```

Isso é uma verificação simples, mas muito útil para detectar ScriptS montado incorretamente.

### 13.2 Cálculo do tamanho de um ScriptRecord

Para qualquer Script que não seja o último:

```text
record_start = pointer[i]
record_end   = pointer[i+1]
record_size  = record_end - record_start
```

Em uma build de prova com o Script 1295 mínimo:

```text
pointer[1295] = 0x002E7338
pointer[1296] = 0x002E738C

record_size = 0x002E738C - 0x002E7338
            = 0x54
            = 84 bytes
```

Esse cálculo é especialmente útil ao comparar duas builds: se o início é o mesmo e o ponteiro seguinte mudou, a diferença mostra exatamente quanto o record cresceu ou diminuiu.

> **Exceção importante:** na ROM-base estudada, o último ScriptRecord possui uma anomalia de cauda. O ponteiro extra cai dentro de bytes ainda pertencentes ao RIFF final. Por isso o último Script não deve ser cortado simplesmente em `pointer[last+1]`. Em ScriptS expandido convencional, o ponteiro extra pode voltar a funcionar como EOF sentinel normal.

### 13.3 Estrutura de um ScriptRecord RIFF/SCR

A estrutura observada pode ser representada assim:

```text
+0x00  4 bytes  "RIFF"
+0x04  u32      tamanho total do record
+0x08  4 bytes  "SCR "

Depois:

chunk:
  +0x00  4 bytes  tag, por exemplo "CODE", "JUMP", "STR "
  +0x04  u32      tamanho do payload
  +0x08  ...      payload
```

A conta geral usada na reconstrução é:

```text
record_size = 12 + Σ(8 + payload_size_de_cada_chunk) + padding
```

O record final deve ficar alinhado em 4 bytes:

```text
padding = (4 - (record_size_sem_padding % 4)) % 4
```

Nos testes, quando era necessário padding de 1 a 3 bytes, ele era colocado no final do payload `STR `, evitando inserir bytes arbitrários no meio do `CODE` ou `JUMP`.

Exemplo didático em Python:

```python
import struct

def u32(v):
    return struct.pack("<I", v)

def make_record(chunks):
    # chunks = [("CODE", bytes), ("STR ", bytes), ...]
    body_len = 12 + sum(8 + len(payload) for _, payload in chunks)
    pad = (-body_len) & 3

    if pad:
        chunks = list(chunks)
        for i, (tag, payload) in enumerate(chunks):
            if tag == "STR ":
                chunks[i] = (tag, payload + bytes(pad))
                break
        else:
            raise ValueError("Record precisa de padding, mas não há STR seguro")

    out = bytearray(b"RIFF" + b"\x00\x00\x00\x00" + b"SCR ")
    for tag, payload in chunks:
        out += tag.encode("ascii")
        out += u32(len(payload))
        out += payload

    struct.pack_into("<I", out, 4, len(out))
    assert len(out) % 4 == 0
    return bytes(out)
```

O código acima é **didático**: ele demonstra a conta do container. O payload real de `CODE`, `JUMP` e `STR` precisa seguir a VM/codec da ROM estudada.

### 13.4 Recriando a pointer table

Para um ScriptS convencional com EOF sentinel, a reconstrução pode ser entendida assim:

```python
import struct

def rebuild_pointer_table(records):
    pointer_count = len(records) + 1
    header_size = 4 + pointer_count * 4

    pointers = []
    cur = header_size
    for record in records:
        pointers.append(cur)
        cur += len(record)

    # ponteiro extra / EOF
    pointers.append(cur)

    header = struct.pack("<I", pointer_count)
    header += b"".join(struct.pack("<I", p) for p in pointers)
    return header + b"".join(records)
```

Na ROM-base, **não use esse EOF convencional cegamente para o último record**, porque a cauda original tem uma relação especial já confirmada. Uma ferramenta segura deve primeiro classificar o layout da cauda e preservar a convenção observada.

### 13.5 Texto novo: STR física e comando lógico

Uma nova mensagem validada nos testes não é apenas:

```text
push STR
call 0x2D
```

O bloco lógico usado para exibir a mensagem e aguardar a confirmação do jogador é maior:

```text
push8 STR_INDEX
call 0x2D
push8 6
push8 0
push8 0
call 0x59

poll:
    push8 0
    call 0x5A
    beq done
    b poll

done:
```

Para mensagens novas, o payload textual validado normalmente termina em:

```text
texto_codificado + 05 00
```

O `05` aqui faz parte do formato observado de mensagem; o `00` termina a string física. Isso **não** significa que toda STR do record deve terminar em `05`, porque labels de Choice e outros usos do banco STR podem seguir regras diferentes.

### 13.6 Relocando branches e JUMP após inserir CODE

Se `K` bytes forem inseridos antes de um destino lógico, todo destino que esteja depois da inserção precisa acompanhar o deslocamento. Conceitualmente:

```text
se target >= insertion_offset:
    new_target = target + K
```

O mesmo raciocínio vale para:

- branches `b`, `beq`, `bne`, `blt`, `ble`, `bge`, `bgt`;
- destinos de tabelas `JUMP`;
- labels internos de blocos novos, como `poll` e `done`.

Depois da montagem final:

```text
CODE_size % 4 == 0
```

Se não estiver alinhado, o padding deve ser calculado **antes** de serializar/validar os offsets seguintes. Um bug real encontrado durante a pesquisa veio justamente de CODE desalinhado, fazendo o fluxo cair em offsets incorretos.

### 13.7 Exemplo completo: adicionar o Script 1295

Uma das provas de conceito chegou ao seguinte layout:

```text
antes:
  pointer_count = 1296
  scripts       = 0..1294

depois:
  pointer_count = 1297
  scripts       = 0..1295

Script 1295:
  start = 0x002E7338
  end   = 0x002E738C
  size  = 0x54 bytes
```

Esse ScriptRecord mínimo usado na prova continha, em nível lógico:

```text
call 0x7D
push8 0
call 0x2D
end
end
nop
nop
nop
```

com `STR 0 = "Evento HMST executado!"`. Esse caso serviu como **prova de retorno de uma native call custom para a VM**, não como modelo universal de evento.

Em little-endian, alguns valores aparecem assim no arquivo:

```text
0x002E7338 -> 38 73 2E 00
1295       -> 0x0000050F -> 0F 05 00 00
```

Ver esse tipo de conversão ajuda bastante ao conferir a ROM em um hex editor.

### 13.8 FAT[66] e realocação do ScriptS

No header NDS, o offset da FAT é lido em `0x48`. Cada entrada FAT ocupa 8 bytes:

```text
+0x00 u32 start
+0x04 u32 end
```

Para o arquivo de ID 66:

```text
fat_entry = fat_offset + 66 * 8
script_start = u32(ROM, fat_entry)
script_end   = u32(ROM, fat_entry + 4)
script_size  = script_end - script_start
```

Em uma build de prova, o ScriptS foi realocado para:

```text
FAT[66].start = 0x04000000
FAT[66].end   = 0x042E738C

size = 0x042E738C - 0x04000000
     = 0x002E738C
```

Como essa ROM ultrapassou a capacidade declarada anterior, naquela build específica também foi necessário atualizar:

```text
device capacity: 9 -> 10
header CRC16: 0x8526
```

Esses valores são **exemplo daquela build**, não constantes universais. O procedimento correto é recalcular conforme o tamanho e o header da ROM que estiver sendo modificada.

### 13.9 `InteractionRect` em bytes

Nos registros de interação estudados, cada entrada ocupa 12 bytes e pode ser interpretada como:

```text
u16 left
u16 top
u16 right
u16 bottom
u32 script_id
```

Equivalente a:

```python
struct.pack("<HHHHI", left, top, right, bottom, script_id)
```

O endereço de um record dentro de uma tabela é:

```text
record_address = table_base + index * 12
```

Exemplo da House State 2:

```text
table_base = 0x0218BCF4
record 16  = 0x0218BCF4 + 16*12
           = 0x0218BDB4
```

O record 16 é uma das referências confirmadas para a interação do Banheiro nessa casa.

Para o retângulo do Banheiro:

```text
left=360, top=48, right=384, bottom=88
width  = 384 - 360 = 24
height = 88 - 48   = 40
```

Adicionar um record à tabela, porém, **não basta para criar uma interação funcional**. O Action Button primeiro resolve a classe do alvo. Foi justamente essa separação que explicou por que alguns retângulos existentes não disparavam evento e por que liberar piso vazio globalmente criava interações fantasma.

### 13.10 Referências e offsets usados numa prova de evento custom

Uma build experimental usou estes pontos para fechar a cadeia `Action Button -> InteractionRect -> Script -> native`:

```text
Interaction table custom RAM = 0x022C6200
referências na ROM           = 0x000EF7CC e 0x000EFA84

native switch entry         = 0x0211941C
custom native stub           = 0x022C6A40

Action Button runtime        = 0x020FFE80
Action Button ROM            = 0x00103E80
```

Esses endereços são úteis para entender a prova de conceito, mas são **offsets da família/build estudada**. Não copie esses valores para outra revisão sem confirmar o mapeamento ARM9 e as tabelas correspondentes.

---

# Parte IV — InteractionRects, objetos e Action Button

## 14. `InteractionRect`

As áreas de interação são registros espaciais ligados a Script IDs.

Conceitualmente:

```text
left
top
right
bottom
script_id
```

Uma forma útil de identificar uma área de interação de maneira estável é usar uma chave do tipo:

```text
"physical_map_id:index"
```

Exemplo:

```text
76:16
```

Isto permite editar posição/tamanho sem depender apenas da ordem visual na interface.

---

## 15. House State 0/1/2

Para a casa foram identificadas tabelas separadas conforme o estado/upgrade.

```text
Physical 74 / House state 0
RAM 0x0218BA9C
12 InteractionRects

Physical 75 / House state 1
RAM 0x0218BB38
16 InteractionRects

Physical 76 / House state 2
RAM 0x0218BCF4
20 InteractionRects
```

Na casa final, áreas confirmadas:

```text
Banheiro: (360,48)..(384,88)
Lavabo:   (408,48)..(432,88)
```

Essas coordenadas vêm da tabela real, não de estimativa por screenshot.

---

## 16. `InteractionRect` não significa “objeto ativo”

Caso importante: **Script 528 / Mesa**.

A tabela possui área associada, mas alterar o Script não fazia a interação acontecer normalmente.

Foi confirmado que:

```text
InteractionRect
!= objeto interagível
!= evento alcançável pelo Action Button
```

Pipeline simplificado observado:

```text
objeto/alvo diante do jogador
 -> ResolveActionTargetClass
 -> classe aceita?
    -> se sim: resolver InteractionRect
    -> se não: não dispara o Script do retângulo
```

Valores observados durante a pesquisa:

```text
piso vazio:
  target = 0x00
  class  = 0x00
  rejeitado

objeto interagível:
  target = 0x43
  class  = 0x40
  aceito
```

Isso explica por que um hotspot pode existir fisicamente e ainda assim não ser ativável.

---

## 17. Por que o bypass global de piso vazio é perigoso

Protótipos antigos ampliavam genericamente a aceitação do Action Button para permitir eventos custom no chão.

Problema:

- áreas oficiais normalmente inativas podem se tornar acessíveis;
- móveis ainda não construídos podem ganhar interação;
- surgem “mobílias fantasmas”.

A arquitetura futura deve distinguir explicitamente:

```text
InteractionRect oficial
InteractionRect custom do editor
```

antes de flexibilizar a regra do Action Button.

Também deve usar edge/latch para evitar disparo duplicado enquanto o botão A permanece pressionado.

---

# Parte V — Objetos da casa

## 18. Scene Object Registry

A comparação entre scripts, objetos visuais e interações mostrou que é útil manter uma camada semântica entre Script ID e objeto de cena.

Estrutura conceitual:

```text
object_id
name
kind
script_ids
confidence
note
ui_profile
world_asset_status
asset_hint
```

Mapeamentos atuais:

| Script | Objeto | Confiança |
|---:|---|---|
| 521 | Banheiro | confirmado runtime |
| 523 | Cama do jogador | alta |
| 524 | Cama do cônjuge | alta |
| 527 | Calendário | confirmado ROM |
| 528 | Mesa | confirmado ROM; ativação não presumida |
| 529 | Relógio / visor | confirmado runtime |
| 530 | Lixeira | confirmado runtime |
| 531 | Cozinha | confirmado runtime |
| 532 | Mobília — identificação pendente | pendente |
| 533 | Toca-discos | confirmado runtime |
| 534 | Mobília — identificação pendente | pendente |
| 535 | Mobília — identificação pendente | pendente |
| 536 | Porta-meia | confirmado ROM |
| 537 | Cama da criança | confirmado ROM |
| 538 | Telefone | confirmado ROM; renderer visual pendente |
| 539 | TV | inferido forte / provável |
| 540 | Lavabo | confirmado runtime |
| 542 | Mobília — identificação pendente | pendente |

Regra de documentação:

> Não chamar 532/534/535/542 de “baú” ou qualquer móvel específico sem prova.

---

# Parte VI — Mapas

## 19. `/map/map.bin`

A reconstrução dos mapas pode ser feita diretamente a partir da ROM pelo seguinte pipeline:

```text
NitroFS
 -> /map/map.bin
 -> HmdsMapHeader
 -> streams LZ10
 -> GFX / tilemap / attributes
 -> camadas
 -> paletas
 -> RGB555
 -> imagem final
```

Foi validado um pipeline contendo:

```text
layer0 -> tiles 4bpp
layer1/2 -> tiles 8bpp
normal compact palette
extended compact palette
RGB555
```

O Physical Map 76 foi comparado pixel a pixel contra a referência usada durante o desenvolvimento.

Em uma ROM usada nos testes, foram encontrados **78 Physical Maps renderizáveis**. As tabelas de warp, porém, fazem referência a IDs físicos na faixa 0..78 porque também incluem slots/extras envolvidos nas transições; portanto “quantidade de mapas renderizáveis” e “faixa de IDs citados por outras tabelas” não devem ser tratados automaticamente como a mesma coisa.

---

## 20. Mapas físicos vs mapas lógicos

A análise das tabelas de warp separa:

```text
Logical Maps 0..71
extras físicos 72..78
```

Alguns Logical Maps podem resolver para diferentes mapas físicos conforme estado.

Caso importante:

```text
Logical 48 -> Physical 74 / 75 / 76
```

correspondendo aos estados da casa.

Também há casos especiais/dinâmicos em torno de Logical 49..51 e Physical 77/78 cuja resolução completa ainda não foi declarada fechada.

---

## 21. Renderização, paletas e horário/frames

No `map.bin`, cada mapa possui ponteiros para recursos como:

```text
gfx
palette
tilemap
attributes
extras
```

Os frames de paleta podem ser calculados a partir dos próprios dados compactados da ROM. Isso permite reconstruir variações visuais do cenário diretamente dos assets originais.

Durante protótipos anteriores, mapas também foram usados com variações de dia/tarde/noite. Qualquer associação temporal deve permanecer baseada nos frames/paletas reais e não em filtros gráficos artificiais.

### RGB555

Formato comum no Nintendo DS:

```text
15 bits úteis
5 bits R
5 bits G
5 bits B
```

Ferramentas precisam expandir cada canal para 8 bits apenas na visualização; a ROM continua armazenando a cor no formato original.

---

# Parte VII — Warps e transições de mapa

## 22. WarpRecords

A análise dos warps identificou a tabela de `WarpRecord` no perfil estudado.

Referência:

```text
ROM offset  = 0x001A16C0
RAM base    = 0x0219D6C0
boundaries  = 80
record size = 12 bytes
```

Dataset de referência:

```text
140 records em Logical 0..71
149 records em Physical/extra 0..78
115 links recíprocos estáticos no export lógico
12 registros com captura runtime
8 registros divergindo dos bytes estáticos da ROM
```

Isto mostra que nem todo comportamento de warp pode ser entendido olhando apenas a tabela estática: alguns destinos/estados são resolvidos ou alterados em runtime.

### Validação estrutural da tabela

A validação estrutural usada na pesquisa verifica:

```text
boundary[0] == 0x00A0
80 boundaries monotônicos
intervalos múltiplos de 12 bytes
```

---

## 23. Runtime x ROM estática

Para comparar o que está na ROM com o que realmente acontece no jogo, a pesquisa mantém duas camadas conceituais:

- WarpRecord da ROM;
- Warp observado em runtime.

Isso é importante para pesquisa porque uma diferença não significa necessariamente corrupção: pode indicar resolução dinâmica de destino, construção, estado da casa ou outra lógica da engine.

---

# Parte VIII — Gráficos e UI

## 24. Compressão LZ10

Vários recursos gráficos do jogo usam LZ10.

Header típico:

```text
10 xx xx xx
```

onde os três bytes seguintes codificam o tamanho descomprimido little-endian.

A pesquisa já confirmou descompressão LZ10 para:

- mapas;
- retratos;
- calendário;
- outros recursos de UI.

Ao reinserir gráficos, é preciso respeitar:

- formato de tiles;
- tamanho esperado;
- tilemap;
- bancos de paleta;
- alinhamento/ponteiros;
- eventual recompressão.

---

## 25. Tiles 4bpp e 8bpp

### 4bpp

Cada pixel usa 4 bits e seleciona uma cor em um banco de 16 cores.

```text
8x8 tile = 64 pixels
64 × 4 bits = 32 bytes

1 cor RGB555 = 2 bytes
16 cores     = 16 × 2 = 32 bytes de paleta
```

### 8bpp

Cada pixel usa 8 bits:

```text
8x8 tile = 64 bytes
```

A interpretação correta depende do recurso e da camada.

---

## 26. Calendário — UI estrutural confirmada

O calendário é um dos casos mais completos de UI extraída diretamente da ROM.

Arquivo:

```text
/console/cons_bg_S.bin
```

Recurso usado:

```text
root[22] = 0x2644C
```

Descriptor observado:

```text
palette = 0x2645C
gfx     = 0x264BC
tilemap = 0x27558
end     = 0x27704
```

### Paletas

```text
96 bytes
= 3 × 16 cores RGB555
```

### GFX

Header:

```text
10 C0 0E 00
```

Logo:

```text
decompressed = 0xEC0 = 3776 bytes
3776 / 32 = 118 tiles 4bpp
```

### Tilemap

Header:

```text
10 00 06 00
```

Logo:

```text
decompressed = 0x600 = 1536 bytes
1536 / 2 = 768 entries
768 = 32 × 24
32×8 × 24×8 = 256×192
```

Os entries usam palette banks 4..6, enquanto o recurso local possui três paletas. Para esse renderer:

```text
local_palette = nscr_palette_bank - 4
```

A camada estática reconstruída apresenta os cabeçalhos:

```text
DOM SEG TER QUA QUI SEX SÁB
```

### O que ainda não representa a tela completa

O renderer atual não afirma reconstruir:

- números dos dias;
- cursor;
- seleção;
- ícones/eventos dinâmicos;
- OBJs sobrepostos;
- todas as camadas compostas em runtime.

Classificação correta:

```text
CALENDAR_STATIC_BG_LAYER = confirmado da ROM
FULL_CALENDAR_RUNTIME_SCREEN = pendente
```

---

## 27. TV e telefone

### TV

Arquivo candidato localizado:

```text
/console/television.bin
```

Relação atual:

```text
Script 539 = TV (provável / inferência forte)
renderer visual = pendente
```

Não usar mockup como se fosse a UI real.

### Telefone

```text
Script 538 = Telefone
handler/UI global conhecido
renderer visual = pendente
```

---

# Parte IX — Retratos, personagens e expressões

## 28. Character ID, Face ID e expressão

A pesquisa de runtime mostrou que Character ID e Face ID são conceitos distintos.

Modelo consolidado:

```text
Character ID
 -> Face ID
 -> estado de expressão
 -> atualização do pacote/estado do rosto
 -> renderer seleciona a variante
```

No caso confirmado do Takakura:

```text
Character ID 30
Face ID 31
```

Runtime do loader de rosto observou Face ID 31 com diferentes estados de expressão.

---

## 29. `020264F8` e pipeline de Face

O loader `0x020264F8` foi observado com Face 31 e expressões diferentes.

O caller `0x0200E1C0` trata o parâmetro como estado/variante de expressão e compara com o estado atual antes de chamar o loader.

Porém o loader não deve ser entendido como “ler diretamente a expressão N do arquivo”. Ele prepara/carrega o pacote do Face ID; a escolha/aplicação da variante envolve o pipeline ao redor.

---

## 30. `face.bin`

Arquivo:

```text
/console/face.bin
```

Para Face ID 31, o registro estudado começa com:

```text
64 bytes de paleta
= 32 cores RGB555

18 ponteiros u32
para subrecursos
```

Vários subrecursos são LZ10. Após descompressão, os tiles 4bpp mostram partes reconhecíveis do Takakura.

Pipeline já comprovado em nível de dados:

```text
face.bin
 -> Face 31
 -> paleta
 -> ponteiros
 -> LZ10
 -> tiles 4bpp
```

O desafio inicial era reconstruir layout/células/OAM para formar os retratos completos fora do jogo; ferramentas posteriores de pesquisa conseguiram gerar centenas de previews estruturais usando estados observados/CFG.

---

## 31. Contexto de retrato no fluxo de chamadas

A análise do fluxo de chamadas distinguiu diferentes fontes de contexto:

```text
call 0x43 -> atualização ativa de personagem/retrato
call 0x3E -> contexto estrutural de retrato/personagem
call 0x1F -> fallback candidato, não prova final
```

Na v0.11, o caminho de visibilidade foi fechado no ARM9:

```text
Face ID válido -> 02004BC4(obj, 1)
sem Face ID    -> 02004BC4(obj, 0)
```

`02004BC4` altera o bit 0 de `object+0x0E`.

Com isso, estados CFG-reachable de `0x3E`/`0x43` com Face ID válido podem ser classificados como retrato visível após a atualização.

### Estatísticas da pesquisa v0.11

```text
script_count                           = 1295
dialogue call 0x2D total              = 14378
dialogue blocks                       = 14376
unreachable dialogue calls            = 2
call 0x3E total                       = 1320
0x3E context resolved                 = 1221
0x3E unresolved                       = 99
call 0x42 total                       = 10355
call 0x43 total                       = 4230
dialogues with visible preview        = 7873
visible via 0x43                      = 5322
visible via 0x3E                      = 2551
unique Face/Expression/Side previews  = 392
```

Esses números são da ROM/perfil analisado e não devem ser tratados como invariantes universais.

---

## 32. `call 0x3E`

A análise estática encontrou uma forma de oito posições. Em 366 casos totalmente imediatos:

```text
arg2 == arg4
arg3 == arg5
arg6 sempre 0..5 ou 255
arg7 sempre 0..5 ou 255
```

No caminho estudado:

```text
arg2 -> Character ID esquerdo
arg3 -> Character ID direito
arg6 -> expressão esquerda
arg7 -> expressão direita
```

`arg1` e `arg8` devem permanecer sem rótulo sem prova adicional.

---

# Parte X — Preview de diálogo

## 33. Composição estrutural do preview de diálogo

O preview integrado usa assets extraídos/derivados da própria pesquisa:

```text
caixa     256×64  em (0,0)
barra     256×16  em (0,176)
cauda     16×16   OBJ separado
seta      16×16   OBJ separado
fonte     8×16    /font/font.bin
BG        256×112 em (0,64)
```

A seta foi observada em posição aproximada/runtime de pesquisa:

```text
(236,45)
```

A cauda varia conforme o lado do retrato.

O preview deve distinguir:

```text
contexto/personagem relacionado
retrato realmente visível
speaker efetivo
```

Não é seguro promover automaticamente “personagem visível” para “falante” quando há múltiplos contextos.

---

# Parte XI — Save e persistência custom HMST

## 34. Estrutura do save oficial observada

Nos testes RAW, o save tem:

```text
0x40000 bytes = 256 KiB
```

Foram identificados dois diários principais:

```text
Diary 1: backup 0x0200, length 0x8C00
Diary 2: backup 0x8E00, length 0x8C00
```

O protocolo oficial observado para backup usa a mesma wrapper com comandos distintos:

```text
READ  = command 6 / arg 1 / mode 0
WRITE = command 7 / arg 0x0A / mode 1
```

READ pode ser assíncrono: é necessário aguardar o estado de transferência terminar antes de interpretar imediatamente o buffer.

---

## 35. CRC oficial

A pesquisa confirmou CRC-16 refletido usando:

```text
poly = 0xA001
init = 0x2410
```

Os testes validaram externamente CRCs de:

- Diary 1;
- Diary 2;
- header.

Qualquer editor de save deve recalcular/validar esses campos em vez de simplesmente alterar bytes e esperar que o jogo aceite.

---

## 36. HMST — extensão persistente experimental

Foi criado um container custom fora dos dois diários oficiais para provar que a ROM pode manter estado adicional sem quebrar o save original.

### HMST v2

Região:

```text
base = 0x3F000
size = 0x0800
end  = 0x3F7FF
```

Layout:

```text
+000  char[4] magic        = "HMST"
+004  u16 version          = 2
+006  u16 header_size      = 0x20
+008  u32 total_size       = 0x800
+00C  u32 load_count
+010  u32 global_flags
+014  u32 feature_bits
+018  u32 save_count
+01C  u32 reserved

+020..+09F custom_flags bitmap      (0x80 bytes / 1024 bits)
+0A0..+29F custom_variables         (0x200 bytes / 128 u32)
+2A0..+31F self_switches bitmap     (0x80 bytes / 1024 bits)
+320..+41F quest_state              (0x100 bytes)
+420..+51F event_state              (0x100 bytes)
+520..+61F map_state                (0x100 bytes)
+620..+7FD reserved/future          (0x1DE bytes)
+7FE..+7FF CRC16
```

CRC HMST:

```text
poly  = 0xA001
init  = 0x2410
range = HMST +0x000 .. +0x7FD
store = HMST +0x7FE
```

O container v2, migração v1.1 -> v2, CRC próprio e integridade dos diários foram confirmados em runtime nos testes descritos no relatório.

---

## 37. Ponte Script/Native -> HMST

Foi provado que uma ação real do jogo pode alterar o estado HMST e persistir no save.

Primeiro teste:

```text
Script 521 Banheiro
 -> native call 0x136
 -> handler ARM9 0x0211F0F4
 -> bridge HMST
 -> custom_flags[0] = 1
 -> custom_variables[0] += 1
 -> CRC HMST
 -> save
```

Depois foi criado um native call custom e Script 1295 independente.

Cadeia confirmada:

```text
Action Button
 -> InteractionRect custom
 -> Script 1295
 -> opcode call 0x7D
 -> switch nativo patchado
 -> ARM9 custom
 -> HMST RAM
 -> custom flag/variable
 -> CRC
 -> SAVE HMST
```

Isto estabelece a base para comandos de scripting custom persistentes.

---

## 38. API HMST genérica

A pesquisa evoluiu para operações conceituais:

```text
HMST_SET_FLAG(index)
HMST_CLEAR_FLAG(index)
HMST_GET_FLAG(index)
HMST_SET_VAR(index, value)
HMST_ADD_VAR(index, delta)
HMST_GET_VAR(index)
```

O relatório registra confirmação em runtime de SET/GET/ADD e branch condicional da VM sobre retorno HMST.

Isso abre caminho para eventos custom estilo RPG Maker com variáveis e flags persistentes sem reutilizar indevidamente flags oficiais do jogo.

---

# Parte XII — Hooks ARM9 e code caves

## 39. Regras de segurança para hooks

Ao inserir código ARM9 custom:

1. confirmar que a região é carregada em RAM;
2. confirmar que a cave não é usada por outro sistema;
3. preservar a semântica da instrução substituída;
4. preservar registradores esperados pelo caller;
5. respeitar ARM/Thumb/interworking;
6. não sobrepor buffer HMST, stubs anteriores ou tabelas realocadas;
7. testar runtime e depois validar save/CRC.

Exemplo de conflito histórico importante:

```text
cave antiga: 0x022C7F00
HMST v2:     0x022C7800..0x022C7FFF
```

Portanto a cave antiga **não pode ser reutilizada** numa build que use HMST v2.

---

# Parte XIII — Rebuild e realocação da ROM

## 40. Realocar ScriptS

Quando ScriptS cresce, o builder pode realocá-lo ao final da ROM e atualizar a FAT.

Em um experimento com Script 1295:

```text
pointer_count passou 1296 -> 1297
scripts reais 0..1295
```

O arquivo foi realocado e a capacidade do cartucho no header precisou ser atualizada quando a ROM ultrapassou a capacidade declarada anterior.

Um builder deve recalcular pelo menos os campos de header que realmente dependem da modificação, incluindo CRC do header quando necessário.

---

## 41. Evitar crescimento infinito em builds iterativos

Problema clássico:

```text
ROM
+ ScriptS antigo órfão
+ ScriptS novo
+ ScriptS novo seguinte
+ ...
```

Uma estratégia segura para builds iterativos é:

```text
se FAT[66].end == tamanho atual da ROM:
    ScriptS já está no fim
    -> truncar a cópia anterior no estágio de build
    -> regravar a partir do mesmo início
senão:
    -> realocar conservadoramente para o fim
```

Isso permite o ciclo:

```text
abrir
editar
gerar
reabrir
editar novamente
gerar novamente
```

sem crescimento indefinido por cópias órfãs do mesmo arquivo.

---

# Parte XIV — Safety Guard e round-trip

## 42. Princípios para um editor confiável

Antes de permitir escrita, ferramentas devem preferir:

```text
round-trip byte-exact quando nenhuma edição é feita
diff por Script ID
recusar build se algo não selecionado mudou sem motivo
preservar opcode/call desconhecido
manter disassembly avançado
validar boundaries de branches/JUMP
validar RIFF/SCR/chunks
```

Para script não editado:

```text
record inteiro byte-exato
```

Para edição apenas de STR:

```text
CODE e JUMP byte-exatos
```

Para CODE editado:

```text
opcode válido
instrução completa
branch target em boundary válido
JUMP target válido
chunks reconstruíveis
CODE alinhado
```

---

# Parte XV — Arquitetura recomendada para ferramentas

## 43. Não duplicar a fonte de verdade

Os testes mostraram uma regra importante para qualquer ferramenta: metadados de pesquisa podem organizar a navegação, mas os bytes editáveis devem vir sempre da ROM/sessão atual.

Exemplo:

```text
Entity Registry
  -> relações e nomes técnicos

RomModel/EditSession
  -> fonte de verdade dos bytes
```

Isso evita que um JSON de pesquisa fique desatualizado e sobrescreva dados reais de uma ROM modificada.

---

## 44. Entity Registry

Chave inicial usada:

```text
character:cid:<CharacterID>
```

Exemplo:

```text
character:cid:30
  display_name = Takakura
  Character ID = 30
  Face ID      = 31
  -> diálogos
  -> Scripts
  -> retratos/expressões
  -> mapa, quando existe relação espacial conhecida
```

Dataset obtido numa etapa da pesquisa:

```text
175 Character IDs
141 entidades com diálogos relacionados
```

Namespaces futuros/recomendados:

```text
item:
object:
tool:
map:
event:
hotspot:
```

---

## 45. Navegação bidirecional

Uma ferramenta avançada pode conectar:

```text
Entidade
 -> diálogo
 -> Script
 -> pesquisa de eventos e bytecode
 -> InteractionRect
 -> mapa
 -> objeto/UI
```

E o caminho inverso:

```text
Mapa
 -> InteractionRect
 -> Script/evento
 -> entidades relacionadas
 -> diálogo/retrato
```

Esse modelo é mais útil para ROM hacking do que editores independentes sem contexto compartilhado.

---

# Parte XVI — Performance e UX de ferramentas

## 46. Preload de mapas

Renderizar mapas grandes repetidamente pode congelar a interface.

A análise passou a adotar:

```text
abertura da ROM
 -> thread de preload
 -> barra 0–100%
 -> cache dos mapas físicos
```

O custo pesado acontece uma vez e a troca de mapa reutiliza a cache.

---

## 47. Evitar loops de seleção da UI

Foi identificado um problema clássico de Tk/Treeview:

```text
selection event
 -> refresh
 -> selection_set
 -> novo selection event
 -> refresh
 -> ...
```

Correção:

- guarda de sincronização;
- ignorar notificações duplicadas;
- repintar apenas seleção anterior/nova;
- durante drag, atualizar overlay/cache/StringVars sem reconstruir o inspector inteiro.

Regressão registrada na pesquisa:

```text
antes: loop / >25 s
após correção: 400 seleções / 400 callbacks / ~0,087 s
```

Isso é relevante para qualquer editor gráfico de mapas/eventos em Python.

---

# Parte XVII — Estado de conhecimento e níveis de confiança

## 48. Classificação recomendada

Cada descoberta deve carregar nível explícito:

```text
CONFIRMADO EM RUNTIME
CONFIRMADO NA ROM / ESTÁTICO
ALTA / INFERIDO_FORTE
PROVÁVEL
PENDENTE
HIPÓTESE
```

Não misture essas categorias.

Exemplo:

```text
Script 521 = Banheiro          confirmado runtime
Script 540 = Lavabo            confirmado runtime
Script 531 = Cozinha           confirmado em runtime em testes posteriores
Script 539 = TV                inferência forte / provável
Script 532/534/535/542         identidade pendente
```

---

# Parte XVIII — Mapa de pesquisa para futuros ROM hackers

## 49. Áreas já suficientemente compreendidas para ferramentas

Em graus diferentes, já há base prática para:

- parser ScriptS;
- leitura/escrita STR;
- rebuild de ScriptS;
- relocação da FAT;
- decompilação parcial da VM;
- Choices binários conhecidos;
- inserção de mensagens com espera;
- branches/JUMP relocados em casos testados;
- agrupamento de eventos multi-Script;
- InteractionRects da casa 74/75/76;
- mapas via `/map/map.bin`;
- WarpRecords read-only;
- retratos via Character/Face/Expression;
- preview estrutural de diálogo;
- calendário estático real;
- save RAW e CRC;
- container HMST custom;
- native call custom;
- flags/variáveis persistentes custom;
- Entity Registry;
- Scene Object Registry.

---

## 50. Áreas que ainda pedem pesquisa adicional

- layout/OAM universal de todos os recursos gráficos ainda não mapeados;
- composição runtime completa de várias UIs;
- TV completa;
- telefone completo;
- objetos/sprites separados da cena em todos os mapas;
- NPC placement como camada editável genérica;
- portas/exits genéricos;
- triggers `Player Touch` genéricos;
- Autorun genérico;
- write-back de WarpRecords com segurança;
- resolução completa de todos os mapas dinâmicos/especiais;
- identificação semântica de todos os native calls;
- identificação de todos os móveis ainda pendentes;
- confirmação universal de speaker em diálogos com múltiplos retratos;
- portabilidade automática entre todas as regiões/revisões do jogo.

---

# Apêndice A — Mapa rápido de offsets e fórmulas

Esta tabela serve como índice para depuração. **Não trate os endereços como universais**: eles pertencem à família/build estudada e devem ser confirmados antes de qualquer patch.

| Área | Endereço / fórmula | Observação |
|---|---|---|
| Campo FAT no header NDS | `ROM + 0x48` | contém o offset da File Allocation Table |
| Entrada FAT de um arquivo | `fat_offset + file_id * 8` | `u32 start` + `u32 end` |
| ScriptS no perfil estudado | `file_id = 66` | localizar pela FAT, não por offset fixo |
| Header do ScriptS | `4 + pointer_count*4` | `pointer[0]` deve coincidir com esse valor |
| Quantidade de Scripts | `pointer_count - 1` | desde que o layout seja estruturalmente válido |
| Tamanho de Script comum | `pointer[i+1] - pointer[i]` | não aplicar cegamente à anomalia do record final da base |
| Warp table | ROM `0x001A16C0` / RAM `0x0219D6C0` | records de 12 bytes no perfil estudado |
| House state 0 | RAM `0x0218BA9C` | 12 InteractionRects |
| House state 1 | RAM `0x0218BB38` | 16 InteractionRects |
| House state 2 | RAM `0x0218BCF4` | 20 InteractionRects |
| Referências House state 2 | ROM `0x000EF7CC`, `0x000EFA84` | apontam para `0x0218BCF4` na base estudada |
| InteractionRect `i` | `table_base + i*12` | `<HHHHI>` |
| Action Button observado | RAM `0x020FFE80` / ROM `0x00103E80` | usado em testes de ativação |
| Native switch entry de prova | RAM `0x0211941C` | slot usado pelo native custom `0x7D` |
| Stub native de prova | RAM `0x022C6A40` | build experimental; não universal |
| Loader de Face | RAM `0x020264F8` | observado com Face ID 31 |
| Caller de expressão | RAM `0x0200E1C0` | compara estado/variante antes do loader |
| HMST no save | `0x3F000..0x3F7FF` | container experimental v2 de `0x800` bytes |
| HMST em RAM | `0x022C7800..0x022C7FFF` | buffer usado nos testes v2 |

## Fórmulas úteis de bolso

```text
# ScriptS
script_count = pointer_count - 1
header_size  = 4 + pointer_count*4
record_size  = next_pointer - current_pointer

# InteractionRect
record_offset = table_base + index*12
width  = right - left
height = bottom - top

# Tiles
4bpp tile 8x8 = 8*8*4/8 = 32 bytes
8bpp tile 8x8 = 8*8*8/8 = 64 bytes

# Paleta RGB555
bytes = quantidade_de_cores * 2

# FAT NDS
entry = fat_offset + file_id*8
size  = end - start
```

## Exemplo de leitura rápida em Python

```python
import struct

def u16(data, off):
    return struct.unpack_from("<H", data, off)[0]

def u32(data, off):
    return struct.unpack_from("<I", data, off)[0]

def get_fat_file(rom, file_id):
    fat_offset = u32(rom, 0x48)
    entry = fat_offset + file_id * 8
    start = u32(rom, entry)
    end = u32(rom, entry + 4)
    return start, end, rom[start:end]

def read_script_pointers(script_s):
    pointer_count = u32(script_s, 0)
    pointers = [u32(script_s, 4 + i*4) for i in range(pointer_count)]
    expected_header = 4 + pointer_count*4
    if pointers[0] != expected_header:
        raise ValueError("pointer[0] não coincide com o fim do header")
    return pointer_count, pointers
```

Esse trecho é propositalmente pequeno: ele ajuda quem já mexe com tradução/hexadecimal a localizar o ScriptS e conferir a pointer table antes de partir para um parser completo.

---

# Parte XX — Glossário rápido

**ARM9** — processador/código principal do jogo no Nintendo DS; grande parte da lógica da engine pesquisada está aqui.

**Character ID** — identificador lógico de personagem/contexto; não é necessariamente igual ao Face ID.

**Face ID** — pacote gráfico de retrato em `/console/face.bin`.

**Expression** — variante/estado do retrato.

**Script ID** — índice físico de ScriptRecord dentro de ScriptS.

**ScriptRecord** — record RIFF/SCR contendo CODE/JUMP/STR.

**Evento lógico / grupo lógico** — interpretação em que um ou mais ScriptRecords físicos pertencem à mesma interação do jogo.

**STR** — banco de strings físico do ScriptRecord.

**CODE** — bytecode da VM de eventos.

**JUMP** — tabela de destinos usada por determinados controles de fluxo.

**Native Call** — chamada da VM para lógica implementada em ARM9.

**InteractionRect** — retângulo espacial associado a interação/Script.

**Physical Map** — recurso físico renderizável da ROM.

**Logical Map** — ID lógico usado por sistemas que podem resolver para diferentes mapas físicos.

**WarpRecord** — registro de transição/teleporte entre locais.

**LZ10** — compressão comum nos assets de Nintendo DS.

**4bpp / 8bpp** — profundidade de cor dos tiles.

**RGB555** — cor de 15 bits usada em recursos do NDS.

**HMST** — container custom experimental criado durante a pesquisa para persistência adicional de flags/variáveis/estado.

**Safety Guard** — conjunto de verificações para impedir que um rebuild altere estruturas não selecionadas ou conhecidas.

**Code cave** — região de código/dados não usada onde um hook custom pode ser inserido, desde que validada.

---

# Parte XXI — Fontes consolidadas deste documento

Este documento foi preparado a partir de relatórios, dumps e artefatos de pesquisa, principalmente:

```text
relatório estrutural de engenharia reversa v10.322
HMDS_Engine_Reverse_Engineering_PTBR_v10_357_Takakura_Expr01_Face31.md
dados de pesquisa de warps / House Facilities
dados de pesquisa de objetos de cena / UI
dados de pesquisa de fluxo de chamadas / retratos
```

Também foram considerados materiais auxiliares de depuração, incluindo:

```text
estrutura central de ROM / ScriptS
pesquisa de eventos e bytecode
contexto de mapas e áreas de interação
objetos de cena e UIs internas
dataset de referência de warps
sumários de CFG / retratos
```

A documentação deve continuar evoluindo junto com a pesquisa. Quando uma hipótese for confirmada ou refutada em runtime, atualize o nível de confiança em vez de apenas substituir silenciosamente a informação antiga.

---

# 51. Aviso de compatibilidade

Este material descreve a família de ROMs estudada e **não é uma especificação oficial da Marvelous/Natsume/Nintendo**.

Offsets absolutos e endereços ARM9 podem mudar entre:

- região;
- revisão;
- ROM modificada;
- rebuilds que realocam arquivos;
- ROMs que já expandiram ou realocaram ARM9/ScriptS.

Prefira sempre:

```text
assinatura estrutural
+ validação de formato
+ comparação runtime
```

em vez de depender exclusivamente de offsets fixos.

---

## Crédito

Pesquisa, testes, depuração e validações de gameplay: **Atm**.

Este arquivo foi organizado para servir como referência pública a tradutores, ROM hackers, pesquisadores e desenvolvedores de ferramentas que desejem estudar Harvest Moon DS e continuar a pesquisa.
