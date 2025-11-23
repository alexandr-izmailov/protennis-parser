# Быстрое развертывание на VPS

## Требования
- VPS с Ubuntu/Debian и root правами
- Docker и Docker Compose установлены

## Установка Docker (если нет)

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sh get-docker.sh
# Docker Compose v2 уже включен в Docker Desktop и новые версии Docker Engine
# Если нужна отдельная установка:
apt-get update && apt-get install -y docker-compose-plugin
# Или используйте: docker compose (без дефиса) вместо docker-compose
```

## Развертывание

1. **Загрузите проект на сервер:**
```bash
# Через git
git clone <ваш-репозиторий> protennis-bot
cd protennis-bot

# Или через scp с локальной машины
scp -r "путь/к/проекту" root@your-server:/root/protennis-bot
ssh root@your-server
cd protennis-bot
```

2. **Запустите бота:**
```bash
docker-compose up -d
# Или если используется Docker Compose v2:
# docker compose up -d
```

3. **Проверьте статус:**
```bash
docker-compose ps
docker-compose logs -f
```

## Управление

- **Просмотр логов:** `docker-compose logs -f` (или `docker compose logs -f`)
- **Остановка:** `docker-compose down` (или `docker compose down`)
- **Перезапуск:** `docker-compose restart` (или `docker compose restart`)
- **Обновление кода:** 
  ```bash
  git pull  # или загрузите новые файлы
  docker-compose up -d --build
  ```

## Автоперезапуск

Docker Compose автоматически перезапускает контейнер при:
- Падении процесса
- Перезагрузке сервера (если Docker настроен на автозапуск)
- Ошибках

Для автозапуска Docker при загрузке системы:
```bash
systemctl enable docker
```

Готово! Бот работает и автоматически перезапускается при сбоях.

