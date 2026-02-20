# 🏠 Mordomo HA - WhatsApp Smart Butler for Home Assistant

Um mordomo inteligente para o Home Assistant, contactável via WhatsApp, com capacidade de controlar toda a casa, criar automações e agendar tarefas.

---

## ✨ Funcionalidades

- **🤖 Multi-LLM** - Suporta OpenAI, Anthropic (Claude), DeepSeek, Ollama (local) e qualquer API OpenAI-compatible
- **📱 WhatsApp** - Suporta Meta Cloud API, Evolution API, WAHA e Baileys
- **🏠 Controlo Total** - Liga/desliga luzes, climatização, estores, fechaduras, etc.
- **📊 Consulta de Estado** - Pergunta temperaturas, consumos, estados de sensores
- **⚙️ Criar Automações** - Cria automações diretamente no HA via conversa natural
- **⏰ Cron Jobs** - Agenda tarefas recorrentes com expressões cron
- **🔒 Segurança** - Whitelist de números autorizados
- **💬 Contexto** - Memória de conversa por utilizador
- **🇵🇹 Português** - Interface e respostas em português

---

## 📦 Instalação

### Via HACS (Recomendado)

1. Abre o HACS no Home Assistant
2. Vai a **Integrações** → **⋮** → **Repositórios personalizados**
3. Adiciona: `https://github.com/joao/mordomo-ha` (Categoria: Integração)
4. Procura "Mordomo HA" e instala
5. Reinicia o Home Assistant

### Manual

1. Copia a pasta `custom_components/mordomo_ha` para a tua pasta `config/custom_components/`
2. Reinicia o Home Assistant

---

## ⚙️ Configuração

### Passo 1: Adicionar a Integração

1. Vai a **Definições** → **Dispositivos e Serviços** → **Adicionar Integração**
2. Procura "Mordomo HA"
3. Segue o assistente de configuração:

### Passo 2: Escolher o LLM

| Provedor | Vantagens | Custo |
|----------|-----------|-------|
| **OpenAI** | Melhor qualidade geral, GPT-4o | ~$2-5/mês uso moderado |
| **Anthropic** | Claude, excelente em PT | ~$3-8/mês uso moderado |
| **DeepSeek** | Muito barato, boa qualidade | ~$0.10-0.50/mês |
| **Ollama** | Grátis, local, privado | Só hardware |
| **Custom** | Qualquer API OpenAI-compatible | Variável |

### Passo 3: Configurar o WhatsApp

#### Opção A: Evolution API (Recomendado para self-hosted)

```bash
# Docker compose para Evolution API
docker run -d \
  --name evolution-api \
  -p 8080:8080 \
  -e AUTHENTICATION_API_KEY=sua-chave-aqui \
  atendai/evolution-api:latest
```

Depois configura no Mordomo:
- **Gateway**: Evolution API
- **URL**: `http://seu-ip:8080`
- **API Key**: A chave definida acima
- **Instance**: Nome da instância (ex: `mordomo`)

#### Opção B: WAHA

```bash
docker run -d \
  --name waha \
  -p 3000:3000 \
  devlikeapro/waha:latest
```

#### Opção C: Meta Cloud API (Oficial)

1. Cria uma app em [developers.facebook.com](https://developers.facebook.com)
2. Ativa o produto WhatsApp Business
3. Obtém o token de acesso e Phone ID
4. Configura o webhook no Meta para apontar ao teu HA

### Passo 4: Configurar o Webhook

O Mordomo regista automaticamente um webhook no HA. Precisas de configurar a tua gateway WhatsApp para enviar mensagens para:

```
https://teu-ha.duckdns.org/api/webhook/mordomo_ha_XXXXX
```

O URL exato aparece nos logs do HA quando o componente inicia.

**Importante**: Precisas de HTTPS com um domínio público (ex: DuckDNS + Let's Encrypt).

### Passo 5: Segurança

Define os números autorizados (formato internacional sem +):
```
351912345678,351967654321
```

---

## 💬 Como Usar

### Exemplos de Conversas

```
Tu: Liga a luz da sala
Mordomo: ✅ Executado: light.turn_on em light.sala
         Luz da sala ligada! 💡

Tu: Qual a temperatura da sala?
Mordomo: 📊 Sensor Temperatura Sala: 22.3 °C
         A temperatura está confortável!

Tu: Cria uma automação para ligar a luz da entrada quando
    o sensor de movimento detectar movimento depois das 18h
Mordomo: ✅ Automação 'Luz entrada com movimento' criada!
         Trigger: sensor de movimento
         Condição: depois das 18:00
         Ação: ligar light.entrada

Tu: Agenda para todos os dias às 7h30 abrir os estores
Mordomo: ⏰ Tarefa agendada: 'Abrir estores de manhã'
         Cron: 30 7 * * *
         Próxima execução: amanhã às 07:30

Tu: Que luzes estão ligadas?
Mordomo: 📊 Luzes ligadas:
         • Luz Sala: on (brightness: 80%)
         • Luz Cozinha: on (brightness: 100%)
         As restantes estão desligadas.
```

### Comandos Especiais

| Comando | Descrição |
|---------|-----------|
| `/ajuda` | Mostra ajuda |
| `/limpar` | Limpa histórico de conversa |
| `/tarefas` | Lista tarefas agendadas |
| `/estado` | Mostra estado geral da casa |

---

## 🔧 Serviços HA

O Mordomo regista serviços que podes usar em automações:

### `mordomo_ha.send_message`
Envia mensagem WhatsApp.
```yaml
service: mordomo_ha.send_message
data:
  phone: "351912345678"
  message: "Alerta: porta da frente aberta!"
```

### `mordomo_ha.create_automation`
Cria uma automação via serviço.

### `mordomo_ha.schedule_job`
Agenda uma tarefa com cron.
```yaml
service: mordomo_ha.schedule_job
data:
  cron: "0 8 * * 1-5"
  description: "Ligar aquecimento dias úteis"
  commands:
    - action: call_service
      domain: climate
      service: set_temperature
      target:
        entity_id: climate.sala
      data:
        temperature: 22
```

### `mordomo_ha.list_jobs`
Lista tarefas agendadas (resultado via evento `mordomo_ha_jobs_list`).

---

## 🏗️ Arquitetura

```
┌─────────────┐     ┌──────────────┐     ┌─────────────┐
│  WhatsApp    │────▶│  Webhook HA  │────▶│  Mordomo HA │
│  (Telemóvel) │◀────│              │◀────│  Component  │
└─────────────┘     └──────────────┘     └──────┬──────┘
                                                │
                    ┌───────────────────────────┤
                    │              │             │
              ┌─────▼─────┐ ┌─────▼─────┐ ┌────▼─────┐
              │ LLM Engine│ │ Command   │ │ Scheduler│
              │ (Multi)   │ │ Processor │ │ (Cron)   │
              └─────┬─────┘ └─────┬─────┘ └──────────┘
                    │             │
              ┌─────▼─────┐ ┌────▼──────────┐
              │ OpenAI /  │ │ Home Assistant │
              │ Claude /  │ │ Services API   │
              │ DeepSeek/ │ │ States API     │
              │ Ollama    │ │ Automations    │
              └───────────┘ └───────────────┘
```

---

## 🔍 Troubleshooting

### Webhook não recebe mensagens
- Verifica se tens HTTPS configurado no HA
- Verifica o URL do webhook nos logs: `grep mordomo_ha home-assistant.log`
- Testa com curl: `curl -X POST https://teu-ha/api/webhook/mordomo_ha_XXX -d '{"test": true}'`

### LLM não responde
- Verifica a API key nas opções da integração
- Para Ollama: confirma que está a correr (`curl http://localhost:11434/api/tags`)
- Verifica logs: `grep mordomo_ha home-assistant.log`

### Mensagem "não autorizado"
- Confirma que o número está no formato correto (sem + e sem espaços)
- Exemplo: `351912345678` (código país + número)

---

## 📝 Roadmap

- [ ] Painel Lovelace com histórico de conversas
- [ ] Suporte para mensagens de voz (speech-to-text)
- [ ] Suporte para envio de imagens (câmaras)
- [ ] Grupos de WhatsApp
- [ ] Múltiplos idiomas no system prompt
- [ ] Dashboard de custos de LLM
- [ ] Integração com Google Calendar
- [ ] Backup/restore de configuração

---

## 📄 Licença

MIT License
