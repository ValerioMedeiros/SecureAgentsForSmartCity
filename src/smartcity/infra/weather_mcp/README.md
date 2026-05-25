# Weather Forecast MCP Server

Servidor MCP de previsão do tempo para agentes LLM, integrado ao pipeline MAPE-K do sistema de cidades inteligentes.

Utiliza a API [Open-Meteo](https://open-meteo.com) — gratuita, sem necessidade de chave de API ou cadastro, com cobertura global via modelo meteorológico ECMWF.

---

## Estrutura do módulo

```
weather_mcp/
├── __init__.py
├── weather_client.py   # Cliente HTTP para a API Open-Meteo
├── server.py           # Servidor FastMCP com ferramentas e resources
└── README.md           # Este arquivo
```

---

## Por que Open-Meteo?

| Critério | Open-Meteo | OpenWeatherMap | WeatherAPI.com | INMET |
|---|---|---|---|---|
| Gratuito | Sim, ilimitado | Tier gratuito limitado | Tier gratuito limitado | Sim |
| Chave de API | **Não precisa** | Obrigatória | Obrigatória | Cadastro burocrático |
| Cobertura Brasil | Excelente (ECMWF) | Boa | Boa | Oficial BR |
| Complexidade de uso | Muito simples | Média | Média | Alta |
| Modelo meteorológico | ECMWF (~9km) | Próprio | Próprio | INMET |
| Atualização | Horária | Horária | Horária | Variável |

A escolha por Open-Meteo elimina toda fricção de setup: nenhuma variável de ambiente, nenhuma chave, nenhum cadastro. Basta um GET HTTP e os dados chegam.

---

## Inicialização

```bash
# Transport HTTP (padrão, porta 8002)
uv run weather-mcp-server http 8002

# Transport stdio (para integração direta com agentes Claude)
uv run weather-mcp-server stdio
```

O entry point `weather-mcp-server` está registrado em `pyproject.toml`:

```toml
[project.scripts]
weather-mcp-server = "smartcity.infra.weather_mcp.server:main"
```

Não há variáveis de ambiente obrigatórias. O servidor funciona imediatamente após `uv run`.

---

## Ferramentas MCP disponíveis

### `get_current_weather`

Retorna as condições climáticas em tempo real para uma localização.

**Entrada:**

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `latitude` | `float` | obrigatório | Latitude em graus decimais (ex: `-23.5505`) |
| `longitude` | `float` | obrigatório | Longitude em graus decimais (ex: `-46.6333`) |
| `timezone` | `str` | `America/Sao_Paulo` | Fuso horário IANA |

**Saída:**

```json
{
  "latitude": -23.5505,
  "longitude": -46.6333,
  "timezone": "America/Sao_Paulo",
  "time": "2026-05-24T22:15",
  "temperature_c": 16.8,
  "humidity_pct": 98,
  "precipitation_mm": 0.0,
  "wind_speed_kmh": 12.3,
  "wind_gusts_kmh": 18.0,
  "weather_code": 3,
  "weather_description": "Nublado"
}
```

---

### `get_hourly_forecast`

Retorna previsão climática hora a hora para as próximas N horas.

**Entrada:**

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `latitude` | `float` | obrigatório | Latitude em graus decimais |
| `longitude` | `float` | obrigatório | Longitude em graus decimais |
| `hours` | `int` | `24` | Horas a prever (1–384, máximo 16 dias) |
| `timezone` | `str` | `America/Sao_Paulo` | Fuso horário IANA |

**Saída:**

```json
{
  "latitude": -23.5505,
  "longitude": -46.6333,
  "timezone": "America/Sao_Paulo",
  "hours_requested": 24,
  "forecast": [
    {
      "time": "2026-05-24T23:00",
      "temperature_c": 16.1,
      "humidity_pct": 97,
      "precipitation_probability_pct": 45,
      "precipitation_mm": 0.2,
      "wind_speed_kmh": 9.8,
      "wind_gusts_kmh": 14.0,
      "weather_code": 61,
      "weather_description": "Chuva leve"
    }
  ]
}
```

O campo `forecast` contém uma entrada por hora solicitada.

---

### `get_daily_forecast`

Retorna previsão climática com granularidade diária.

**Entrada:**

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `latitude` | `float` | obrigatório | Latitude em graus decimais |
| `longitude` | `float` | obrigatório | Longitude em graus decimais |
| `days` | `int` | `7` | Dias a prever (1–16) |
| `timezone` | `str` | `America/Sao_Paulo` | Fuso horário IANA |

**Saída:**

```json
{
  "latitude": -23.5505,
  "longitude": -46.6333,
  "timezone": "America/Sao_Paulo",
  "days_requested": 7,
  "forecast": [
    {
      "date": "2026-05-25",
      "temperature_max_c": 22.4,
      "temperature_min_c": 14.1,
      "precipitation_sum_mm": 3.8,
      "precipitation_probability_max_pct": 65,
      "wind_speed_max_kmh": 18.0,
      "wind_gusts_max_kmh": 32.0,
      "weather_code": 63,
      "weather_description": "Chuva moderada"
    }
  ]
}
```

---

### `get_rainfall_risk`

Avalia o risco de alagamento para as próximas N horas com base na previsão de precipitação.

Esta ferramenta não existe diretamente na API Open-Meteo — ela é **derivada**: chama `get_hourly_forecast()` internamente e agrega os dados para produzir uma análise de risco consolidada.

A classificação utiliza os limiares de referência do **CEMADEN** (Centro Nacional de Monitoramento e Alertas de Desastres Naturais):

| Risco | Condição |
|---|---|
| `crítico` | Acumulado ≥ 50mm **ou** máximo horário ≥ 20mm/h |
| `alto` | Acumulado ≥ 20mm **ou** máximo horário ≥ 10mm/h |
| `médio` | Acumulado ≥ 5mm **ou** probabilidade máxima ≥ 70% |
| `baixo` | Abaixo de todos os limiares acima |

**Entrada:**

| Parâmetro | Tipo | Padrão | Descrição |
|---|---|---|---|
| `latitude` | `float` | obrigatório | Latitude em graus decimais |
| `longitude` | `float` | obrigatório | Longitude em graus decimais |
| `hours` | `int` | `6` | Janela de análise em horas (1–48) |
| `timezone` | `str` | `America/Sao_Paulo` | Fuso horário IANA |

**Saída:**

```json
{
  "latitude": -23.5505,
  "longitude": -46.6333,
  "timezone": "America/Sao_Paulo",
  "hours_analyzed": 6,
  "total_precipitation_mm": 12.4,
  "max_hourly_precipitation_mm": 4.1,
  "max_precipitation_probability_pct": 78,
  "flood_risk": "médio",
  "hourly_breakdown": [
    {
      "time": "2026-05-24T23:00",
      "precipitation_mm": 0.2,
      "precipitation_probability_pct": 45
    }
  ]
}
```

---

## Resources MCP (contexto somente leitura)

Resources são URIs que expõem dados como contexto passivo — o agente os lê antes de raciocinar, sem precisar fazer uma chamada ativa de ferramenta.

| URI | Descrição |
|---|---|
| `weather://current/{latitude}/{longitude}` | Condições atuais na localização |
| `weather://rainfall-risk/{latitude}/{longitude}` | Risco de alagamento para as próximas 6 horas |

---

## Códigos WMO de condição climática

A API Open-Meteo retorna um código WMO inteiro para cada leitura. O cliente traduz automaticamente para português. Os códigos mais relevantes para o contexto de cidades inteligentes:

| Código | Descrição |
|---|---|
| 0–3 | Céu limpo a nublado |
| 51–55 | Garoa (leve a densa) |
| 61–65 | Chuva (leve a forte) |
| 80–82 | Pancadas de chuva (leves a violentas) |
| 95 | Tempestade |
| 96, 99 | Tempestade com granizo |

---

## Fusos horários brasileiros

| Fuso IANA | Região |
|---|---|
| `America/Sao_Paulo` | Sudeste, Sul, Centro-Oeste (BR-) |
| `America/Fortaleza` | Nordeste (exceto MA e PI) |
| `America/Recife` | Pernambuco e estados vizinhos |
| `America/Belem` | Pará, Amapá, Maranhão |
| `America/Manaus` | Amazonas, Roraima, Rondônia |
| `America/Porto_Velho` | Rondônia |
| `America/Boa_Vista` | Roraima |
| `America/Noronha` | Fernando de Noronha (UTC-2) |

---

## Coordenadas de referência para cidades brasileiras

| Cidade | Latitude | Longitude |
|---|---|---|
| São Paulo | -23.5505 | -46.6333 |
| Rio de Janeiro | -22.9068 | -43.1729 |
| Belo Horizonte | -19.9167 | -43.9345 |
| Salvador | -12.9714 | -38.5014 |
| Fortaleza | -3.7172 | -38.5433 |
| Recife | -8.0476 | -34.8770 |
| Manaus | -3.1019 | -60.0250 |
| Belém | -1.4558 | -48.5044 |
| Brasília | -15.7797 | -47.9297 |
| Porto Alegre | -30.0277 | -51.2287 |
| Curitiba | -25.4284 | -49.2733 |

---

## Integração com o pipeline MAPE-K

O sistema já monitora estações meteorológicas físicas registradas no Orion Context Broker (dados históricos e em tempo real). Este servidor MCP adiciona a dimensão de **previsão futura**, permitindo decisões proativas:

```
Monitor  →  recebe dados atuais da WeatherStation via Orion (o que está acontecendo)
            chama get_rainfall_risk() para o período seguinte  (o que vem por aí)

Planner  →  usa o risco de alagamento para escolher o plano adequado:
              flood_risk = "alto"    →  CandidatePlan com turnOnPump (risco LOW, aprovação auto)
              flood_risk = "crítico" →  CandidatePlan com notifyUser (risco HIGH, aprovação humana)

Executor →  executa as ações aprovadas via MCP Server (porta 8000)
```

A ferramenta `get_rainfall_risk` é a mais diretamente acionável pelo planner, pois entrega uma decisão já consolidada (`flood_risk`) em vez de séries temporais brutas que o LLM teria que interpretar.

---

## Tratamento de erros

Todos os erros de rede e HTTP são capturados pelo `WeatherClient` e convertidos em `WeatherConnectionError`. O servidor MCP os intercepta e retorna um dicionário `{"error": "..."}` ao LLM — nunca uma exceção não tratada — seguindo o mesmo padrão do `fiware_mcp/server.py`.

```python
# Exemplo de resposta de erro
{
  "error": "Erro de conexão: Falha ao conectar com Open-Meteo: ..."
}
```
