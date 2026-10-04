#!/bin/bash

# Скрипт для остановки Telegram бота

BOT_NAME="telegram-chat-analyzer"

echo "🛑 Остановка Telegram Chat Analyzer..."

# Проверка, запущен ли бот
if ! screen -list | grep -q "$BOT_NAME"; then
    echo "⚠️  Бот не запущен!"
    exit 1
fi

# Остановка бота
screen -S "$BOT_NAME" -X quit

echo "✅ Бот остановлен!"

