# Contribuindo

Obrigado por querer mexer no HMDS Character Translation Studio.

O projeto nasceu de uma fan translation e continua com essa ideia: tornar o trabalho de tradução/research de Harvest Moon DS mais acessível sem transformar a ferramenta algo complexo.

## Antes de alterar o código

- Rode os testes: `py -3.13 -m unittest discover -s tests -v`.
- Para novas integrações, prefira `cts.api`.
- Evite fazer ferramentas externas dependerem de métodos privados de `cts.ui`.
- Não invente nomes/semântica para controles ou Character IDs ainda não confirmados.
- Uma entrada ruim num arquivo de importação nunca deve derrubar o pacote inteiro.
- A ROM original nunca deve ser sobrescrita por padrão.

## Pull requests que combinam com o projeto

- correções de parser/rebuild;
- melhorias de compatibilidade entre ROMs HMDS normais;
- documentação de controles confirmados;
- melhorias de acessibilidade/UX para tradutores;
- testes;
- API para ferramentas externas;
- melhorias na importação/exportação;
- novas línguas da interface.

## Licença

Contribuições aceitas passam a ser distribuídas sob a mesma **Atm Non-Commercial Source License 1.0** do projeto. Isso permite estudo, forks e ferramentas derivadas não comerciais, mas não venda.
