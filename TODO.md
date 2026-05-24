- Criar modelo de Bomba Orion
- MCP da bomba -> Printa que ligou a bomba

- Criar modelo de Plano gerado por IA que contem ações relacionadas ao MCP da bomba

- Módulo de segurança: Criar usuários associados a permissões e a um token(senha),
criar função que recebe plano e retorna usuários que podem aprovar esse plano.

- OPA: Políticas que dizem se plano precisa de aprovação ou se pode ser executado diretamente.

- SEQUENCIA
> Notificacao Estacao -> Agente de IA -> (Weather Forecast MCP) -> Plano -> OPA -> Security Agent -> Chat Interface -> (Guardrails) -> Executar Ação -> Alegria.