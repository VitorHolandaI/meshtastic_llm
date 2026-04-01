# Meshtastic ↔ OpenVINO LLM Gateway

Gateway que recebe mensagens de texto via rádio LoRa (Meshtastic), consulta um LLM local (OpenVINO) e devolve a resposta pelo rádio. Funciona sem internet — ideal para situações sem energia ou conectividade.

```
[Rádio remoto] ──LoRa──> [Dispositivo Meshtastic] ──USB/WiFi──> [Gateway + LLM] ──> [resposta pelo rádio]
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

## Preparar o modelo

O gateway usa modelos no formato OpenVINO IR (`.xml` + `.bin`). Você pode converter modelos do Hugging Face usando o `optimum-intel`:

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
python mesh_llm_gateway.py
```

Seleciona modelo e dispositivo de compute via menus numerados.

### Modo por argumentos

```bash
# Serial (mais comum — cabo USB)
python mesh_llm_gateway.py --port /dev/ttyUSB0 --model ./qwen2.5-1.5b-int4 --device CPU

# Windows
python mesh_llm_gateway.py --port COM3 --model ./qwen2.5-1.5b-int4 --device CPU

# TCP (dispositivo Meshtastic com WiFi habilitado)
python mesh_llm_gateway.py --host 192.168.1.10 --model ./qwen2.5-1.5b-int4 --device CPU

# NPU Intel (mais rápido em laptops com Core Ultra)
python mesh_llm_gateway.py --port /dev/ttyUSB0 --model ./qwen2.5-1.5b-int4 --device NPU

# AUTO (OpenVINO escolhe o melhor dispositivo disponível)
python mesh_llm_gateway.py --port /dev/ttyUSB0 --model ./qwen2.5-1.5b-int4 --device AUTO
```

### Todos os argumentos

| Argumento  | Descrição                                              | Padrão              |
|------------|--------------------------------------------------------|---------------------|
| `--port`   | Porta serial do dispositivo Meshtastic                 | auto-detect         |
| `--host`   | IP do dispositivo Meshtastic via TCP (WiFi)            | —                   |
| `--model`  | Caminho para o diretório do modelo OpenVINO            | menu interativo     |
| `--device` | Dispositivo de inferência: `CPU`, `GPU`, `NPU`, `AUTO` | menu interativo     |

> `--port` e `--host` são mutuamente exclusivos.

---

## Como usar pelo rádio

Do lado do rádio remoto (ex: app Meshtastic no celular):

1. **Abra o canal** correto (o mesmo que o gateway está ouvindo)
2. **Envie uma mensagem direta (DM)** para o nó do gateway
3. Aguarde a resposta — pode vir em múltiplas partes `[1/2]`, `[2/2]` para respostas longas

### Comando especial

| Mensagem | Efeito                                      |
|----------|---------------------------------------------|
| `!reset` | Limpa o histórico de conversa do seu nó     |

---

## Comportamento

- **Histórico por nó:** cada rádio remoto tem sua própria sessão de conversa independente
- **Compressão automática:** quando o histórico fica longo, o gateway resume automaticamente as mensagens antigas para caber no contexto do LLM
- **Chunking:** respostas longas são divididas em pacotes de até 200 bytes para respeitar o limite do protocolo Meshtastic
- **Fila de processamento:** múltiplas mensagens simultâneas são processadas sequencialmente (o rádio listener nunca bloqueia)

---

## Recomendações de modelo por hardware

| Hardware              | Modelo recomendado           | Quantização | RAM aprox. |
|-----------------------|------------------------------|-------------|------------|
| CPU moderno (8+ cores) | Qwen2.5-3B-Instruct         | int4        | ~2 GB      |
| CPU limitado           | Qwen2.5-1.5B-Instruct       | int4        | ~1 GB      |
| Intel Arc / iGPU       | Phi-3.5-mini-instruct       | int4        | ~2 GB      |
| NPU Intel Core Ultra   | Qwen2.5-1.5B-Instruct       | int4        | —          |

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
