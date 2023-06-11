import json

CONFIG = None
DATA = None

with open('config.json', 'r') as config:
    CONFIG = json.load(config)

with open('data.json', 'r') as data:
    DATA = json.load(data)

def save_config():
    with open('config.json', 'w') as config_file:
        json.dump(CONFIG, config_file)

def save_data():
    with open('data.json', 'w') as data_file:
        json.dump(DATA, data_file)

