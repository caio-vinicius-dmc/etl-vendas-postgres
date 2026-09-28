# etl-vendas-postgres

Um processo que lê arquivos de venda, confere linha por linha, guarda o
que está bom e separa o que está errado — sem usar nenhum framework.

## Do que se trata, em linguagem simples

**ETL** é a sigla para extrair, transformar e carregar. É o trabalho de
pegar dados de um lugar, arrumá-los e colocá-los em outro. Na prática:
todo dia de manhã chegam arquivos com as vendas do dia anterior, e alguém
precisa colocar isso no banco de dados que alimenta os relatórios.

Parece simples. O que complica é tudo que pode dar errado:

- uma linha vem com a data `31/02/2026`, que não existe
- outra vem com o valor escrito `R$ 1.234,00` em vez de `1234.00`
- o processo trava no meio e alguém precisa rodar de novo — e não pode
  duplicar o que já entrou
- o sistema de origem reenvia o arquivo de ontem corrigido

Este projeto resolve esses quatro casos. Sem framework, de propósito: a
ideia é mostrar o que o Airflow, o Dagster e os outros fazem por baixo do
pano.

## O que ele garante

**Rodar duas vezes não duplica nada.** A gravação identifica cada venda
pelo código dela. A segunda execução atualiza as mesmas linhas em vez de
criar cópias. Isso é o que torna seguro mandar rodar de novo depois de uma
falha.

**Nenhuma linha errada derruba o dia inteiro.** Registro inválido vai para
uma "quarentena", com o motivo anotado e o conteúdo original guardado. O
processo continua com o resto.

**Ou entra tudo, ou não entra nada.** A carga roda dentro de uma transação
única. Se algo estourar no meio, o banco volta ao estado anterior. Mas o
registro de que houve erro sobrevive, porque é gravado por uma conexão
separada.

**Dá para reprocessar qualquer período passado** sem bagunçar o que já foi
carregado depois.

## O que você precisa ter instalado

- **Docker Desktop** — sobe o banco de dados sem instalar nada permanente.
  [docker.com](https://www.docker.com/products/docker-desktop/)
- **Python 3.11 ou mais novo** —
  [python.org](https://www.python.org/downloads/), marcando "Add Python to
  PATH".

## Como rodar

**1. Configuração e banco.**

```bash
cp .env.example .env
docker compose up -d
```

**2. Ambiente do Python.**

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -r requirements.txt
```

No Linux ou macOS: `source .venv/bin/activate`.

**3. Crie os arquivos de origem.** Num sistema real eles chegariam de
fora; aqui o projeto gera trinta dias de vendas:

```bash
python -m src.cli gerar-fontes --dias 30 --de 2026-01-01
```

Cerca de 3% das linhas saem defeituosas **de propósito** — data
inexistente, valor com "R$", produto que não existe no catálogo. É o que
dá trabalho para a etapa de conferência.

**4. Crie as tabelas no banco.**

```bash
python -m src.cli migrar
```

**5. Rode o processo.**

```bash
python -m src.cli executar --de 2026-01-01 --ate 2026-01-30
```

**6. Veja o que aconteceu.**

```bash
python -m src.cli status
```

### Quando terminar

```bash
docker compose down -v
```

## O que sai

Trinta dias, quatrocentas vendas por dia:

```
Linhas lidas          12000
Gravadas              11621
Rejeitadas              379
```

E os motivos das rejeições, agrupados:

| Motivo | Linhas |
|--------|--------|
| valor unitário não numérico | 148 |
| cliente ausente ou não numérico | 150 |
| canal fora da lista conhecida | 132 |
| produto não existe no catálogo | 120 |
| quantidade precisa ser maior que zero | 110 |
| data fora do formato ou inexistente | 98 |

Rodando o mesmo comando de novo, o resultado é idêntico: 11.621 gravadas,
zero duplicadas. É a garantia de idempotência funcionando.

## Rodando de onde parou

Sem informar as datas, o processo continua do ponto em que estava, usando
uma "marca d'água" — o registro de até quando ele já processou:

```bash
python -m src.cli executar --ate 2026-02-15
```

Se não houver nada novo, ele avisa e encerra sem fazer nada.

## Reprocessando um período

```bash
python -m src.cli executar --de 2026-01-05 --ate 2026-01-10 --recarregar
```

Sem o `--recarregar`, o reprocessamento apenas atualiza as linhas que
vieram no arquivo. Com a opção, as vendas daquele período são apagadas
antes.

A diferença importa num caso específico: quando a origem **deixou de
mandar** um registro que antes existia. Só atualizar não faria essa linha
sumir do destino; apagar antes, sim. Como é mais caro, não é o padrão.

## Como os dados ficam organizados

Três áreas separadas dentro do banco, cada uma com um papel:

| Área | Para que serve |
|------|---------------|
| `stg` | área de pouso. Tudo entra como texto e é apagada a cada execução |
| `dw` | o dado já conferido e tipado: `dw.produto` e `dw.venda` |
| `ctl` | o controle do próprio processo: execuções, rejeitados e marca d'água |

A área de pouso guardar tudo como texto é proposital. Se as colunas
fossem tipadas ali, uma única linha com data inválida derrubaria a
importação inteira — e com ela as 12 mil linhas que estavam boas.

Depois de rodar, dá para consultar direto:

```sql
-- o que foi rejeitado e por quê
SELECT motivo, count(*) FROM ctl.rejeitado GROUP BY motivo ORDER BY 2 DESC;

-- faturamento por canal
SELECT canal, count(*) AS vendas, sum(valor_total) AS faturamento
  FROM dw.venda GROUP BY canal ORDER BY faturamento DESC;
```

## Formato do registro de atividade

No arquivo `.env`, a opção `FORMATO_LOG=text` dá a saída legível no
terminal. Trocando para `json`, cada evento vira uma linha estruturada,
pronta para ser lida por uma ferramenta de monitoramento:

```json
{"momento": "2026-01-30T22:45:45", "nivel": "info", "mensagem": "Validacao concluida", "validas": 11621, "rejeitadas": 379, "duplicadas": 0}
```

São dois formatos porque o uso é diferente: no terminal a gente quer ler,
em produção uma ferramenta quer campos separados.

## Estrutura das pastas

```
sql/001_estrutura.sql   as tabelas e índices
src/extracao.py         acha os arquivos do período e carrega no banco
src/transformacao.py    confere linha por linha e decide o que rejeitar
src/carga.py            grava no destino e na quarentena
src/pipeline.py         coordena tudo e controla a transação
src/gerador_fontes.py   cria os arquivos de origem
docs/arquitetura.md     as decisões de projeto e o que ficou de fora
```

## Problemas comuns

**"ports are not available" ou "bind: An attempt was made to access a socket
in a way forbidden by its access permissions".** O Windows reserva faixas de
porta para uso próprio, e elas mudam a cada reinício. Veja quais estão
reservadas com:

```bash
netsh int ipv4 show excludedportrange protocol=tcp
```

Se a porta do projeto estiver numa das faixas, mude `POSTGRES_PORT` no
arquivo `.env` para qualquer valor livre abaixo de 49152 e suba de novo.

**"O banco recusou a senha."** Você mudou a senha no `.env` depois de já
ter subido o banco. Recrie com `docker compose down -v && docker compose up -d`.

**"Nenhum arquivo de venda entre as datas."** Rode o `gerar-fontes`
primeiro, ou confira se as datas pedidas batem com as dos arquivos na
pasta `dados/entrada/`.

**"Catálogo de produtos não encontrado."** O `gerar-fontes` cria o
catálogo junto com os arquivos de venda. Se você apagou a pasta, rode de
novo.

## Limitações

- Os arquivos são lidos em sequência, sem paralelismo. Para o volume deste
  projeto não pesa, mas com centenas de arquivos por período valeria a
  pena dividir o trabalho.
- O resultado da conferência fica todo na memória. Para arquivos muito
  maiores seria preciso processar em pedaços.
- O controle das tabelas é um script com `IF NOT EXISTS`. Num projeto com
  várias pessoas mexendo, uma ferramenta como Alembic ou Flyway resolveria
  melhor.
- **Não há agendamento.** Quem manda o processo rodar é você, ou um cron.
  O projeto [airflow-pipeline](../airflow-pipeline) cobre essa parte.

---

## 👤 Autor

Desenvolvido por **Caio Vinícius Barbosa Barros**.

Se você tiver dúvidas, sugestões ou quiser reportar um problema, sinta-se à vontade para entrar em contato:

*   **✉️ E-mail:** [caio@dynamicmotioncentury.com.br](mailto:caio@dynamicmotioncentury.com.br)
*   **🌐 Site/Portfólio:** [www.dynamicmotioncentury.com.br](https://dynamicmotioncentury.com.br)
*   **💼 LinkedIn:** [linkedin.com/in/caio-vinicius-dmc](https://linkedin.com/in/caio-vinicius-dmc)
*   **🐙 GitHub:** [@caio-vinicius-dmc](https://github.com/caio-vinicius-dmc)

💡 *Se este projeto te ajudou, deixe uma ⭐ no repositório!*
