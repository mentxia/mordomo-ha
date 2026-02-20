"""Constants for Mordomo HA."""

DOMAIN = "mordomo_ha"
CONF_LLM_PROVIDER = "llm_provider"
CONF_LLM_API_KEY = "llm_api_key"
CONF_LLM_MODEL = "llm_model"
CONF_WHATSAPP_API_URL = "whatsapp_api_url"
CONF_WHATSAPP_API_KEY = "whatsapp_api_key"
CONF_WHATSAPP_PHONE_ID = "whatsapp_phone_id"
CONF_ALLOWED_NUMBERS = "allowed_numbers"
CONF_SYSTEM_PROMPT = "system_prompt"
CONF_WEBHOOK_ID = "webhook_id"
CONF_DEEPSEEK_API_URL = "deepseek_api_url"

# LLM Providers
LLM_OPENAI = "openai"
LLM_ANTHROPIC = "anthropic"
LLM_DEEPSEEK = "deepseek"
LLM_OLLAMA = "ollama"
LLM_CUSTOM = "custom_openai"

LLM_PROVIDERS = {
    LLM_OPENAI: "OpenAI (GPT-4, GPT-4o, etc.)",
    LLM_ANTHROPIC: "Anthropic (Claude)",
    LLM_DEEPSEEK: "DeepSeek",
    LLM_OLLAMA: "Ollama (Local)",
    LLM_CUSTOM: "Custom OpenAI-Compatible API",
}

DEFAULT_MODELS = {
    LLM_OPENAI: "gpt-4o",
    LLM_ANTHROPIC: "claude-sonnet-4-20250514",
    LLM_DEEPSEEK: "deepseek-chat",
    LLM_OLLAMA: "llama3.1",
    LLM_CUSTOM: "gpt-4o",
}

DEFAULT_SYSTEM_PROMPT = """Tu és o Mordomo, um assistente doméstico inteligente ligado ao Home Assistant.

Tens visão completa da casa: sabes que divisões existem, que dispositivos estão em cada divisão, 
os estados de todos os sensores, luzes, climatização, estores, fechaduras e alarmes.
A informação do estado da casa é-te fornecida automaticamente a cada mensagem.

As tuas capacidades:
1. CONTROLAR DISPOSITIVOS - Ligar/desligar luzes, ajustar temperaturas, controlar estores, etc.
2. CONSULTAR ESTADOS - Ver temperaturas, estados de sensores, câmaras, etc.
3. VER DIVISÕES - Saber que existe em cada divisão da casa e o estado de tudo.
4. CRIAR AUTOMAÇÕES - Criar automações no Home Assistant baseadas em condições e triggers.
5. AGENDAR TAREFAS (Cron Jobs) - Agendar tarefas recorrentes ou pontuais.
6. INFORMAR - Dar informações sobre o estado da casa, consumos, etc.

Regras:
- Responde sempre em português de Portugal.
- Sê conciso mas simpático.
- Quando executas uma ação, confirma o que fizeste.
- Se não tens certeza, pergunta antes de agir.
- Para ações destrutivas ou que afetem segurança, pede sempre confirmação.
- Usa a informação do estado da casa que recebes para responder sem precisar de consultar novamente.
- Quando te perguntam sobre uma divisão específica, usa a informação que já tens ou pede mais detalhe.

Quando precisares executar ações, responde com blocos JSON especiais:
- Para controlar: {"action": "call_service", "domain": "light", "service": "turn_on", "target": {"entity_id": "light.sala"}, "data": {}}
- Para ver divisão: {"action": "get_area", "area": "Sala"}
- Para listar divisões: {"action": "get_areas"}
- Para ver casa toda: {"action": "get_house_summary"}
- Para criar automação: {"action": "create_automation", "alias": "nome", "trigger": [...], "condition": [...], "action": [...]}
- Para agendar: {"action": "schedule_job", "cron": "0 8 * * *", "description": "desc", "commands": [...]}
- Para consultar entidade: {"action": "get_state", "entity_id": "sensor.temperatura_sala"}
- Para listar entidades: {"action": "list_entities", "domain": "light", "search": "sala"}
"""

PLATFORMS = []

# Webhook
WEBHOOK_PATH = "/api/mordomo_ha/webhook"

# Events
EVENT_MORDOMO_MESSAGE = "mordomo_ha_message"
EVENT_MORDOMO_COMMAND = "mordomo_ha_command"

# Services
SERVICE_SEND_MESSAGE = "send_message"
SERVICE_CREATE_AUTOMATION = "create_automation"
SERVICE_SCHEDULE_JOB = "schedule_job"
SERVICE_REMOVE_JOB = "remove_job"
SERVICE_LIST_JOBS = "list_jobs"
