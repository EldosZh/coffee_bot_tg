import json

OPTIONS = {
    'size': {'S': ('S', 0), 'M': ('M (+200 ₸)', 200), 'L': ('L (+400 ₸)', 400)},
    'temperature': {'hot': ('🔥 Горячий', 0), 'cold': ('🧊 Холодный', 0)},
    'milk': {'regular': ('🥛 Обычное', 0), 'oat': ('Овсяное (+300 ₸)', 300),
             'almond': ('Миндальное (+300 ₸)', 300), 'coconut': ('Кокосовое (+300 ₸)', 300)},
    'syrup': {'none': ('Без сиропа', 0), 'vanilla': ('Ваниль (+150 ₸)', 150),
              'caramel': ('Карамель (+150 ₸)', 150), 'hazelnut': ('Лесной орех (+150 ₸)', 150)},
    'extra': {'no': ('Без доп. эспрессо', 0), 'yes': ('Доп. эспрессо (+300 ₸)', 300)},
}
LABELS = {'size': 'Размер', 'temperature': 'Температура', 'milk': 'Молоко',
          'syrup': 'Сироп', 'extra': 'Доп. эспрессо'}


def normalize(options: dict, drink: bool) -> str:
    if not drink:
        if options:
            raise ValueError('У еды нет параметров напитка.')
        return '{}'
    if set(options) != set(OPTIONS) or any(options[k] not in OPTIONS[k] for k in OPTIONS):
        raise ValueError('Выберите все параметры напитка заново.')
    return json.dumps(options, sort_keys=True)


def price(base: int, options: dict) -> int:
    return base + sum(OPTIONS[key][value][1] for key, value in options.items())


def describe(raw: str) -> str:
    options = json.loads(raw)
    return '\n'.join(f'{LABELS[k]}: {OPTIONS[k][v][0]}' for k, v in options.items())


def money(value: int) -> str:
    return f'{value:,}'.replace(',', ' ') + ' ₸'
