# Spark LLM Router для DGX Spark

Сервис решает задачу запуска **одной активной LLM за раз** с автоматическим переключением между моделями, чтобы экономить GPU/CPU память.

## Что делает решение

- Поднимает единый OpenAI-совместимый endpoint: `POST /v1/chat/completions`.
- По полю `model` определяет нужную модель.
- Если запрошена другая модель:
  1. останавливает текущий backend,
  2. запускает backend нужной модели,
  3. проверяет health,
  4. проксирует запрос в backend.
- Отдает список моделей через `GET /v1/models`.
- Позволяет ручное переключение через `POST /admin/switch/{model_name}`.

## Быстрый старт

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/models.example.yaml config/models.yaml
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

> Проверьте команды запуска моделей в `config/models.yaml` под вашу версию vLLM/моделей.

## Как установить это на DGX Spark и пользоваться после перезагрузки

### 1) Скопировать проект на DGX

Если репозиторий в git:

```bash
git clone <repo-url> /opt/spark-llm-router
cd /opt/spark-llm-router
```

Если без git — через `scp/rsync` в папку, например `/opt/spark-llm-router`.

### 2) Подготовить Python-окружение

```bash
cd /opt/spark-llm-router
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config/models.example.yaml config/models.yaml
```

Отредактируйте `config/models.yaml` под ваши реальные команды запуска моделей (пути, `CUDA_VISIBLE_DEVICES`, флаги vLLM, `--max-model-len`).

### 3) Проверить ручной запуск

```bash
source .venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8080
```

Проверка:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/v1/models
```

### 4) Установить как systemd-сервис (автозапуск после ребута)

Скопируйте unit-файлы:

```bash
sudo cp deploy/spark-llm-router.service /etc/systemd/system/spark-llm-router@.service
sudo cp deploy/jupyter-lab.service /etc/systemd/system/jupyter-lab@.service
```

Важно: в `deploy/spark-llm-router.service` проверьте `WorkingDirectory` и `ExecStart` (они должны указывать на фактический путь проекта на DGX).

Включение автозапуска (замените `<user>` на вашего Linux-пользователя):

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now spark-llm-router@<user>
sudo systemctl enable --now jupyter-lab@<user>
```

### 5) Как пользоваться дальше (ежедневно)

- После перезагрузки DGX сервисы поднимутся автоматически (`enable`).
- Проверить статус:

```bash
systemctl status spark-llm-router@<user>
systemctl status jupyter-lab@<user>
```

- Логи:

```bash
journalctl -u spark-llm-router@<user> -f
journalctl -u jupyter-lab@<user> -f
```

- Доступ с ноутбука через tailnet:
  - LLM API: `http://<tailscale-ip-dgx>:8080/v1/chat/completions`
  - Jupyter: `http://<tailscale-ip-dgx>:8888/?token=...`

### 6) Обновление сервиса

```bash
cd /opt/spark-llm-router
git pull
source .venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart spark-llm-router@<user>
```

Если меняли Jupyter-конфиг или пакет jupyterlab — перезапустите и `jupyter-lab@<user>`.

## Конфигурация моделей

Файл: `config/models.yaml`.

- `base_url` — куда проксировать после запуска.
- `start_cmd` — команда запуска backend.
- `startup_timeout_sec` — сколько ждать прогрева модели.
- `health_path` — endpoint проверки готовности.

## Примеры запросов

### Список моделей

```bash
curl http://127.0.0.1:8080/v1/models
```

### Запуск запроса в конкретную модель

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "qwen3-30b",
    "messages": [{"role":"user", "content":"Привет!"}],
    "temperature": 0.3
  }'
```

## Публикация наружу через Tailscale

1. Узнайте tailscale-IP:
   ```bash
   tailscale ip -4
   ```
2. Разрешите порт `8080` в firewall (если включен).
3. Используйте URL вида `http://<tailscale-ip>:8080` с ваших доверенных устройств.

## Jupyter Lab на DGX Spark

Запуск как сервис:

```bash
python3 -m pip install jupyterlab
jupyter lab --ip=0.0.0.0 --port=8888 --no-browser
```

Рекомендуется:
- включить пароль/токен,
- работать только через tailnet,
- не публиковать в открытый интернет.

## Подключение из VS Code / PyCharm

- **VS Code**: `Jupyter: Specify local or remote Jupyter server` → вставить URL `http://<tailscale-ip>:8888/?token=...`.
- **PyCharm Professional**: Settings → Jupyter → Configure Jupyter Server → Existing → URL сервера.

## Важные эксплуатационные замечания

- Переключение тяжелых моделей может занимать минуты — это нормально.
- Если модель не стартовала за timeout, роутер вернет ошибку 500.
- Для снижения фрагментации памяти лучше держать один backend-процесс на модель и корректно завершать его через SIGTERM/SIGKILL (уже реализовано).

## Как работает оркестратор (пошагово)

1. Клиент отправляет запрос в `POST /v1/chat/completions` с полем `model`.
2. Роутер берет lock (чтобы одновременно не стартовали две модели).
3. Если запрошенная модель уже активна — запрос сразу проксируется в ее backend.
4. Если активна другая модель:
   - старая модель останавливается,
   - стартует новая через `start_cmd`,
   - роутер опрашивает `health_path`, пока модель не станет готовой,
   - после готовности проксирует пользовательский запрос.

Это гарантирует режим «одна тяжелая модель в памяти в конкретный момент времени».

## Что будет, если модель зависнет или упадет

- **Зависла при остановке**: оркестратор отправляет `SIGTERM`, ждет немного, потом отправляет `SIGKILL`.
- **Упала при старте**: health-check не станет успешным до `startup_timeout_sec`, после чего роутер вернет ошибку `500`.
- **Упала во время работы**: следующий запрос к этой модели приведет к повторному запуску модели (через обычный путь переключения).

Практика для прод-стабильности:
- увеличьте `startup_timeout_sec` для самых тяжелых моделей,
- добавьте мониторинг `journalctl -u spark-llm-router@<user>` и логов vLLM,
- держите запас GPU memory utilization (не выставляйте всегда 0.99+).

## Как регулировать размер контекстного окна

Контекст задается в команде запуска backend в `config/models.yaml`, обычно через флаг vLLM:

```bash
--max-model-len 8192
```

Где менять:
- для каждой модели отдельно в поле `start_cmd`;
- чем больше `max-model-len`, тем выше расход VRAM и ниже throughput;
- для 235B 4bit обычно безопаснее начинать с меньшего окна (например 4096) и постепенно поднимать.

После изменения `config/models.yaml` перезапустите сервис роутера.

## Как «прикладывать файлы» в запросах к модели

Текущая версия роутера проксирует OpenAI-совместимый `chat/completions`, поэтому файл как бинарный upload не принимает напрямую.

Рабочие варианты:
- маленькие файлы: прочитать файл на клиенте и отправить текст в `messages`;
- большие файлы: сделать RAG-пайплайн (ингест в векторное хранилище + retrieval + передача релевантных фрагментов в промпт);
- изображения/мультимодальность: нужен backend и API-метод, поддерживающий vision/file inputs.

Если нужно, можно добавить отдельный endpoint в роутер (например `/v1/files/upload`) и промежуточное хранилище, но это отдельная доработка.

## Как модель «ходит в интернет»

В базовой схеме LLM backend (vLLM) **не делает веб-браузинг сам по себе**.

Чтобы дать доступ в интернет, обычно делают tool-calling:
1. Модель решает вызвать инструмент `web_search`/`fetch_url`.
2. Ваше приложение выполняет HTTP-запрос во внешний интернет.
3. Возвращает результат в модель следующим сообщением.

То есть сеть использует не сама модель напрямую, а ваш серверный инструмент-обвязка с контролем доменов, таймаутов, ACL и логированием.
