# Телеграм-бот для мониторинга трейдинговых сервисов

Проверяет состояние всяких трейдинговых штук.
Присылает алерты, отвечает на запросы про статус, запускает команды IBC.


## Зависимости

Нужен Python >= 3.10


## Конфигурация

Пример конфига лежит в [config.yaml.example](./config/config.yaml.example).
Через параметры можно задать свой файл.


## Установка
```
virtualenv -p python3 .venv
source .venv/bin/activate
pip install -r requirements.txt
```


## Запуск
```
cd src
python antifreeze.py
```


## Параметры

--config - путь до файла конфигурации, ../config/default.yaml

