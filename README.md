# Meshtastic ↔ OpenVINO LLM Gateway

[![tests](https://github.com/VitorHolandaI/meshtastic_llm/actions/workflows/tests.yml/badge.svg)](https://github.com/VitorHolandaI/meshtastic_llm/actions/workflows/tests.yml)
![coverage](https://img.shields.io/badge/coverage-98%25-brightgreen)

Gateway que recebe mensagens de texto via rádio LoRa (Meshtastic), consulta um LLM local (OpenVINO) e devolve a resposta pelo rádio. Funciona sem internet — ideal para situações sem energia ou conectividade.

```
[Rádio remoto] ──LoRa──> [Dispositivo Meshtastic] ──USB/WiFi──> [Gateway + LLM] ──> [resposta pelo rádio]
```

---

## Estrutura do projeto

```
chat_mesh/
├── main.py                    # Ponto de entrada principal
├── chat_mesh/
│   ├── config.py              # Constantes e tunables
│   ├── llm/
│   │   ├── pipeline.py        # Carregamento do modelo OpenVINO
│   │   └── prompt.py          # Construção de prompt, compressão de histórico, streaming
│   └── mesh/
│       ├── gateway.py         # MeshLLMGateway — sessões, ACK, envio
│       └── radio.py           # Chunking de pacotes, descoberta de modelos, menus
├── .env                       # Configuração local (não commitado)
├── .env_dev                   # Template — copie para .env e preencha
├── requirements.txt
└── changes.md                 # Histórico de mudanças e documentação técnica
```

---

## Requisitos

### Hardware

- 1 dispositivo Meshtastic conectado ao computador (ex: T-Beam, Heltec, RAK4631) via USB serial ou WiFi
- 1 ou mais rádios Meshtastic remotos para enviar mensagens

### Software

```bash
pip install -r requirements.txt
```

> **Nota:** O `openvino-genai` requer Python 3.9–3.12. Se usar NPU Intel, instale também os drivers NPU do seu sistema operacional.

---

## Configuração

Todas as opções podem ser passadas via argumentos CLI ou variáveis de ambiente. Copie `.env_dev` para `.env` e preencha:

```bash
cp .env_dev .env
```

| Variável de ambiente | Argumento CLI    | Padrão          | Descrição                                              |
|----------------------|------------------|-----------------|--------------------------------------------------------|
| `MESH_PORT`          | `--port`         | auto-detect     | Porta serial do dispositivo Meshtastic                 |
| `MESH_HOST`          | `--host`         | —               | IP do dispositivo Meshtastic via TCP (WiFi)            |
| `MESH_MODEL`         | `--model`        | menu interativo | Caminho para o diretório do modelo OpenVINO            |
| `MESH_DEVICE`        | `--device`       | menu interativo | Dispositivo de inferência: `CPU`, `GPU`, `NPU`, `AUTO` |
| `MESH_REPLY_MODE`    | `--reply-mode`   | `dm`            | Modo de resposta (ver abaixo)                          |
| `MESH_CHANNEL_PSK`   | `--channel-psk`  | `AQ==`          | PSK do canal 0 em Base64 (informativo)                 |

> `--port` e `--host` são mutuamente exclusivos.

---

## Preparar o modelo

O gateway usa modelos no formato OpenVINO IR (`.xml` + `.bin`). Converta modelos do Hugging Face com o `optimum-intel`:

```bash
pip install optimum[openvino]

# Exemplo: Qwen2.5-1.5B (leve, rápido, bom para CPU/NPU)
optimum-cli export openvino \
    --model Qwen/Qwen2.5-1.5B-Instruct \
    --weight-format int4 \
    ./qwen2.5-1.5b-int4

# Exemplo: Phi-3.5-mini (boa qualidade, ~2GB int4)
optimum-cli export openvino \
    --model microsoft/Phi-3.5-mini-instruct \
    --weight-format int4 \
    ./phi3.5-mini-int4
```

---

## Uso

### Modo interativo (menus)

```bash
python main.py
```

Seleciona modelo e dispositivo via menus numerados.

### Modo por argumentos

```bash
# Serial (cabo USB)
python main.py --port /dev/ttyUSB0 --model ./qwen2.5-1.5b-int4 --device CPU

# Windows
python main.py --port COM3 --model ./qwen2.5-1.5b-int4 --device CPU

# TCP (dispositivo com WiFi)
python main.py --host 192.168.1.10 --model ./qwen2.5-1.5b-int4 --device CPU

# NPU Intel (mais rápido em laptops com Core Ultra)
python main.py --port /dev/ttyUSB0 --model ./qwen2.5-1.5b-int4 --device NPU

# Respostas visíveis para todos no canal 0 (LongFast)
python main.py --reply-mode broadcast
```

Para ver todos os argumentos com exemplos:

```bash
python main.py --help
```

---

## Modos de resposta (`--reply-mode`)

### `dm` (padrão)

A resposta é enviada diretamente ao nó que enviou a mensagem, com confirmação de entrega (ACK). Se o pacote não for confirmado após 3 retransmissões do firmware, o gateway para de enviar e loga um aviso.

- Apenas o remetente vê a resposta
- ACK garantido: se a mensagem chegou, as chaves já batem
- Ideal para uso privado

### `broadcast`

A resposta é enviada no canal 0 (LongFast) e fica visível para todos os nós com a mesma chave do canal. A resposta é prefixada com `@<node_id>:` para identificar quem perguntou.

- Todos os nós no canal 0 veem a resposta
- Todos os dispositivos devem compartilhar o mesmo PSK do canal 0
- Ideal para uso em grupo / público

---

## Criptografia

O Meshtastic criptografa todas as mensagens com AES-256 no nível do firmware. O gateway só vê texto plano — a criptografia é transparente.

O `MESH_CHANNEL_PSK` no `.env` é **informativo**: documenta a chave configurada nos dispositivos físicos. Para aplicar uma chave via CLI:

```bash
# Chave pública padrão (sem privacidade real)
meshtastic --ch-index 0 --ch-set psk default

# Chave privada personalizada
meshtastic --ch-index 0 --ch-set psk base64:SUA_CHAVE_BASE64

# Gerar uma chave privada
openssl rand -base64 32
```

---

## Como usar pelo rádio

1. **Abra o canal correto** no app Meshtastic (mesmo canal que o gateway está ouvindo)
2. **Envie uma mensagem** diretamente para o nó do gateway (modo `dm`) ou no canal (modo `broadcast`)
3. Aguarde a resposta — respostas longas chegam em partes `[1/2]`, `[2/2]`

### Comandos especiais

| Mensagem | Efeito                                  |
|----------|-----------------------------------------|
| `!reset` | Limpa o histórico de conversa do seu nó |
| `/reset` | Mesmo efeito                            |

---

## Comportamento

- **Histórico por nó:** cada rádio remoto tem sua própria sessão de conversa independente
- **Compressão automática:** quando o histórico fica longo, o gateway resume automaticamente as mensagens antigas para caber no contexto do LLM
- **Resposta completa primeiro:** o LLM gera a resposta inteira antes de qualquer pacote ser enviado — nunca envia respostas parciais
- **ACK por chunk:** no modo `dm`, cada chunk aguarda confirmação de entrega antes de enviar o próximo
- **Chunking:** respostas longas são divididas em pacotes de até 200 bytes para respeitar o limite do protocolo Meshtastic

---

## Modelos recomendados

| Modelo                    | RAM (int4) | CPU 8-core | NPU Core Ultra | Indicado para               |
|---------------------------|-----------|------------|----------------|-----------------------------|
| Qwen2.5-1.5B-Instruct     | ~900 MB   | 2–5 s      | 1–3 s          | Qualquer hardware, padrão   |
| Qwen2.5-3B-Instruct       | ~1.8 GB   | 5–10 s     | 2–5 s          | CPU moderno, iGPU           |
| Phi-3.5-mini-instruct     | ~2.2 GB   | 8–15 s     | não suportado  | Perguntas técnicas, Arc GPU |

Use quantização `int4` para uso em rádio — menor e mais rápido com mínima perda de qualidade.

---

## Encontrar a porta serial no Linux

```bash
ls /dev/ttyUSB* /dev/ttyACM*
# ou
dmesg | grep tty | tail -5
```

Se aparecer "Permission denied":

```bash
sudo usermod -aG dialout $USER
# fazer logout/login após
```
