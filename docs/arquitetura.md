# Decisões de projeto

Anotações sobre o porque de algumas escolhas. Varias delas são respostas a
problemas que só apareceram quando a pipeline rodou de verdade.

## Staging com tudo em texto

A primeira versão tinha `stg.vendas` com colunas tipadas. Bastou uma linha
com `31/02/2026` para o `COPY` inteiro morrer -- e com ele os 12 mil
registros que estavam bons.

Hoje o staging e todo `text`. A conversão acontece em `transformacao.py`,
onde dá para capturar o erro, anotar o motivo e mandar só aquela linha para
a quarentena. A carga continua.

## Duas conexões, de propósito

A carga roda em uma transação única: ou a janela inteira entra, ou nada
entra. Isso é o que a gente quer para o dado.

O problema e que o registro de controle mora no mesmo banco. Se ele fosse
gravado na mesma transação, um erro no meio da carga faria o rollback apagar
também a linha que diz que houve erro -- a pipeline falharia em silêncio.

Por isso `pipeline.executar` abre uma conexão própria, em autocommit, só
para `ctl.execucao`. Ela grava o início, e no `except` grava o status de erro
antes de propagar a exceção.

## Marca d'agua com GREATEST

A marca d'agua guarda até que data a pipeline processou. Reprocessar uma
janela antiga não pode fazer ela retroceder: se o carregado vai até 30 de
janeiro e alguém reprocessa a semana do dia 5, a marca precisa continuar em
30, senao a próxima execução incremental recarregaria o mês inteiro.

O `ON CONFLICT ... SET ultima_data = GREATEST(...)` resolve isso em uma
linha de SQL.

## Upsert não resolve exclusão

O upsert por `id_venda` cobre inserção e atualização, e é o que torna a
pipeline idempotente. Mas ele não tem como saber que uma venda que existia
no destino sumiu da origem.

Dai a opção `--recarregar`: ela apaga as vendas da janela antes de gravar.
E mais cara e por isso não é o padrão -- na maioria dos dias o upsert basta.

## Integridade referencial checada antes do banco

`transformacao.py` verifica se o SKU existe no catálogo em vez de deixar a
foreign key reclamar. Duas razões: o erro vira uma mensagem legível na
quarentena (`produto SKU-9999 não existe no catálogo`) em vez de uma
violação de constraint, e a transação não precisa ser abortada e reiniciada.

A FK continua lá, como rede de segurança.

## Deduplicação dentro do lote

Quando o mesmo `id_venda` aparece duas vezes no mesmo lote, o
`ON CONFLICT DO UPDATE` falha com *cannot affect row a second time* --
o Postgres não deixa a mesma linha ser tocada duas vezes na mesma comando.

`remover_duplicatas` resolve mantendo a última ocorrência, que é a regra
razoável quando a origem reenvia um arquivo corrigido. O contador aparece no
resumo da execução para que a duplicidade não passe despercebida.

## COPY em vez de INSERT

A carga para o staging usa `COPY ... FROM STDIN`. Com 12 mil linhas a
diferença já é visível; com alguns milhões, e a diferença entre segundos e
minutos.

## O que ficou de fora

- **Agendamento.** Não tem. E proposital: o objetivo era o miolo da pipeline.
- **Particionamento de `dw.venda`.** Com dois anos de dados diários comecaria
  a fazer sentido particionar por mês.
- **Retentativa automática.** Se o banco cai no meio, a execução fica com
  status `erro` e alguém precisa rodar de novo. Como a pipeline e
  idempotente, rodar de novo é seguro -- mas não é automático.
- **Alerta.** O `status` mostra as rejeições, mas ninguém e avisado quando
  elas sobem. Esse e o assunto do projeto n8n-alertas-dados.
