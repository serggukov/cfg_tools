# Скрипт для выгрузки версий хранилища 1с в GIT
## Сценарий использования ##
1. Создаем [файл настроек](#Файл-настройки)
2. Запускаем `python rup.py init <файл настроек>`
3. Настраиваем [соответствие пользователей и пользователей GIT](#Файл-соответствия-авторов) в файле `Каталог локального репозитория\authors.csv`
3. Запускаем `python rup.py export <файл настроек>`


## Файл настройки
Содержит настройки логирования и настройки выгрузки. Может содрежать несколько секций с настройками выгрузки
```
[LOG]
level = DEBUG|INFO|ERROR
file = %%Y-%%m-%%d.log
[BASE]
store = Путь к файлу хранилища
local_repo = Путь к локальному каталогу репозитория
remote_repo = URL удаленного репозитория
use_pull = True|False
```

###Секция [LOG]:
* level - уровень выводимых сообщений. 
* file - Имя файла лога, если не указан вывод в консоль. Поддерживает формирование имени по дате
  - %%Y - год
  - %%m - месяц
  - %%d - день
  - %%H - час
  - %%M - минуты
  - %%S - секунды
### Секция настройки выгрузки [BASE]:
Может иметь любое имя, кроме LOG
* store - Путь к файлу хранилища 1с. Пример: `c:\store\1cv8ddb.1cd`
* local_repo - Путь к каталогу выгрузки (локальный репозиторий). Пример: `c:\store\repo`
* remote_repo - URL центрального хранилища. Пример: `git@host:namespace\name_repo.git`
* use_pull - True использовать комманду pull перед выгрузкой версий, False - не использовать  

## Файл соответствия авторов
Содержит соответствие пользователей хранилица и пользователей git, адресов электронной почты

Имя файла: Каталог локального репозитория\authors.csv

Кодировка: UTF-8

В формате CSV

`ИмяПользователяХранилища; ИмяПользователяРепозитория; АдресЭлектроннойПочты`

## Файл информация о версии ##
Содержит номер последний выгруженной версии хранилища. При первичной выгрузке либо содержит 0, либо не существует

Имя файла: Каталог локального репозитория\last_version.txt